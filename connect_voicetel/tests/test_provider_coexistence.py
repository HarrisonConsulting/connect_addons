# -*- coding: utf-8 -*-
"""VoiceTel next to connect_twilio on the shared Connect ledger models."""
import inspect
import unittest

from odoo.tests import TransactionCase, new_test_user, tagged

SHARED_MODELS = (
    'connect.call',
    'connect.channel',
    'connect.message',
    'connect.recording',
    'connect.settings',
    'connect.tool',
    'connect.user',
    'res.partner',
    'res.users',
)

# Fields this module may extend without a voicetel prefix: selection_add on
# core provider switches, which every provider module extends the same way.
SHARED_SELECTION_FIELDS = {
    'connect.settings': {'rest_provider'},
    'connect.tool': {'adapter'},
    'connect.user': {'originate_provider', 'message_provider'},
}


@tagged('post_install', '-at_install')
class TestProviderCoexistence(TransactionCase):
    """Odoo merges every module's class for a model into one MRO, so a method
    name VoiceTel and another module both define, where either skips super(),
    runs only the version merged last, for both modules' callers. A field
    both declare is one column, and the last declaration sets its comodel.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        installed = cls.env['ir.module.module'].sudo().search_count(
            [('name', '=', 'connect_twilio'), ('state', '=', 'installed')])
        if not installed:
            raise unittest.SkipTest(
                'connect_twilio is not installed: coexistence cannot be exercised')

    def _module_methods(self, model_name):
        by_module = {}
        for cls in type(self.env[model_name]).__mro__:
            module = getattr(cls, '_module', None)
            if not module or module in ('connect', 'base') or '_name' in cls.__dict__:
                continue
            methods = {
                name: member for name, member in vars(cls).items()
                if inspect.isfunction(member)
            }
            by_module.setdefault(module, {}).update(methods)
        return by_module

    def test_shared_method_names_chain_through_super(self):
        clashes = []
        for model_name in SHARED_MODELS:
            by_module = self._module_methods(model_name)
            ours = by_module.get('connect_voicetel', {})
            for module, theirs in by_module.items():
                if module == 'connect_voicetel':
                    continue
                for name in sorted(set(ours) & set(theirs)):
                    sources = (inspect.getsource(ours[name]), inspect.getsource(theirs[name]))
                    if not all('super(' in source for source in sources):
                        clashes.append('%s.%s (connect_voicetel, %s)' % (model_name, name, module))
        self.assertFalse(
            clashes,
            'Same-named methods without super() on shared models; name the '
            'VoiceTel one apart or chain both: %s' % ', '.join(clashes))

    def test_shared_model_fields_are_namespaced(self):
        unprefixed = []
        for model_name in SHARED_MODELS:
            allowed = SHARED_SELECTION_FIELDS.get(model_name, set())
            for name, field in self.env[model_name]._fields.items():
                if 'connect_voicetel' not in (getattr(field, '_modules', None) or ()):
                    continue
                if name in allowed or 'voicetel' in name:
                    continue
                unprefixed.append('%s.%s' % (model_name, name))
        self.assertFalse(
            unprefixed,
            'VoiceTel fields on shared models must carry voicetel in the name, '
            'or another provider can own the same column: %s' % ', '.join(unprefixed))


@tagged('post_install', '-at_install')
class TestProviderPlumbing(TransactionCase):

    def test_tool_adapter_resolves_voicetel(self):
        tool = self.env['connect.tool'].create({
            'name': 'VoiceTel voice', 'adapter': 'voicetel'})
        odoo_user = new_test_user(self.env, login='vt_tool_user')
        connect_user = self.env['connect.user'].with_context(
            no_voicetel_create=True).create({'user': odoo_user.id})
        self.env['connect.user.tool'].create(
            {'user_id': connect_user.id, 'tool_id': tool.id, 'use': 'voice'})
        self.assertEqual(connect_user.first_tool('voice').adapter, 'voicetel')
        self.assertEqual(
            self.env['connect.settings']._get_originate_provider(odoo_user), 'voicetel')

    def test_rest_provider_credentials_and_log_context(self):
        Settings = self.env['connect.settings']
        Settings.sudo().set_param('voicetel_account_sid', 'ACvoicetel123')
        Settings.sudo().set_param('voicetel_api_key', 'k' * 32)
        Settings.sudo().set_param('rest_provider', 'voicetel')
        self.assertEqual(
            Settings._get_client_credentials(), ('ACvoicetel123', 'k' * 32))
        context = Settings._provider_log_context()
        self.assertIn('provider=voicetel', context)
        self.assertNotIn('k' * 32, context)
