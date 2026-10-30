"""connect_textable next to Connect core and the other carriers: routing
through _get_message_provider(), super() chains on shared models and
namespaced fields."""
import inspect
import unittest
from unittest.mock import patch

from odoo import models
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from .common import (
    CONTACT, SENDER_A, SENDER_B, SENDER_C, FakeTextable, TextableSendCase)

SHARED_MODELS = (
    'connect.message',
    'connect.settings',
    'connect.tool',
    'connect.user',
)

# Selection fields every carrier extends with selection_add.
SHARED_SELECTION_FIELDS = {
    'connect.tool': {'adapter'},
    'connect.user': {'message_provider'},
}


def _methods_by_module(model, include_definitions=False):
    """{module: {name: function}} of the classes that extend the model;
    with ``include_definitions`` the class that defines it counts too."""
    by_module = {}
    for cls in type(model).__mro__:
        module = getattr(cls, '_module', None)
        if not module or module == 'base':
            continue
        if '_name' in cls.__dict__ and not include_definitions:
            continue
        methods = {name: member for name, member in vars(cls).items() if inspect.isfunction(member)}
        by_module.setdefault(module, {}).update(methods)
    return by_module


@tagged('post_install', '-at_install')
class TestTextableCoexistence(TextableSendCase):

    def test_send_falls_through_when_textable_is_not_the_provider(self):
        Settings = type(self.env['connect.settings'])
        fake = FakeTextable()
        with patch.object(Settings, '_get_message_provider', lambda *args, **kwargs: 'not_textable'):
            with self.assertRaises(UserError) as caught:
                self._send(CONTACT, 'Not for Textable', fake)
        self.assertIn('No messaging module can handle this message', str(caught.exception))
        self.assertEqual(fake.calls, [])
        self.assertFalse(self._intents())

    def test_a_textable_tool_in_context_routes_a_system_send(self):
        """Crons have no Connect user: they route with connect_tool_id."""
        tool = self.env['connect.tool'].create({'name': 'Textable tenant', 'adapter': 'textable'})
        fake = FakeTextable(200)
        Message = self.env['connect.message'].with_context(connect_tool_id=tool.id)
        with self.enter_registry_test_mode(), fake.installed():
            message = Message.send(CONTACT, 'Cron reminder', outgoing_callerid=SENDER_B)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(message.sudo().textable_state, 'sent_unconfirmed')
        self.assertEqual(message.sudo().from_number, SENDER_B)

    def test_textable_overrides_of_core_methods_call_super(self):
        ours = _methods_by_module(self.env['connect.message']).get('connect_textable', {})
        for name in ('send', '_own_numbers'):
            with self.subTest(method=name):
                self.assertIn(name, ours)
                self.assertIn('super()', inspect.getsource(ours[name]))

    def test_shared_method_names_chain_through_super(self):
        clashes = []
        for model_name in SHARED_MODELS:
            by_module = _methods_by_module(self.env[model_name])
            ours = by_module.get('connect_textable', {})
            for module, theirs in by_module.items():
                if module in ('connect_textable', 'connect'):
                    continue
                for name in sorted(set(ours) & set(theirs)):
                    sources = (inspect.getsource(ours[name]), inspect.getsource(theirs[name]))
                    if not all('super(' in source for source in sources):
                        clashes.append('%s.%s (connect_textable, %s)' % (model_name, name, module))
        self.assertFalse(clashes, 'Same-named methods without super(): %s' % ', '.join(clashes))

    def test_new_methods_on_shared_models_are_namespaced(self):
        unprefixed = []
        for model_name in SHARED_MODELS:
            by_module = _methods_by_module(self.env[model_name], include_definitions=True)
            ours = by_module.get('connect_textable', {})
            inherited = {
                name for module, methods in by_module.items()
                if module != 'connect_textable' for name in methods}
            inherited |= {name for name in dir(models.BaseModel)}
            for name in ours:
                if name in inherited or name.startswith('__'):
                    continue
                if 'textable' not in name:
                    unprefixed.append('%s.%s' % (model_name, name))
        self.assertFalse(unprefixed, 'Unprefixed new methods: %s' % ', '.join(unprefixed))

    def test_shared_model_fields_are_namespaced(self):
        unprefixed = []
        for model_name in SHARED_MODELS:
            allowed = SHARED_SELECTION_FIELDS.get(model_name, set())
            for name, field in self.env[model_name]._fields.items():
                if 'connect_textable' not in (getattr(field, '_modules', None) or ()):
                    continue
                if name in allowed or 'textable' in name:
                    continue
                unprefixed.append('%s.%s' % (model_name, name))
        self.assertFalse(unprefixed, 'Unprefixed fields: %s' % ', '.join(unprefixed))

    def test_own_numbers_include_every_textable_number(self):
        self.number_b.active = False
        own = self.env['connect.message']._own_numbers()
        self.assertIn(SENDER_A, own)
        self.assertIn(SENDER_B, own, 'An archived number still sent the messages on record')

    def test_an_echo_without_a_sender_user_reads_as_outgoing(self):
        message = self.env['connect.message'].create({
            'from_number': SENDER_A, 'to_number': CONTACT, 'body': 'Sent in the app', 'status': 'sent'})
        self.assertEqual(message.direction, 'outgoing')


@tagged('post_install', '-at_install')
class TestTextableWithVoiceTel(TextableSendCase):
    """Both carriers installed: each user's sends go to their own carrier."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        installed = cls.env['ir.module.module'].sudo().search_count(
            [('name', '=', 'connect_voicetel'), ('state', '=', 'installed')])
        if not installed:
            raise unittest.SkipTest('connect_voicetel is not installed: coexistence cannot be exercised')
        cls.voicetel_user = new_test_user(
            cls.env, login='voicetel-sender-%s' % cls.run_tag,
            groups='base.group_user,connect.group_user')
        cls._connect_user(cls.voicetel_user, provider='voicetel')

    def _license_ok(self):
        return patch.object(type(self.env['oduist.license']), 'check_license', return_value=True)

    def test_a_voicetel_user_does_not_reach_textable(self):
        fake = FakeTextable()
        with self._license_ok(), self.assertRaises(Exception) as caught:
            self._send(CONTACT, 'VoiceTel user', fake, as_user=self.voicetel_user)
        self.assertNotIn('Textable', str(caught.exception))
        self.assertEqual(fake.calls, [])
        self.assertFalse(self._intents())

    def test_a_textable_user_still_reaches_textable(self):
        fake = FakeTextable(200)
        with self._license_ok():
            message = self._send(CONTACT, 'Textable user', fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(message.textable_state, 'sent_unconfirmed')

    def test_own_numbers_are_the_union(self):
        own = self.env['connect.message']._own_numbers()
        self.assertTrue({SENDER_A, SENDER_B} <= own)
        self.assertNotIn(SENDER_C, own)
