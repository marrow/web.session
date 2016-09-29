# encoding: utf-8

from __future__ import unicode_literals

from datetime import timedelta

from web.ext.session import SessionExtension


class TestSessionExtension(object):
	def test_construction_defaults(self):
		se = SessionExtension()
		assert not se.refresh
		assert se.cookie == {'name': 'session', 'httponly': True, 'path': '/'}
		assert 'default' in se.engines
		assert se.engines['default'].__class__.__name__ == 'MemorySession'
	
	def test_construction_expires(self):
		se = SessionExtension(expires=24)
		assert se.expires == 24 * 60 * 60
		
		se = SessionExtension(expires=timedelta(days=2))
		assert se.expires == 2 * 24 * 60 * 60

