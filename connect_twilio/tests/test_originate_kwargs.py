# -*- coding: utf-8 -*-
import ast
from unittest.mock import patch

from odoo.addons.connect.models.settings import Settings as CoreSettings
from odoo.tests import TransactionCase, tagged
from odoo.tools.misc import file_path


@tagged('post_install', '-at_install')
class TestOriginateKwargs(TransactionCase):
    """A click-to-call keeps its keyword arguments on the way down the MRO."""

    def test_twilio_fall_through_forwards_kwargs(self):
        settings = self.env['connect.settings'].sudo()
        seen = {}

        def base(number, **kwargs):
            seen.update(kwargs, number=number)
            return 'reached base'

        with patch.object(type(settings), '_get_originate_provider', return_value='elsewhere'), \
                patch.object(CoreSettings, 'originate_call', side_effect=base):
            result = settings.originate_call(
                '+15550100', res_model='res.partner', res_id=1,
                whatsapp_call=True, origin_nonce='tab-1', idempotency_key='key-1')

        self.assertEqual(result, 'reached base')
        self.assertEqual(seen, {
            'number': '+15550100',
            'res_model': 'res.partner',
            'res_id': 1,
            'user': None,
            'whatsapp_call': True,
            'origin_nonce': 'tab-1',
            'idempotency_key': 'key-1',
        })

    def test_website_override_takes_and_forwards_kwargs(self):
        # Sits ahead of Twilio in the MRO. A source check, because the install
        # commands do not include connect_website and a skip would hide this.
        with open(file_path('connect_website/models/settings.py')) as source:
            tree = ast.parse(source.read())
        method = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == 'originate_call')
        self.assertEqual(method.args.kwarg and method.args.kwarg.arg, 'kwargs')
        super_calls = [
            node for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'originate_call'
            and isinstance(node.func.value, ast.Call)
            and getattr(node.func.value.func, 'id', None) == 'super'
        ]
        self.assertEqual(len(super_calls), 1)
        spreads = [kw for kw in super_calls[0].keywords if kw.arg is None]
        self.assertEqual([getattr(kw.value, 'id', None) for kw in spreads], ['kwargs'])
