# encoding: utf-8

from __future__ import unicode_literals

from hashlib import md5, sha256
from hmac import new as hmac
from binascii import unhexlify
from os import getpid
from socket import gethostname
from time import time
from random import randint
from threading import RLock

try:
	from hmac import compare_digest
except ImportError:
	def compare_digest(a, b):
		return a is b

try:
	str = unicode
except NameError:
	py3 = True
else:
	py3 = False


MACHINE = int(md5(gethostname().encode() if py3 else gethostname()).hexdigest()[:6], 16)



class SignatureError(ValueError):
	pass


class Counter(object):
	def __init__(self):
		self.value = randint(0, 2**24)
		self.lock = RLock()
	
	def __iter__(self):
		return self
	
	def __next__(self):
		with self.lock:
			self.value = (self.value + 1) % 0xFFFFFF
			value = self.value
		
		return value
	
	next = __next__

counter = Counter()


class SessionIdentifier(object):
	def __init__(self, value=None):
		if value:
			self.parse(value)
		else:
			self.generate()
	
	def parse(self, value):
		if not isinstance(value, str):
			value = value.decode('ascii')
		
		self.time = int(value[:8], 16)
		self.machine = int(value[8:14], 16)
		self.process = int(value[14:18], 16)
		self.counter = int(value[18:24], 16)
	
	def generate(self):
		self.time = int(time())
		self.machine = MACHINE
		self.process = getpid() % 0xFFFF
		self.counter = next(counter)
	
	def __str__(self):
		return "{self.time:08x}{self.machine:06x}{self.process:04x}{self.counter:06x}".format(self=self)
	
	def __repr__(self):
		return "{self.__class__.__name__}('{self}')".format(self=self)
	
	if not py3:
		__unicode__ = __str__
		
		def __str__(self):
			return self.__unicode__().encode('ascii')


class SignedSessionIdentifier(SessionIdentifier):
	__slots__ = ('__secret', '__signature', 'expires')
	def __init__(self, value=None, secret=None, expires=None):
		self.__secret = secret.encode('ascii') if hasattr(secret, 'encode') else secret
		self.__signature = None
		self.expires = expires
		
		super(SignedSessionIdentifier, self).__init__(value)
	
	def parse(self, value):
		if len(value) != 88:
			raise SignatureError("Invalid signed identifier length.")
		
		super(SignedSessionIdentifier, self).parse(value)
		
		if self.expires and (time() - self.time) > self.expires:
			raise SignatureError("Expired signed identifier.")
		
		self._signature = value[24:]
		
		if not self.valid:
			raise SignatureError("Invalid signed identifier.")
	
	@property
	def signed(self):
		identifier = super(SignedSessionIdentifier, self).__str__()
		identifier = identifier + self.signature
		
		return identifier
	
	@property
	def signature(self):
		if not self.__signature:
			self.__signature = hmac(
					self.__secret,
					unhexlify(str(self).encode('ascii')),
					sha256
				).hexdigest()
		
		return self.__signature
	
	@property
	def valid(self):
		if not self._signature:
			return False
		
		if self.expires and (time() - self.time) > self.expires:
			return False
		
		challenge = hmac(
				self.__secret,
				unhexlify(str(self).encode('ascii')),
				sha256
			).hexdigest()
		
		return compare_digest(challenge, self.signature)

