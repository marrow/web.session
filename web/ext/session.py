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
	
	__slots__ = ('engines', 'uses', 'needs', 'provides', '_cookie')
	
	provides = {'session'}
	needs = {'request'}
	
	def __init__(self, default=None, expires=None, cookie=None, **engines):
		"""Configure settings and setup slots for the extension
		
		Current settings consist of the following:
		* `engines` -- either `None`, which will setup a default `MemorySessionEngine`, or a `dict` of
		 	session engines. This setting is used to tell the `SessionExtension` which session engines to use. This
			setting should contain at least one entry with the key `default`
		* `cookie' -- either `None` or a `dict`. This is used to tell the `SessionExtension` which settings to use
			for the browser cookie. possible options are 'name' - `str`, 'max_age' - `int`, 'http_only' - `True` or
			`False`, and `str` 'path'
		"""
		
		if expires and hasattr(expires, 'isdigit'):
			expires = timedelta(days=expires)
		
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
		context.db = context.db._promote('DBGroup')
		context.db['_ctx'] = context
		self._handle_event('prepare', context)
		if __debug__:
			log.debug("Preparing session group")
		
		# Must promote the ContextGroup so that the lazy wrapped function calls operate properly
		context.session = context.session._promote('SessionGroup')
		
		# Give lazy wrapped functions a way to access the RequestContext
		context.session['_ctx'] = context
		
		self._handle_event('prepare', context=context)
	
	def after(self, context):
		"""Determine if the session cookie needs to be set"""
		
		# if the session was accessed at all during this request
		if '_id' not in context.session.__dict__:
			return
		
		# engines could have made a new storage even if the id is old
		self._handle_event('after', context=context)
		
		# if the session id has just been generated this request, we need to set the cookie
		if '_new' not in context.session.__dict__:
			return
		
		# see WebOb request / response
		context.response.set_cookie(value=context.session._id, **self._cookie)
	
	def _handle_event(self, event, *args, **kw):
		"""Send a signal event to all session engines
		
		* `event` -- the signal to run on all session engines
		* `event` -- the RequestContext for the current request
		* `*args` -- additional args passed on to session engine callbacks
		* `**kwargs` -- additional kwargs passed on to session engine callbacks, if it contains `overrides` and that
			value is True, events will be run on session engines regardless of whether they have been accessed during
			this request or not, otherwise only engines that have been accessed during this request will have events
			run. If there is no kwarg for context, all engines will have the event run regardless
		"""
		override = kw.get('overrides', False)
		context = kw.get('context', None)
		
		# In a typical scenario these callbacks will only happen if the specific session engine was accessed
		for engine in self.engines.values():
			if engine is not None and hasattr(engine, event):
				if override or context is None or engine.__name__ in context.session.__dict__:
					getattr(engine, event)(*args, **kw)
	
	def __getattr__(self, name):
		"""Pass any signals SessionExtension doesn't use on to SessionEngines"""
		
		# Only allow signals defined in `web.ext.extensions.WebExtensions`
		if name not in ('stop', 'graceful', 'dispatch', 'before', 'done', 'interactive', 'inspect'):
			raise AttributeError()
		
		return partial(self._handle_event, name)
