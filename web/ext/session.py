# encoding: utf-8

"""Session handling extension using session engines."""

from web.core.util import lazy
from web.core.context import Context, ContextGroup
from web.session.memory import MemorySession

log = __import__('logging').getLogger(__name__)


import os, base64
from functools import partial

# Probably want something more secure than this, but I didn't want to add any dependencies
def generate_session_id(num_bytes=24):
	"""Generates random string which is then base64 encoded

	* `num_bytes` -- the number of bytes this random string should consist of
	"""

	return str(base64.b64encode(os.urandom(num_bytes)).decode('utf-8'))


class SessionExtension(object):
	"""Manage client sessions using session engines
	
	This extension stores session data in session session engines and handles the session cookie
	"""
	
	__slots__ = ('engines', 'uses', 'needs', 'provides', '_cookie')
	
	_provides = {'session'}
	_needs = {'request'}
	
	def __init__(self, **config):
		"""Configure settings and setup slots for the extension
	
		Current settings consist of the following:
		* `engines` -- either `None`, which will setup a default `MemorySessionEngine`, or a `dict` of
			session engines. This setting is used to tell the `SessionExtension` which session engines to use. This
			setting should contain at least one entry with the key `default`
		* `cookie' -- either `None` or a `dict`. This is used to tell the `SessionExtension` which settings to use
			for the browser cookie. possible options are 'name' - `str`, 'max_age' - `int`, 'http_only' - `True` or
			`False`, and `str` 'path'
		"""
		
		conf = self._configure(**config)
		self.engines = conf['engines']
		self._cookie = conf['cookie']
		
		self.uses = set()
		self.needs = set(self._needs)
		self.provides = set(self._provides)
		
		# Gather all the dependency information from Session Engines
		for name, engine in self.engines.items():
			if engine is None: continue
			engine.__name__ = name  # Inform the engine what its name is.
			self.uses.update(getattr(engine, 'uses', ()))
			self.needs.update(getattr(engine, 'needs', ()))
			self.provides.update(getattr(engine, 'provides', ()))
	
	def _configure(self, **config):
		"""Parses `**kwargs` from `__init__` into valid settings for use by the extension"""
		
		config = config or dict()
		
		# TODO: Pass config expire time to default if we create it
		if 'engines' not in config: config['engines'] = {'default': MemorySession()}
		
		# TODO: Check that there is a default
		
		if 'cookie' not in config: config['cookie'] = {}
		
		# Handle cookie defaults
		cookie = config['cookie']
		if 'name' not in cookie:
			cookie['name'] = 'user_session'
		
		if 'max_age' not in cookie:
			cookie['max_age'] = 360
		
		if 'http_only' not in cookie:
			cookie['http_only'] = True
		
		if 'path' not in cookie:
			cookie['path'] = '/'
		
		#Probably want a way to have any params beyond those listed above go into **kwargs on response.set_cookie
		return config
	
	def get_session_id(self, session_group):
		"""Lazily get the session id for the current request

		* `session_group` -- the `SessionGroup` that contains the session engines
		"""
		
		# Use session_group._ctx in order to access the RequestContext
		context = session_group._ctx
		
		# Check if the browser sent a session cookie
		if(self._cookie['name'] in context.request.cookies):
			id = context.request.cookies[self._cookie['name']]
			if __debug__:
				log.debug("Retreived cookie session id: "+str(id))
			# TODO: check if any session engines have this key, if not generate a new one
			# otherwise use this key
		else:
			id = generate_session_id()
			if __debug__:
				log.debug("Generated new session key: "+str(id))
			session_group['_new'] = True
		
		return id
	
	def start(self, context):
		"""Setup context attributes that will be used on RequestContext"""
		
		context.session = ContextGroup(**{name: lazy(value.get_session, name) for name, value in self.engines.items()})
		context.session['_id'] = lazy(self.get_session_id, '_id')
		
		self._handle_event('start', context=context, overrides=True)
	
	def prepare(self, context):
		"""Set the _ctx attribute for access in lazy functions and promote the ContextGroup"""
		if __debug__:
			log.debug("Preparing session group")
		
		# Give lazy wrapped functions a way to access the RequestContext
		context.session['_ctx'] = context
		# Must promote the ContextGroup so that the lazy wrapped function calls operate properly
		context.session = context.session._promote('SessionGroup')
		
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
		context.response.set_cookie(
				httponly = self._cookie['http_only'],
				max_age = self._cookie['max_age'],
				name = self._cookie['name'],
				path = self._cookie['path'],
				value = context.session._id,
			)
	
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
