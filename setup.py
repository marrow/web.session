#!/usr/bin/env python3

from setuptools import setup
from sys import argv, version_info as python_version
from pathlib import Path


if python_version < (3, 6):
	raise SystemExit("Python 3.6 or later is required.")

here = Path(__file__).resolve().parent
version = description = url = author = author_email = ""  # Silence linter warnings.
exec((here / "web" / "session" / "release.py").read_text('utf-8'))  # Actually populate those values.

tests_require = [
		'pytest',  # test collector and extensible runner
		'pytest-cov',  # coverage reporting
		'pytest-flakes',  # syntax validation
		'web.dispatch.object',  # test endpoint routing
	]


setup(
	name = "web.session",
	version = version,
	
	description = description,
	long_description = (here / 'README.rst').read_text('utf-8'),
	url = url,
	download_url = 'https://github.com/marrow/web.session/releases',
	
	author = author.name,
	author_email = author.email,
	
	license = 'MIT',
	keywords = [
			'marrow',
			'web.ext',
			'web.session',
			'sessions',
		],
	classifiers = [
			"Development Status :: 5 - Production/Stable",
			"Environment :: Console",
			"Environment :: Web Environment",
			"Intended Audience :: Developers",
			"License :: OSI Approved :: MIT License",
			"Operating System :: OS Independent",
			"Programming Language :: Python",
			"Programming Language :: Python :: 3",
			"Programming Language :: Python :: 3.6",
			"Programming Language :: Python :: 3.7",
			"Programming Language :: Python :: 3.8",
			"Programming Language :: Python :: Implementation :: CPython",
			"Programming Language :: Python :: Implementation :: PyPy",
			"Topic :: Software Development :: Libraries",
			"Topic :: Software Development :: Libraries :: Python Modules",
		],
	
	# ### Code Discovery
	
	packages = ('web.ext', 'web.session'),
	include_package_data = True,
	package_data = {'': ['README.rst', 'LICENSE.txt']},
	
	# ### Plugin Registration
	
	entry_points = {
			# #### Re-usable applications or application components.
			'web.app': [
					# 'session = web.app.session:SessionCollection',
				],
			
			# #### WebCore Extensions
			'web.extension': [
					'session = web.ext.session:SessionExtension',
				],
			
			# #### WebCore Extensions
			'web.session': [
					'memory = web.session.memory:MemorySession',
					'disk = web.session.disk:DiskSession',
				],
		},
	
	# ## Installation Dependencies
	
	setup_requires = [
			'pytest-runner',
		] if {'pytest', 'test', 'ptr'}.intersection(sys.argv) else [],
	
	install_requires = [
			'marrow.package>=2.0.0,<3.0.0',  # Plugin discovery and loading.
			'WebCore>=3.0,<4.0',  # web framework version pinning
		],
	
	extras_require = {
			'development': tests_require,  # An extended set of useful development tools.
		},
	
	tests_require = tests_require,
)
