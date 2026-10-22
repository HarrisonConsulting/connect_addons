# -*- coding: utf-8 -*-
"""The voiceml pin: manifest, requirements.txt and the installed SDK agree.

0.9.5 is the true floor: connect.voicetel.number pushes status_callback
through IncomingPhoneNumbers.update, which 0.9.3 does not accept.
"""
import inspect
import os
from importlib.metadata import version

from voiceml import Client

from odoo.modules.module import get_manifest
from odoo.tests import BaseCase, tagged
from odoo.tools import parse_version

_MODULE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIN = 'voiceml>=0.9.5,<0.10'


@tagged('at_install', '-post_install')
class TestSdkPin(BaseCase):

    def test_manifest_declares_the_floor(self):
        deps = get_manifest('connect_voicetel')['external_dependencies']['python']
        self.assertIn(PIN, deps)

    def test_repo_requirements_match_the_manifest(self):
        path = os.path.join(os.path.dirname(_MODULE), 'requirements.txt')
        with open(path) as f:
            lines = [l.split('#')[0].strip() for l in f]
        voiceml = [l for l in lines if l.startswith('voiceml')]
        self.assertEqual(voiceml, [PIN])

    def test_installed_sdk_meets_the_floor(self):
        installed = parse_version(version('voiceml'))
        self.assertGreaterEqual(installed, parse_version('0.9.5'))
        self.assertLess(installed, parse_version('0.10'))

    def test_number_update_accepts_status_callback(self):
        client = Client(account_sid='AC' + '0' * 32, api_key='x',
                        base_url='https://voiceml.voicetel.com')
        params = inspect.signature(client.incoming_phone_numbers.update).parameters
        self.assertIn('status_callback', params)
        self.assertIn('status_callback_method', params)
