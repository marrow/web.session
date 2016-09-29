# encoding: utf-8

"""Session handling extension utilizing pluggable session data storage engines."""

from __future__ import unicode_literals

from os import urandom
from binascii import hexlify
from datetime import timedelta
from functools import partial

from web.core.util import lazy
from web.core.context import ContextGroup
from web.session.memory import MemorySession
from web.session.util import SignedSessionIdentifier


log = __import__('logging').getLogger(__name__)


class SessionExtension(object):
	"""Client session management extension.
	
	This extension provides a modular approach to managing sessions, with the concept of a "default" engine handling
	most requests, and optional additional engines accessible by their prefix. It populates (lazily) a
	`context.session` object whose attributes are (lazily) loaded on access, and persists .
	"""
	
	provides = {'session'}
	needs = {'request'}
	
	def __init__(self, secret=None, default=None, expires=None, cookie=None, refresh=True, **engines):
		"""Configure session management extension and prepare engines.
		
		The first positional argument is `secret`, the application-secret value used as the cryptographic basis for
		cookie validation. Make sure to set this to a reasonably large random value in production environments. In
		development, if not provided, a different pseudo-random value will be generated on each start.
		
		The next positional argument, `default`, represents the target of otherwise unknown attribute access to the
		`context.session` object. If one is not given, a `MemorySession` instance will be utilized.
		
		An optional `expires` time may be given (either a `timedelta` object or an integer representing a number of
		hours) to indicate the lifetime of abandoned sessions; this will be used as the default cookie `max_age` if
		set.
		
		Cookie settings, to be passed through to the `context.response.set_cookie` WebOb helper, may be passed as a
		dictionary or dictionary-alike named `cookie`.
		
		If `refresh` is truthy, the cookie will be refreshed to have an updated expiry time on each access. Set this
		value falsy if you want your sessions to have a fixed lifespan from initial creation, otherwise it will
		expire only after it has been abandoned for that duration.
		
		Additional keyword arguments are used as session engines assigned as lazily loaded attributes of the
		`context.session` object. Individual engines may have their own expiry controls in addition to the global
		setting made here. (There is never a point in setting a specific engine's expiry time to be longer than the
		global.)
		"""
		
		if not secret:
			if not __debug__:
				raise ValueError("A secret must be defined in production environments.")
			
			log.warn("Generating temporary session secret; sessions will not persist between restarts.")
			secret = hexlify(urandom(32)).decode('ascii')
		
		if expires and (hasattr(expires, 'isdigit') or isinstance(expires, (int, float))):
			expires = timedelta(hours=int(expires))
		
		self._refresh = refresh
		self._expires = expires
		self.__secret = secret
		
		engines['default'] = default if default else MemorySession()
		self.engines = engines
		self._cookie = cookie = cookie if cookie else dict()
		
		cookie.setdefault('name', 'session')
		cookie.setdefault('httponly', True)
		cookie.setdefault('path', '/')
		
		if expires:
			cookie.setdefault('max_age', 
					expires.days * 24 * 60 * 60 + \
					expires.seconds)
		
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
		"""Lazily get the session id for the current request.
		
		The `session` passed to this function is the bound SessionGroup instance containing the lazy engines.
		"""
		# TODO: check if any session engines have this key, if not generate a new one
		# otherwise use this key
		
		cookies = session._ctx.request.cookies
		identifier = None
		token = cookies.get(self._cookie['name'], None)
		
		if isinstance(token, bytes):
			token = token.decode('ascii')
		
		if token:
			try:
				if self._expires:
					expires = self._expires.days * 24 * 60 * 60 + \
							self.expires.hours * 60 * 60 + \
							self.expires.minutes * 60 + \
							self.expires.seconds
					identifier = SignedSessionIdentifier(token, secret=self.__secret, expires=expires)
				else:
					identifier = SignedSessionIdentifier(token, secret=self.__secret)
			
			except ValueError:
				log.warn("Signature failed to validate.", extra=dict(request=id(session._ctx)))
			
			else:
				if __debug__:
					log.debug("Retreived valid session token from cookie.", extra=dict(
							request=id(session._ctx), identifier=identifier))
		
		if not identifier:
			# TODO: if token: notify to nuke old session
			identifier = SignedSessionIdentifier(secret=self.__secret)
			session['_new'] = True
			
			if __debug__:
				log.debug("No existing session identifier; generated new.", extra=dict(
					request=id(session._ctx), session=identifier))
		
		return identifier
	
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
		self._handle_event(True, 'start', context=context)
	
	def prepare(self, context):
		"""Called to prepare attributes on the RequestContext.
		
		We additionally promote our DBGroup of extensions here and "bind" the group to this request.
		"""
		
		if __debug__:
			log.debug("Preparing session group.", extra=dict(request=id(context)))
		
		context.session = context.session._promote('SessionGroup')  # Allow the lazy descriptor to run from the class.
		context.session['_ctx'] = context  # Bind this promoted SessionGroup to the current context.
		
		self._handle_event(True, 'prepare', context)
	
	def after(self, context):
		"""Called after the view has prepared a response, prior to details being sent to the client.
		
		Determine if the session cookie needs to be set, if so, set it.
		"""
		
		# engines could have made a new storage even if the id is old
		self._handle_event(True, 'after', context)
		
		# if the session was accessed at all during this request
		if '_id' not in context.session.__dict__:
			return
		
		# if the session id has just been generated this request, we need to set the cookie
		if not self._refresh and '_new' not in context.session.__dict__:
			return
		
		# see WebOb request / response
		context.response.set_cookie(value=str(context.session._id), **self._cookie)
	
	def done(self, context):
		"""Called after the response has been fully sent to the client.
		
		This helps us defer the overhead of writing session data out until after the client is already served.
		"""
		
		self._handle_event(True, 'done', context)
		
		if '_id' not in context.session.__dict__:
			return  # Bail early if the session was never accessed.
		
		# Inform session engines that had their data touched to persist any changes.
		for ext in set(context.session.__dict__) & set(self.engines):
			self.engines[ext].persist(context, context.session._id, context.session[ext])
	
	def _handle_event(self, all, event, context, *args, **kw):
		"""Send a signal to all, or only accessed session engines.
		
		The required positional argument `all` controls if the message is broadcast to all available engines or only
		those that have been accessed during the current request.
		
		The `event` argument is the string name of the method ("event") to call, if present on the engine.
		
		A WebCore context must also be passed in as `context`. Any additional arguments (positional or keyword) will
		be passed through to the callbacks themselves.
		"""
		
		# Determine the set of engines we're sending signals to.
		engines = self.engines.items() if all else {i: self.engines[i] for i \
				in set(context.session.__dict__) & set(self.engines)}
		
		# Call the event callback, if present in the engine.
		for name, engine in engines:
			if not hasattr(engine, event): continue
			getattr(engine, event)(context, *args, **kw)
	
	def __getattr__(self, name):
		"""Pass any signals SessionExtension doesn't use on to SessionEngines"""
		
		# Only allow signals defined in `web.ext.extensions.WebExtensions`
		if name not in ('stop', 'graceful', 'dispatch', 'before', 'interactive', 'inspect'):
			raise AttributeError()
		
		return partial(self._handle_event, True, name)
