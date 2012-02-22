#!/usr/bin/env python

from setuptools import setup, find_packages

setup(name='phoenix',
      version='2.0.0-rc2',
      description='Phoenix Bitcoin Miner',
      author='CFSworks & jedi95',
      url='http://github.com/phoenix2',
      packages=find_packages(),
      package_data={'phoenix2': ['kernels/*/*.py',
                                 'kernels/*/*.cl',
                                 'www/TODO'
                                ]},
      entry_points={
          'console_scripts': [
              'phoenix = phoenix2:main'
          ]
      }
     )
