# -*- coding: utf-8 -*-
import inspect
import unittest
from itertools import count
from unittest.mock import MagicMock, patch

from odoo.tools import mute_logger
from odoo.tests import TransactionCase, tagged
from psycopg2 import IntegrityError

SHARED_MODELS = (
    'connect.call',
    'connect.channel',
    'connect.message',
    'connect.recording',
    'connect.settings',
    'connect.tool',
    'connect.user',
    'res.partner',
)

# Fields this module may extend without a voiceml prefix: selection_add on
# core provider switches, which every provider module extends the same way.
SHARED_SELECTION_FIELDS = {
    'connect.settings': {'rest_provider'},
    'connect.tool': {'adapter'},
    'connect.user': {'originate_provider', 'message_provider'},
}


@tagged('post_install', '-at_install')
class TestProviderCoexistence(TransactionCase):
    """VoiceML shares the Connect ledger models with other provider modules.

    Odoo merges every module's class for a model into one MRO, so a method
    name VoiceML and another module both define, where either skips super(),
    runs only the version merged last, for both modules' callers. A field
    both declare is one column, and the last declaration sets its comodel.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Without another provider installed every check below passes
        # vacuously, so say so instead of reporting green.
        installed = cls.env['ir.module.module'].sudo().search_count(
            [('name', '=', 'connect_twilio'), ('state', '=', 'installed')])
        if not installed:
            raise unittest.SkipTest('connect_twilio is not installed: coexistence cannot be exercised')

    def _module_classes(self, model_name):
        for cls in type(self.env[model_name]).__mro__:
            module = getattr(cls, '_module', None)
            if not module or module in ('connect', 'base') or '_name' in cls.__dict__:
                continue
            yield module, cls

    def _module_methods(self, model_name):
        by_module = {}
        for module, cls in self._module_classes(model_name):
            methods = {
                name: member for name, member in vars(cls).items()
                if inspect.isfunction(member)
            }
            by_module.setdefault(module, {}).update(methods)
        return by_module

    def test_shared_method_names_chain_through_super(self):
        clashes = []
        for model_name in SHARED_MODELS:
            if model_name not in self.env:
                continue
            by_module = self._module_methods(model_name)
            ours = by_module.get('connect_voiceml', {})
            for module, theirs in by_module.items():
                if module == 'connect_voiceml':
                    continue
                for name in sorted(set(ours) & set(theirs)):
                    sources = (inspect.getsource(ours[name]), inspect.getsource(theirs[name]))
                    if not all('super(' in source for source in sources):
                        clashes.append('%s.%s (connect_voiceml, %s)' % (model_name, name, module))
        self.assertFalse(
            clashes,
            'Same-named methods without super() on shared models; name the '
            'VoiceML one apart or chain both: %s' % ', '.join(clashes))

    def test_shared_model_fields_are_namespaced(self):
        unprefixed = []
        for model_name in SHARED_MODELS:
            if model_name not in self.env:
                continue
            allowed = SHARED_SELECTION_FIELDS.get(model_name, set())
            for name, field in self.env[model_name]._fields.items():
                if 'connect_voiceml' not in (getattr(field, '_modules', None) or ()):
                    continue
                if name in allowed or 'voiceml' in name:
                    continue
                unprefixed.append('%s.%s' % (model_name, name))
        self.assertFalse(
            unprefixed,
            'VoiceML fields on shared models must carry voiceml in the name, '
            'or another provider can own the same column: %s' % ', '.join(unprefixed))


@tagged('post_install', '-at_install')
class TestVoicemlUserServerSide(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # No test here may reach VoiceML: every remote call goes to a mock
        # whose created objects carry unique string sids.
        sids = count(1)
        client = MagicMock()
        client.sip.domains.create.side_effect = lambda **kw: MagicMock(sid='DN%04d' % next(sids))
        client.sip.credential_lists.create.side_effect = lambda **kw: MagicMock(sid='CL%04d' % next(sids))
        client.sip.credential_lists.credentials.return_value.create.side_effect = \
            lambda **kw: MagicMock(sid='CR%04d' % next(sids))
        cls.startClassPatcher(patch.object(
            type(cls.env['connect.settings']), '_voiceml_client', return_value=client))
        Domain = cls.env['connect.voiceml.domain']
        cls.domain_a = Domain.create({'domain_name': 'a.example.com'})
        cls.domain_b = Domain.create({'domain_name': 'b.example.com'})
        cls.User = cls.env['connect.user']

    def _new_user(self, login, **vals):
        res_user = self.env['res.users'].create({'name': login, 'login': login})
        vals.update({'user': res_user.id, 'name': login})
        return self.User.create(vals)

    def test_domain_delete_keeps_plain_user(self):
        user = self._new_user('plain_keep')
        user.voiceml_domain = self.domain_b
        self.domain_b.unlink()
        self.assertTrue(user.exists())
        self.assertFalse(user.voiceml_domain)

    def test_plain_user_gets_no_domain(self):
        user = self._new_user('plain_nodom')
        self.assertFalse(user.voiceml_domain)

    def test_enabling_phone_picks_a_domain(self):
        user = self._new_user('phone_dom', voiceml_username='phonedom')
        user.voiceml_client_enabled = True
        self.assertTrue(user.voiceml_domain)

    def test_two_records_get_two_sids(self):
        # Remote credentials exist only for SIP-enabled users. create() has no
        # password yet so it makes no remote call; the write below provisions.
        users = self._new_user(
            'sid_one', voiceml_username='sidone', voiceml_domain=self.domain_a.id,
            voiceml_sip_enabled=True) \
            + self._new_user(
            'sid_two', voiceml_username='sidtwo', voiceml_domain=self.domain_a.id,
            voiceml_sip_enabled=True)
        self.env['connect.settings'].set_param('voiceml_auto_sync', True)
        sids = iter(['CRsid1', 'CRsid2'])
        with patch.object(
                type(self.User), '_create_voiceml_sip_account',
                autospec=True, side_effect=lambda *a, **k: next(sids)):
            users.write({'voiceml_password': 'Passw0rdPassw0rd'})
        self.assertEqual(sorted(users.mapped('voiceml_sid')), ['CRsid1', 'CRsid2'])

    def test_same_username_resolves_per_domain(self):
        # voiceml_username is globally unique, so one name lives in one domain.
        # The URI host picks the user when it matches; an unknown host falls
        # back to the username alone only because it is unambiguous.
        one = self._new_user('dup_one', voiceml_username='dupuser', voiceml_domain=self.domain_a.id)
        self.assertEqual(self.User.get_user_by_uri('sip:dupuser@a.example.com'), one)
        self.assertEqual(self.User.get_user_by_uri('sip:dupuser@b.example.com'), one)
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            self._new_user('dup_two', voiceml_username='dupuser', voiceml_domain=self.domain_b.id)

    def test_empty_wss_setting_yields_domain_url(self):
        user = self.User.search([('user', '=', self.env.user.id)], limit=1)
        if not user:
            user = self.User.create({'user': self.env.user.id, 'name': 'wss_user'})
        user.write({
            'voiceml_username': 'wssuser',
            'voiceml_domain': self.domain_a.id,
            'voiceml_client_enabled': True,
        })
        config = self.User.get_voiceml_phone_config()
        self.assertTrue(config['ok'])
        self.assertEqual(config['wss_url'], 'wss://a.example.com:8443')
