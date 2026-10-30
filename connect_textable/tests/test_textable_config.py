"""Credentials, tenant URL, relay secret rotation, neutralization and the
werkzeug log redaction."""
import hashlib
import logging
import re

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.modules.neutralize import get_neutralization_queries
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from .common import CONTACT, FakeTextable, TextableSendCase

RELAY_URL = re.compile(r'(https://odoo\.example\.com/textable/webhook/relay/)([A-Za-z0-9_-]+)')


def _rotate(number):
    """Rotate the relay secret of the number's Textable user; return
    (token, the action shown)."""
    action = number.action_textable_rotate_relay()
    match = RELAY_URL.search(action['params']['message'])
    if not match:
        raise AssertionError('No relay URL in the rotation notice: %r' % action['params']['message'])
    return match.group(2), action


@tagged('post_install', '-at_install')
class TestTextableConfig(TextableSendCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param('connect.api_url', 'https://odoo.example.com')
        cls.admin = new_test_user(
            cls.env, login='textable-sysadmin-%s' % cls.run_tag,
            groups='base.group_user,base.group_system')

    def _settings(self):
        return self.env['connect.settings'].sudo().search([], limit=1)

    def _relay(self, textable_user_id):
        return self.env['connect.textable.relay'].sudo().search(
            [('textable_user_id', '=', textable_user_id)])

    def _rows_containing(self, table, value):
        self.env.flush_all()
        self.env.cr.execute(
            'SELECT count(*) FROM %s t WHERE strpos(row_to_json(t)::text, %%s) > 0' % table,
            [value])
        return self.env.cr.fetchone()[0]

    # ------------------------------------------------------------------
    # Per-number credentials
    # ------------------------------------------------------------------

    def test_credentials_are_stored_apart_and_shown_masked(self):
        number = self.number_a.sudo()
        self.assertEqual(number.account_uid, self.uid_a)
        self.assertEqual(number.api_key, self.key_a)
        self.assertEqual(number.display_api_key, '*' * len(self.key_a))
        self.assertEqual(number.display_account_uid, '*' * len(self.uid_a))
        self.assertTrue(number.textable_credentials_set)

    def test_the_mask_coming_back_changes_nothing(self):
        self.number_a.write({'display_api_key': '*' * len(self.key_a)})
        self.assertEqual(self.number_a.sudo().api_key, self.key_a)
        self.number_a.write({'display_api_key': 'key-rotated-SECRET'})
        self.assertEqual(self.number_a.sudo().api_key, 'key-rotated-SECRET')
        self.assertEqual(self.number_a.display_api_key, '*' * len('key-rotated-SECRET'))

    def test_a_connect_user_cannot_read_the_token(self):
        number = self.number_a.with_user(self.sender_user)
        for field in ('api_key', 'account_uid'):
            with self.subTest(field=field), self.assertRaises(AccessError):
                number.read([field])
        values = number.read(['display_api_key', 'display_account_uid', 'phone_number'])[0]
        self.assertNotIn(self.key_a, str(values))
        self.assertNotIn(self.uid_a, str(values))

    def test_a_connect_user_cannot_change_a_number(self):
        with self.assertRaises(AccessError):
            self.number_a.with_user(self.sender_user).write({'display_api_key': 'stolen-SECRET'})
        self.assertEqual(self.number_a.sudo().api_key, self.key_a)

    def test_campaign_and_caps_are_for_system_administrators(self):
        """A Connect admin manages numbers but cannot approve a campaign or
        raise a cap: those gates are the system administrator's."""
        connect_admin = new_test_user(
            self.env, login='textable-connect-admin-%s' % self.run_tag,
            groups='base.group_user,connect.group_admin')
        number = self.number_b.with_user(connect_admin)
        self.assertFalse(number.textable_policy_editable)
        for vals in ({'campaign_state': 'not_approved'}, {'campaign_reference': 'CAMP-FORGED'},
                     {'campaign_use_case': 'Marketing'}, {'hourly_cap': 2 ** 31 - 1},
                     {'daily_cap': 100000}, {'recipient_daily_cap': 5000},
                     {'optout_hourly_cap': 9999}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                number.write(vals)
        # Unchanged values and the other fields stay editable.
        number.write({'name': 'Bravo line renamed', 'hourly_cap': self.number_b.hourly_cap})
        self.assertEqual(self.number_b.name, 'Bravo line renamed')
        Number = self.env['connect.textable.number'].with_user(connect_admin)
        base = {'name': 'Admin line', 'phone_number': '+12015550127',
                'textable_user_id': 'tx-user-admin'}
        with self.assertRaises(AccessError):
            Number.create(dict(base, campaign_state='approved', campaign_reference='CAMP-X'))
        with self.assertRaises(AccessError):
            Number.create(dict(base, daily_cap=100000))
        created = Number.create(dict(base))
        self.assertEqual(created.campaign_state, 'not_approved')
        system_admin = new_test_user(
            self.env, login='textable-policy-admin-%s' % self.run_tag,
            groups='base.group_user,base.group_system,connect.group_admin')
        self.assertTrue(self.number_b.with_user(system_admin).textable_policy_editable)
        self.number_b.with_user(system_admin).write({'hourly_cap': 5})
        self.assertEqual(self.number_b.hourly_cap, 5)

    def test_a_number_is_configured_once_across_companies(self):
        company = self.env['res.company'].create({'name': 'Textable Twin Co %s' % self.run_tag})
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError):
            self._number('Alpha twin', self.number_a.phone_number, 'tx-user-twin',
                         'acct-twin', 'key-twin-SECRET', company_id=company.id, assigned=False)

    def test_numbers_are_nanp_and_approval_needs_a_reference(self):
        with self.assertRaises(ValidationError):
            self._number('Bad line', '+442071838751', 'tx-user-bad', 'a', 'b', approved=False)
        with self.assertRaises(ValidationError):
            self.number_a.write({'campaign_reference': False})

    # ------------------------------------------------------------------
    # Tenant URL
    # ------------------------------------------------------------------

    def test_base_url_is_validated_on_save(self):
        Settings = self.env['connect.settings']
        for bad in ('http://example-tenant.textable.app',
                    'https://example.com',
                    'https://textable.app.attacker.example.com',
                    'https://user:pw@example-tenant.textable.app',
                    'https://example-tenant.textable.app:8443',
                    'https://example-tenant.textable.app/api'):
            with self.subTest(url=bad), self.assertRaises(ValidationError):
                Settings.set_param('textable_base_url', bad)
        Settings.set_param('textable_base_url', 'https://other-tenant.textable.app/')
        self.assertEqual(Settings._textable_base_url(), 'https://other-tenant.textable.app')

    def test_tenant_settings_are_for_system_administrators(self):
        settings = self._settings().with_user(self.sender_user)
        for field in ('textable_base_url', 'textable_organization_id'):
            with self.subTest(field=field), self.assertRaises(AccessError):
                settings.read([field])
        with self.assertRaises(AccessError):
            self.env['connect.textable.relay'].with_user(self.sender_user).search([])

    # ------------------------------------------------------------------
    # Relay secret, one per Textable user
    # ------------------------------------------------------------------

    def test_rotation_keeps_only_the_hash(self):
        token, action = _rotate(self.number_a.with_user(self.admin))
        self.assertGreaterEqual(len(token), 32)
        self.assertTrue(action['params']['sticky'])
        self.assertIn(self.number_a.textable_user_id, action['params']['message'])
        relay = self._relay(self.number_a.textable_user_id)
        self.assertEqual(relay.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertTrue(relay.rotated_at)
        self.assertTrue(self.number_a.textable_relay_configured)
        for table in ('connect_textable_relay', 'connect_settings', 'ir_config_parameter',
                      'mail_message', 'ir_logging'):
            self.assertEqual(self._rows_containing(table, token), 0,
                             'The relay token is stored in %s' % table)

    def test_rotation_replaces_the_old_secret(self):
        first, _action = _rotate(self.number_a)
        second, _action = _rotate(self.number_a)
        self.assertNotEqual(first, second)
        relay = self._relay(self.number_a.textable_user_id)
        self.assertEqual(len(relay), 1)
        self.assertEqual(relay.token_hash, hashlib.sha256(second.encode()).hexdigest())

    def test_each_textable_user_has_its_own_secret(self):
        """Textable posts to each user's own relayWebhook: a rotation for
        one user leaves the other user's URL working."""
        token_a, _action = _rotate(self.number_a)
        self.assertFalse(self.number_b.textable_relay_configured)
        token_b, _action = _rotate(self.number_b)
        self.assertNotEqual(token_a, token_b)
        Relay = self.env['connect.textable.relay']
        self.assertEqual(Relay._textable_match_token(token_a)[1].textable_user_id,
                         self.number_a.textable_user_id)
        self.assertEqual(Relay._textable_match_token(token_b)[1].textable_user_id,
                         self.number_b.textable_user_id)
        _rotate(self.number_a)
        self.assertFalse(Relay._textable_match_token(token_a)[1])
        self.assertEqual(Relay._textable_match_token(token_b)[1].textable_user_id,
                         self.number_b.textable_user_id)

    def test_numbers_of_one_textable_user_share_its_secret(self):
        sibling = self._number(
            'Alpha second line', '+12015550126', self.number_a.textable_user_id,
            'acct-a2', 'key-a2-SECRET')
        _rotate(self.number_a)
        self.assertTrue(sibling.textable_relay_configured)
        self.assertEqual(len(self._relay(self.number_a.textable_user_id)), 1)

    def test_rotation_is_system_only(self):
        with self.assertRaises(AccessError):
            self.number_a.with_user(self.sender_user).action_textable_rotate_relay()
        with self.assertRaises(AccessError):
            self.env['connect.textable.relay'].with_user(self.sender_user)._textable_rotate(
                self.number_a.textable_user_id)
        self.assertFalse(self._relay(self.number_a.textable_user_id))

    def test_rotation_needs_an_https_public_url(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('connect.api_url', 'http://odoo.example.com')
        with self.assertRaises(UserError):
            self.number_a.action_textable_rotate_relay()
        self.assertFalse(self._relay(self.number_a.textable_user_id).token_hash)

    # ------------------------------------------------------------------
    # Neutralization
    # ------------------------------------------------------------------

    def test_neutralization_blanks_every_secret_and_stops_sends(self):
        token_a, _action = _rotate(self.number_a)
        token_b, _action = _rotate(self.number_b)
        self.env.flush_all()
        queries = list(get_neutralization_queries(['connect_textable']))
        self.assertTrue(queries, 'connect_textable ships no neutralization')
        for query in queries:
            self.env.cr.execute(query)
        self.env.invalidate_all()
        # ir.config_parameter reads are cached per process.
        self.env.registry.clear_cache()

        for number in (self.number_a, self.number_b):
            number = number.sudo()
            self.assertFalse(number.account_uid)
            self.assertFalse(number.api_key)
            self.assertFalse(number.display_account_uid)
            self.assertFalse(number.display_api_key)
            self.assertFalse(number.textable_credentials_set)
            # The rest of the configuration survives.
            self.assertTrue(number.phone_number)
            self.assertTrue(number.textable_user_id)
            self.assertEqual(number.campaign_state, 'approved')
        for number in (self.number_a, self.number_b):
            self.assertFalse(self._relay(number.textable_user_id).token_hash)
            self.assertFalse(number.textable_relay_configured)
        Relay = self.env['connect.textable.relay']
        for token in (token_a, token_b):
            self.assertEqual(Relay._textable_match_token(token), (False, Relay.browse()))

        fake = FakeTextable()
        with self.assertRaises(UserError):
            self._send(CONTACT, 'After neutralization', fake)
        self.assertEqual(fake.calls, [])
        self.env.invalidate_all()
        self.assertFalse(self._intents(to_number=CONTACT))

    # ------------------------------------------------------------------
    # Access log redaction
    # ------------------------------------------------------------------

    def test_werkzeug_access_log_redacts_the_relay_token(self):
        werkzeug_logger = logging.getLogger('werkzeug')
        markers = [getattr(f, 'marker', None) for f in werkzeug_logger.filters]
        self.assertEqual(markers.count('connect_textable_relay_redactor'), 1,
                         'The relay redactor must be installed exactly once')
        token = 'tok-%s-SECRET_value' % self.run_tag
        record = logging.LogRecord(
            'werkzeug', logging.INFO, __file__, 1, '%s - - [%s] "%s" %s %s',
            ('127.0.0.1', '30/Oct/2026 10:00:00',
             'POST /textable/webhook/relay/%s HTTP/1.1' % token, '200', '-'), None)
        for log_filter in werkzeug_logger.filters:
            log_filter.filter(record)
        rendered = record.getMessage()
        self.assertNotIn(token, rendered)
        self.assertIn('/textable/webhook/relay/', rendered)
        other = logging.LogRecord(
            'werkzeug', logging.INFO, __file__, 1, '"%s" %s', ('GET /web HTTP/1.1', '200'), None)
        for log_filter in werkzeug_logger.filters:
            log_filter.filter(other)
        self.assertEqual(other.getMessage(), '"GET /web HTTP/1.1" 200')
