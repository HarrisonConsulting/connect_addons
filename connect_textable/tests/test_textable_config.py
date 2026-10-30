"""Credentials, tenant URL, relay secret rotation, neutralization and the
werkzeug log redaction."""
import hashlib
import logging
import re

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.modules.neutralize import get_neutralization_queries
from odoo.tests import new_test_user, tagged

from .common import CONTACT, FakeTextable, TextableSendCase

RELAY_URL = re.compile(r'(https://odoo\.example\.com/textable/webhook/relay/)([A-Za-z0-9_-]+)')


def _rotate(env):
    """Rotate the relay secret; return (token, the action shown)."""
    action = env['connect.settings'].rotate_textable_relay_secret()
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
        for field in ('textable_base_url', 'textable_organization_id', 'textable_relay_token_hash'):
            with self.subTest(field=field), self.assertRaises(AccessError):
                settings.read([field])

    # ------------------------------------------------------------------
    # Relay secret
    # ------------------------------------------------------------------

    def test_rotation_keeps_only_the_hash(self):
        token, action = _rotate(self.env['connect.settings'].with_user(self.admin))
        self.assertGreaterEqual(len(token), 32)
        self.assertTrue(action['params']['sticky'])
        settings = self._settings()
        self.assertEqual(settings.textable_relay_token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertTrue(settings.textable_relay_rotated_at)
        self.assertTrue(settings.textable_relay_configured)
        for table in ('connect_settings', 'ir_config_parameter', 'mail_message', 'ir_logging'):
            self.assertEqual(self._rows_containing(table, token), 0,
                             'The relay token is stored in %s' % table)
        self.assertFalse(self.env['ir.config_parameter'].sudo().get_param(
            'connect.textable_relay_token_hash'), 'The relay hash was mirrored into a parameter')

    def test_rotation_replaces_the_old_secret(self):
        first, _action = _rotate(self.env['connect.settings'])
        second, _action = _rotate(self.env['connect.settings'])
        self.assertNotEqual(first, second)
        self.assertEqual(self._settings().textable_relay_token_hash,
                         hashlib.sha256(second.encode()).hexdigest())

    def test_rotation_is_system_only(self):
        before = self._settings().textable_relay_token_hash
        with self.assertRaises(AccessError):
            self.env['connect.settings'].with_user(self.sender_user).rotate_textable_relay_secret()
        self.assertEqual(self._settings().textable_relay_token_hash, before)

    def test_rotation_needs_an_https_public_url(self):
        before = self._settings().textable_relay_token_hash
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('connect.api_url', 'http://odoo.example.com')
        with self.assertRaises(UserError):
            self.env['connect.settings'].rotate_textable_relay_secret()
        self.assertEqual(self._settings().textable_relay_token_hash, before)

    # ------------------------------------------------------------------
    # Neutralization
    # ------------------------------------------------------------------

    def test_neutralization_blanks_every_secret_and_stops_sends(self):
        _rotate(self.env['connect.settings'])
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('connect.textable_relay_token_hash', 'a-mirrored-copy')
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
        self.assertFalse(self._settings().textable_relay_token_hash)
        self.assertFalse(self.env['connect.settings']._textable_relay_token_hash())
        self.assertFalse(params.get_param('connect.textable_relay_token_hash'))

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
