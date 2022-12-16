import sys
import warnings

from binascii import hexlify
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import compare_digest, new as hmac
from os import getenv as env, urandom

from web.core import Application
from web.db.mongo import MongoDBConnection
from web.ext.annotation import AnnotationExtension
from web.ext.db import DatabaseExtension
from web.ext.debug import DebugExtension
from web.ext.local import ThreadLocalExtension
from web.ext.serialize import SerializationExtension
from web.ext.session import SessionExtension
from web.session.mongo import MongoSessionStorage, MongoSession
from webob.cookies import CookieProfile

from marrow.mongo.field import Integer
from marrow.mongo.trait import Expires, Identified, Queryable


MAX_AGE = timedelta(days=2)

COOKIE = CookieProfile(
		'SID',
		httponly = True,
		secure = not __debug__,  # Production environments demand HTTPS.
		samesite = 'lax' if __debug__ else 'none',
		# max_age = MAX_AGE,  # Default to prior "session cookie" practice.
	)


class Session(MongoSessionStorage, Queryable):
	"""A derivative of the base model provided by Marrow Mongo for use as a persisted user session.
	
	Ref: https://github.com/marrow/mongo/blob/develop/web/session/mongo.py?ts=4
	"""
	
	__collection__ = 'Sessions'
	
	id = Queryable.id.adapt(assign=True)
	visits = Integer(default=0, assign=True)
	
	# Session Metadata
	expires = Expires.expires.adapt(default=lambda: datetime.utcnow() + MAX_AGE, assign=True)
	
	# Methods
	
	def save(self):  # Persist the whole session object.
		"""Persist this session.
		
		This is theoretically not as fast as atomic updates, which should be used in preference to this if the value
		is not required within the same request. (Or you can hypothetically update both, if careful to update the
		underlying `__data__` backing store, and not through attribute manipulation.)
		"""
		
		if hasattr(sys, 'session'): return  # Persistence is automatic for the lifetime of the process.
		
		self.expires = datetime.utcnow() + MAX_AGE  # Refresh the expiry time.
		
		cls, collection, query, options = self._prepare_find(Session.id == self)
		
		result = collection.replace_one(query, self, True)  # Upsert to create if missing.
		if not result.raw_result['n']:
			log.error("Unable to create or update session: " + unicode(Session.id))
		
		return result
	
	def invalidate(self, *args, **kw):
		"""Clear the current session state.
		
		Potentially utilized by testing, not currently utilized in-application.
		"""
		
		if hasattr(sys, 'session'): sys.session = Session()
	
	def load(self):
		self.reload()
	
	revert = load


DB_URI = env('MONGODB_ADDON_URI', 'mongodb://localhost/test')


class Root:
	def __init__(self, context):
		self._ctx = context
	
	def __call__(self):
		context = self._ctx
		context.session.visits += 1
		
		return f"Session {context.session._id} visited {context.session.visits} times; expires: {context.session.expires}"
	
	def die(self):
		"""Trigger a diagnostic REPL shell."""
		1/0


app = Application(Root, extensions=[  # WSGI application instance, extensions that are always enabled:
		AnnotationExtension(),  # Allows us to use Python 3 function annotations as typecasting hints.
		DatabaseExtension(MongoDBConnection(DB_URI)), # Ensure our default database connection is attached.
		SessionExtension(env('SECRET', ''), MongoSession(Session), auto=True, expires=MAX_AGE) #, cookie=COOKIE)
	] + ([  # Extensions that are only enabled in development or testing environments:
		DebugExtension(),  # Interactive traceback debugger, but gives remote code execution access.
		ThreadLocalExtension(),  # Permit old-style "superglobal" use: web.core:request, et. al. Diagnostic.
	] if __debug__ else []), logging = {
				'version': 1,
				'handlers': {
						'console': {
								'class': 'logging.StreamHandler',
								'formatter': 'json',
								'level': 'DEBUG' if __debug__ else 'INFO',
								'stream': 'ext://sys.stdout',
							},
					},
				'loggers': {
						'web': {
								'level': 'INFO' if __debug__ else 'WARN',
								'handlers': ['console'], #, 'db'],
								'propagate': False,
							},
					},
				'root': {
						'level': 'INFO' if __debug__ else 'WARN',
						'handlers': ['console'], #, 'db'],
					},
				'formatters': {
						'json': {'()': 'marrow.mongo.util.logger.JSONFormatter'},
					}
			})

if __name__ == '__main__':
	app.serve('wsgiref')

