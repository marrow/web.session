# encoding: utf-8

"""Session handling extension utilizing pluggable session data storage engines."""

from datetime import timedelta
import os, base64
from functools import partial

from web.core.util import lazy
from web.core.context import ContextGroup
from web.session.memory import MemorySession


log = __import__('logging').getLogger(__name__)



# Probably want something more secure than this, but I didn't want to add any dependencies
def generate_session_id(num_bytes=24):
	"""Generates random string which is then base64 encoded
	
	* `num_bytes` -- the number of bytes this random string should consist of
	
	TODO: Replace with itsdangerous use.
	"""
	
	return str(base64.b64encode(os.urandom(num_bytes)).decode('utf-8'))


class SessionExtension(object):
	"""Client session management extension.
	
	This extension provides a modular approach to managing sessions, with the concept of a "default" engine handling
	most requests, and optional additional engines accessible by their prefix. It populates (lazily) a
	`context.session` object whose attributes are (lazily) loaded on access, and persists .
	"""
	
	provides = {'session'}
	needs = {'request'}
	
	def __init__(self, default=None, expires=None, cookie=None, **engines):
		"""Configure session management extension and prepare engines.
		
		The first positional argument, `default`, represents the target of otherwise unknown attribute access to the
		`context.session` object. If one is not given, a `MemorySession` instance will be utilized.
		
		An optional `expires` time may be given (either a `timedelta` object or an integer representing a number of
		hours) to indicate the lifetime of abandoned sessions; this will be used as the default cookie `max_age` if
		set.
		
		Cookie settings, to be passed through to the `context.response.set_cookie` WebOb helper, may be passed as a
		dictionary or dictionary-alike named `cookie`.
		
		Additional keyword arguments are used as session engines assigned as lazily loaded attributes of the
		`context.session` object. Individual engines may have their own expiry controls in addition to the global
		setting made here. (There is never a point in setting a specific engine's expiry time to be longer than the
		global.)
		"""
		
		if expires and (hasattr(expires, 'isdigit') or isinstance(expires, (int, float))):
			expires = timedelta(hours=int(expires))
		
		engines['default'] = default if default else MemorySession()
		self.engines = engines
		self._cookie = cookie = cookie if cookie else dict()
		
		cookie.setdefault('name', 'session')
		cookie.setdefault('http_only', True)
		cookie.setdefault('path', '/')
		if expires:
			cookie.setdefault('max_age', expires.days * 24 * 60 * 60 + expires.hours * 60 * 60 + expires.minutes * 60 + expires.seconds)
		
		self.uses = set()
		self.needs = set(self.needs)
		self.provides = set(self.provides)
		
		# Gather all the dependency information from Session Engines
		for name, engine in self.engines.items():
			if engine is None: continue  # Handle no-default case.
			engine.__name__ = name  # Inform the engine what its name is.
			self.uses.update(getattr(engine, 'uses', ()))
			self.needs.update(getattr(engine, 'needs', ()))
			self.provides.update(getattr(engine, 'provides', ()))
	
	def get_session_id(self, session):
		"""Lazily get the session id for the current request
		
		* `session_group` -- the `SessionGroup` that contains the session engines
		"""
		
		cookies = session._ctx.request.cookies
		
		# Check if the browser sent a session cookie
		if self._cookie['name'] in cookies:
			id = cookies[self._cookie['name']]
			if __debug__:
				log.debug("Retreived cookie session id: "+str(id))
			# TODO: check if any session engines have this key, if not generate a new one
			# otherwise use this key
		else:
			id = generate_session_id()
			if __debug__:
				log.debug("Generated new session key: "+str(id))
			session['_new'] = True
		
		return id
	
	def start(self, context):
		"""Called to prepare attributes on the ApplicationContext."""
		
		# Construct lazy bindings for each configured session extension.
		context.session = ContextGroup(**{
				name: lazy(lambda s: engine.load(s._ctx, s._id), name) \
				for name, engine in self.engines.items()
			})
		
		# Also lazily construct the session ID on first request.
		context.session['_id'] = lazy(self.get_session_id, '_id')
		
		# Notify the engines.
		self._handle_event('start', context=context, overrides=True)
	
	def prepare(self, context):
		"""Called to prepare attributes on the RequestContext.
		
		We additionally promote our DBGroup of extensions here and "bind" the group to this request.
		"""
		
		if __debug__:
			log.debug("Preparing session group.", extra=dict(request=id(context)))
		
		context.session = context.session._promote('SessionGroup')  # Allow the lazy descriptor to run from the class.
		context.session['_ctx'] = context  # Bind this promoted SessionGroup to the current context.
		
		self._handle_event('prepare', context, overrides=True)
	
	def after(self, context):
		"""Called after the view has prepared a response, prior to details being sent to the client.
		
		Determine if the session cookie needs to be set, if so, set it.
		"""
		
		# if the session was accessed at all during this request
		if '_id' not in context.session.__dict__:
			return
		
		# engines could have made a new storage even if the id is old
		self._handle_event('after', context, overrides=True)
		
		# if the session id has just been generated this request, we need to set the cookie
		if '_new' not in context.session.__dict__:
			return
		
		# see WebOb request / response
		context.response.set_cookie(value=context.session._id, **self._cookie)
	
	def done(self, context):
		"""Called after the response has been fully sent to the client.
		
		This helps us defer the overhead of writing session data out until after the client is already served.
		"""
		
		self._handle_event('done', context, overrides=True)
		
		if '_id' not in context.session.__dict__:
			return  # Bail early if the session was never accessed.
		
		# Inform session engines that had their data touched to persist any changes.
		for ext in set(context.session.__dict__) & set(self.engines):
			self.engines[ext].persist(context, context.session._id, context.session[ext])
	
	def _handle_event(self, event, context, *args, **kw):
		"""Send a signal event to all session engines
		
		* `event` -- the signal to run on all session engines
		* `event` -- the RequestContext for the current request
		* `*args` -- additional args passed on to session engine callbacks
		* `**kwargs` -- additional kwargs passed on to session engine callbacks, if it contains `overrides` and that
			value is True, events will be run on session engines regardless of whether they have been accessed during
			this request or not, otherwise only engines that have been accessed during this request will have events
			run. If there is no kwarg for context, all engines will have the event run regardless
		"""
		override = kw.pop('overrides', False)
		
		# In a typical scenario these callbacks will only happen if the specific session engine was accessed
		for engine in self.engines.values():
			if engine is not None and hasattr(engine, event):
				if override or context is None or engine.__name__ in context.session.__dict__:
					getattr(engine, event)(context, *args, **kw)
	
	def __getattr__(self, name):
		"""Pass any signals SessionExtension doesn't use on to SessionEngines"""
		
		# Only allow signals defined in `web.ext.extensions.WebExtensions`
		if name not in ('stop', 'graceful', 'dispatch', 'before', 'done', 'interactive', 'inspect'):
			raise AttributeError()
		
		return partial(self._handle_event, name)
