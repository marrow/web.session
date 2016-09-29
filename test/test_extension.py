# encoding: utf-8

from __future__ import unicode_literals

from web.ext.session import SessionExtension


class TestSessionExtension(object):
	def test_construction_defaults(self):
		SessionExtension()

