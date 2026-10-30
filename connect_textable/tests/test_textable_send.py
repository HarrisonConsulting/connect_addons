"""The send path through connect.message.send(): sender credentials, the
local gate, the intent committed before the POST, honest outcomes and
what is logged. Every request goes to FakeTextable; none leaves the
process."""
import json
from unittest.mock import patch

import requests
from urllib3.exceptions import NewConnectionError

from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tests.test_cursor import TestCursor

from odoo.addons.connect_textable.models.message import TextableGateError
from odoo.addons.connect_textable.tools.textable_client import PROBE_PATH, SEND_PATH

from .common import (
    CONTACT, CONTACT_2, CONTACT_3, NOT_NANP, SENDER_A, SENDER_B, SENDER_C,
    TENANT_URL, FakeTextable, TextableSendCase, capture_logs, digits, sha256_hex)

STATUS_OUTCOMES = [
    # (answer, outcome, message status, textable_state)
    (200, 'accepted', 'sent', 'sent_unconfirmed'),
    (201, 'accepted', 'sent', 'sent_unconfirmed'),
    (302, 'rejected_request', 'failed', 'failed'),
    (400, 'rejected_request', 'failed', 'failed'),
    (401, 'auth_failed', 'failed', 'failed'),
    (403, 'auth_failed', 'failed', 'failed'),
    (404, 'rejected_contact', 'failed', 'failed'),
    (422, 'rejected_request', 'failed', 'failed'),
    (429, 'throttled', 'failed', 'failed'),
    (500, 'ambiguous', 'unknown', 'unknown'),
    (503, 'ambiguous', 'unknown', 'unknown'),
]


def _refused_connection():
    return requests.exceptions.ConnectionError(
        NewConnectionError(None, 'Failed to establish a new connection: refused'))


@tagged('post_install', '-at_install')
class TestTextableSend(TextableSendCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Generous caps; the cap tests lower them.
        (cls.number_a | cls.number_b).write({
            'hourly_cap': 1000, 'daily_cap': 1000, 'recipient_daily_cap': 1000})
        cls.partner = cls.env['res.partner'].create({'name': 'Textable Contact'})

    # ------------------------------------------------------------------
    # Credentials and request
    # ------------------------------------------------------------------

    def test_credentials_follow_the_sender_number(self):
        """Each send uses the token of the sender number's own Textable
        user, and the request body is exactly {to, from, message}."""
        fake = FakeTextable(200, 200)
        self._send(CONTACT, 'Credentials from number B', fake, outgoing_callerid=SENDER_B)
        self._send(CONTACT, 'Credentials from number A', fake, outgoing_callerid=SENDER_A)
        self.assertEqual(len(fake.calls), 2)
        first, second = (call.request for call in fake.calls)
        self.assertEqual(first.headers['Authorization'], 'Bearer %s:%s' % (self.uid_b, self.key_b))
        self.assertEqual(second.headers['Authorization'], 'Bearer %s:%s' % (self.uid_a, self.key_a))
        self.assertEqual(json.loads(first.body), {
            'to': CONTACT, 'from': SENDER_B, 'message': 'Credentials from number B'})
        self.assertEqual(json.loads(second.body), {
            'to': CONTACT, 'from': SENDER_A, 'message': 'Credentials from number A'})
        for call in fake.calls:
            self.assertEqual(call.request.method, 'POST')
            self.assertEqual(call.request.url, TENANT_URL + SEND_PATH)

    def test_default_sender_is_the_first_assigned_number(self):
        fake = FakeTextable(200)
        message = self._send(CONTACT, 'Default sender', fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(json.loads(fake.calls[0].request.body)['from'], SENDER_A)
        self.assertEqual(message.from_number, SENDER_A)
        self.assertEqual(message.textable_number_id, self.number_a)

    # ------------------------------------------------------------------
    # The gate: each refusal names its code, posts nothing, leaves nothing
    # ------------------------------------------------------------------

    def _assert_refused(self, codes, recipient, body, as_user=True, **kwargs):
        if isinstance(codes, str):
            codes = (codes,)
        fake = FakeTextable()
        intents_before = self._intents()
        messages_before = self._messages()
        with self.assertRaises(TextableGateError) as caught:
            self._send(recipient, body, fake, as_user=as_user, **kwargs)
        self.assertIn(caught.exception.textable_code, codes)
        self.assertIn(caught.exception.textable_code, str(caught.exception))
        self.assertEqual(fake.calls, [], 'A refused send must make no HTTP call')
        self.env.invalidate_all()
        self.assertEqual(self._intents(), intents_before, 'A refused send leaves no intent')
        self.assertEqual(self._messages(), messages_before, 'A refused send leaves no message')
        return caught.exception

    def test_gate_refuses_a_number_the_user_is_not_assigned_to(self):
        other = new_test_user(
            self.env, login='textable-other-%s' % self.run_tag,
            groups='base.group_user,connect.group_user')
        self._connect_user(other)
        self._assert_refused(
            'sender_not_assigned', CONTACT, 'Not my number', as_user=other,
            outgoing_callerid=SENDER_A)

    def test_gate_refuses_a_user_with_no_number(self):
        other = new_test_user(
            self.env, login='textable-none-%s' % self.run_tag,
            groups='base.group_user,connect.group_user')
        self._connect_user(other)
        self._assert_refused(
            ('sender_not_assigned', 'no_sender'), CONTACT, 'No number at all', as_user=other)

    def test_gate_refuses_an_archived_number(self):
        self.number_b.active = False
        self._assert_refused(
            ('sender_inactive', 'no_sender'), CONTACT, 'Archived sender',
            outgoing_callerid=SENDER_B)

    def test_gate_refuses_an_unapproved_campaign(self):
        self._number('Charlie line', SENDER_C, 'tx-user-charlie', 'acct-c', 'key-c-SECRET',
                     approved=False)
        self._assert_refused(
            'campaign_not_approved', CONTACT, 'Unapproved campaign',
            outgoing_callerid=SENDER_C)

    def test_gate_refuses_a_destination_outside_nanp(self):
        self._assert_refused('destination_not_nanp', NOT_NANP, 'Overseas')
        self._assert_refused('destination_not_nanp', '2025550147', 'Not E.164')

    def test_gate_refuses_a_blank_or_oversized_body(self):
        self._assert_refused('invalid_body', CONTACT, '   ')
        self._assert_refused('invalid_body', CONTACT, 'x' * 1601)

    def test_gate_refuses_an_opted_out_recipient(self):
        self.env['phone.blacklist'].sudo()._add([CONTACT_3])
        self._assert_refused('recipient_opted_out', CONTACT_3, 'To an opted-out contact')

    def test_an_archived_blacklist_entry_does_not_block(self):
        Blacklist = self.env['phone.blacklist'].sudo()
        Blacklist._add([CONTACT_3])
        Blacklist._remove([CONTACT_3])
        fake = FakeTextable(200)
        message = self._send(CONTACT_3, 'Opted back in', fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(message.textable_state, 'sent_unconfirmed')

    def test_gate_enforces_the_sender_hourly_cap(self):
        self.number_a.hourly_cap = 2
        fake = FakeTextable(200, 200)
        self._send(CONTACT, 'Hourly one', fake)
        self._send(CONTACT_2, 'Hourly two', fake)
        self.assertEqual(len(fake.calls), 2)
        self._assert_refused('sender_hourly_cap', CONTACT_3, 'Hourly three')

    def test_gate_enforces_the_sender_daily_cap(self):
        self.number_a.daily_cap = 2
        fake = FakeTextable(200, 200)
        self._send(CONTACT, 'Daily one', fake)
        self._send(CONTACT_2, 'Daily two', fake)
        self.assertEqual(len(fake.calls), 2)
        self._assert_refused('sender_daily_cap', CONTACT_3, 'Daily three')

    def test_gate_enforces_the_recipient_daily_cap_across_numbers(self):
        """The per-recipient cap counts sends from every number of the
        company, not only the sending one."""
        (self.number_a | self.number_b).write({'recipient_daily_cap': 2})
        fake = FakeTextable(200, 200)
        self._send(CONTACT, 'Recipient one', fake, outgoing_callerid=SENDER_A)
        self._send(CONTACT, 'Recipient two', fake, outgoing_callerid=SENDER_B)
        self.assertEqual(len(fake.calls), 2)
        self._assert_refused(
            'recipient_daily_cap', CONTACT, 'Recipient three', outgoing_callerid=SENDER_A)
        # Another recipient is still allowed.
        fake = FakeTextable(200)
        self._send(CONTACT_2, 'Other recipient', fake, outgoing_callerid=SENDER_A)
        self.assertEqual(len(fake.calls), 1)

    def test_a_number_of_another_company_is_refused(self):
        company = self.env['res.company'].create({'name': 'Textable Other Co %s' % self.run_tag})
        self._number('Delta line', SENDER_C, 'tx-user-delta', 'acct-d', 'key-d-SECRET',
                     company_id=company.id)
        self._assert_refused(
            ('no_sender', 'sender_not_assigned'), CONTACT, 'Cross-company sender',
            outgoing_callerid=SENDER_C)

    # ------------------------------------------------------------------
    # Configuration errors: no request, no intent
    # ------------------------------------------------------------------

    def _assert_config_error(self, body, **kwargs):
        fake = FakeTextable()
        with self.assertRaises(UserError) as caught:
            self._send(CONTACT, body, fake, **kwargs)
        self.assertNotIsInstance(caught.exception, TextableGateError)
        self.assertEqual(fake.calls, [])
        self.env.invalidate_all()
        self.assertFalse(self._intents(to_number=CONTACT))
        self.assertFalse(self._messages(to_number=CONTACT))
        return caught.exception

    def test_missing_credentials_send_nothing(self):
        self._number('Echo line', SENDER_C, 'tx-user-echo', False, False)
        error = self._assert_config_error('No credentials', outgoing_callerid=SENDER_C)
        self.assertNotIn('Bearer', str(error))

    def test_missing_base_url_sends_nothing(self):
        self.env['connect.settings'].set_param('textable_base_url', False)
        self._assert_config_error('No base URL')

    def test_a_base_url_planted_in_parameters_is_checked_before_sending(self):
        """The tenant URL is validated before every request, not only on
        save: a value written straight into ir.config_parameter is refused."""
        params = self.env['ir.config_parameter'].sudo()
        for planted in ('http://example-tenant.textable.app',
                        'https://textable.app.attacker.example.com',
                        'https://attacker.example.com'):
            params.set_param('connect.textable_base_url', planted)
            error = self._assert_config_error('Planted URL %s' % planted)
            self.assertNotIn(self.key_a, str(error))

    # ------------------------------------------------------------------
    # Intent before the POST, and never twice
    # ------------------------------------------------------------------

    def test_intent_is_committed_before_the_post(self):
        body = 'Intent before the POST'
        seen = {}

        def inspect_intent(request):
            # The claim cursor is closed: its work is committed or gone.
            seen['open_cursors'] = list(TestCursor._cursors_stack)
            with self.registry.cursor() as cr:
                cr.execute(
                    'SELECT state, outcome, from_number, to_number FROM connect_textable_send '
                    'WHERE body_hash = %s', [sha256_hex(body)])
                seen['rows'] = cr.fetchall()

        fake = FakeTextable(200, on_request=inspect_intent)
        self._send(CONTACT, body, fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(seen['open_cursors'], [], 'The intent cursor is still open during the POST')
        self.assertEqual(seen['rows'], [('queued', None, SENDER_A, CONTACT)],
                         'The intent must be visible from another cursor before the POST')
        self.env.invalidate_all()
        intent = self._intents(body_hash=sha256_hex(body))
        self.assertRecordValues(intent, [{
            'state': 'done', 'outcome': 'accepted', 'http_status': 200, 'attempts': 1,
            'number_id': self.number_a.id, 'from_number': SENDER_A, 'to_number': CONTACT,
            'user_id': self.sender_user.id,
        }])
        self.assertTrue(intent.finished_at)

    def test_intent_never_stores_the_message_text(self):
        body = 'Intent text stays on the message %s' % self.run_tag
        self._send(CONTACT, body, FakeTextable(200))
        self.env.flush_all()
        self.env.cr.execute(
            'SELECT count(*) FROM connect_textable_send t '
            'WHERE strpos(row_to_json(t)::text, %s) > 0', [body])
        self.assertEqual(self.env.cr.fetchone()[0], 0)

    def test_a_rollback_after_the_post_does_not_resend(self):
        """The caller's transaction fails after Textable took the message.
        The intent survived; sending the same message again posts nothing
        and records the known outcome."""
        body = 'Rolled back after the POST'
        fake = FakeTextable(200)
        Partner = type(self.env['res.partner'])
        with patch.object(Partner, 'message_post', side_effect=RuntimeError('crash after the POST')):
            with self.assertRaises(RuntimeError):
                self._send(CONTACT, body, fake, res_model='res.partner', res_id=self.partner.id)
        self.assertEqual(len(fake.calls), 1)
        # What the caller's rollback takes away: the message it created.
        self._messages(to_number=CONTACT).unlink()

        again = FakeTextable()
        message = self._send(CONTACT, body, again, res_model='res.partner', res_id=self.partner.id)
        self.assertEqual(again.calls, [], 'A send already accepted was posted again')
        self.assertEqual(message.textable_state, 'sent_unconfirmed')
        self.assertEqual(message.status, 'sent')
        self.env.invalidate_all()
        self.assertEqual(len(self._intents(body_hash=sha256_hex(body))), 1)

    def test_an_intent_with_no_recorded_outcome_is_never_resent(self):
        """When the outcome could not be written, the intent stays in
        flight; a later identical send posts nothing and says unknown."""
        body = 'Outcome lost'
        Message = type(self.env['connect.message'])
        with patch.object(Message, '_textable_record_outcome', lambda *args, **kwargs: None):
            self._send(CONTACT, body, FakeTextable(200))
        self._messages(to_number=CONTACT).unlink()
        again = FakeTextable()
        message = self._send(CONTACT, body, again)
        self.assertEqual(again.calls, [])
        self.assertEqual(message.textable_state, 'unknown')
        self.assertEqual(message.status, 'unknown')
        self.env.invalidate_all()
        self.assertRecordValues(self._intents(body_hash=sha256_hex(body)), [
            {'state': 'queued', 'outcome': False}])

    def test_the_same_send_twice_posts_once(self):
        fake = FakeTextable(200)
        first = self._send(CONTACT, 'Double click', fake)
        second = self._send(CONTACT, 'Double click', fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(first, second)

    def test_a_caller_key_dedupes_the_same_message(self):
        fake = FakeTextable(200)
        first = self._send(CONTACT, 'Keyed reminder', fake, idempotency_key='order-42-reminder')
        second = self._send(CONTACT, 'Keyed reminder', fake, idempotency_key='order-42-reminder')
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(first, second)

    def test_a_caller_key_is_bound_to_its_message(self):
        """Reusing a key for another body or recipient is refused before
        anything is sent, whatever the first send's outcome was."""
        for answer in (200, 400, requests.exceptions.ReadTimeout('read timed out')):
            with self.subTest(answer=answer):
                key = 'bound-%r' % (answer,)
                first_body = 'Keyed first %r' % (answer,)
                self._send(CONTACT, first_body, FakeTextable(answer), idempotency_key=key)
                for recipient, body in ((CONTACT, 'Keyed second %r' % (answer,)),
                                        (CONTACT_2, first_body)):
                    error = self._assert_refused(
                        'idempotency_conflict', recipient, body, idempotency_key=key)
                    self.assertNotIn(body, str(error))
                self.env.invalidate_all()
                self.assertRecordValues(self._intents(body_hash=sha256_hex(first_body)), [
                    {'to_number': CONTACT, 'attempts': 1}])

    def test_a_reclaimed_key_counts_against_the_caps_from_its_new_claim(self):
        """A key posted again after a definite failure counts as a send made
        now, not when the key was first used."""
        self.number_a.hourly_cap = 2
        body = 'Keyed, refused, then posted again'
        self._send(CONTACT, body, FakeTextable(400), idempotency_key='cap-key')
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE connect_textable_send SET create_date = create_date - interval '2 days', "
            "claimed_at = claimed_at - interval '2 days' WHERE body_hash = %s", [sha256_hex(body)])
        self.env.invalidate_all()
        fake = FakeTextable(200, 200)
        again = self._send(CONTACT, body, fake, idempotency_key='cap-key')
        self.assertEqual(again.textable_state, 'sent_unconfirmed')
        self._send(CONTACT_2, 'Within the cap', fake)
        self.assertEqual(len(fake.calls), 2)
        self._assert_refused('sender_hourly_cap', CONTACT_3, 'Past the cap')
        self.env.invalidate_all()
        intent = self._intents(body_hash=sha256_hex(body))
        self.assertEqual(intent.attempts, 2)
        self.assertGreater(intent.claimed_at, intent.create_date)

    def test_ambiguous_is_unknown_and_never_resent(self):
        for answer in (requests.exceptions.ReadTimeout('read timed out'), 500, 503):
            with self.subTest(answer=answer):
                body = 'Ambiguous %r' % (answer,)
                fake = FakeTextable(answer)
                message = self._send(CONTACT, body, fake)
                self.assertEqual(len(fake.calls), 1)
                self.assertRecordValues(message, [{
                    'status': 'unknown', 'textable_state': 'unknown',
                    'error_code': 'ambiguous', 'has_error': True,
                }])
                again = FakeTextable()
                self._send(CONTACT, body, again)
                with self.enter_registry_test_mode(), again.installed():
                    message.action_retry()
                self.assertEqual(again.calls, [], 'An ambiguous send was posted again')

    def test_a_definite_failure_may_be_posted_again(self):
        body = 'Refused then retried'
        first = self._send(CONTACT, body, FakeTextable(400))
        self.assertRecordValues(first, [{
            'status': 'failed', 'textable_state': 'failed', 'error_code': 'rejected_request'}])
        fake = FakeTextable(200)
        second = self._send(CONTACT, body, fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(second.textable_state, 'sent_unconfirmed')
        self.env.invalidate_all()
        self.assertRecordValues(self._intents(body_hash=sha256_hex(body)), [
            {'outcome': 'accepted', 'attempts': 2}])

    # ------------------------------------------------------------------
    # Outcomes
    # ------------------------------------------------------------------

    def test_every_outcome_maps_to_an_honest_message(self):
        cases = list(STATUS_OUTCOMES) + [
            (requests.exceptions.ConnectTimeout('connect timed out'), 'unreachable', 'failed', 'failed'),
            (_refused_connection(), 'unreachable', 'failed', 'failed'),
            (requests.exceptions.ReadTimeout('read timed out'), 'ambiguous', 'unknown', 'unknown'),
            (requests.exceptions.ChunkedEncodingError('cut'), 'ambiguous', 'unknown', 'unknown'),
        ]
        for index, (answer, outcome, status, state) in enumerate(cases):
            with self.subTest(answer=answer):
                fake = FakeTextable(answer)
                message = self._send(CONTACT, 'Outcome case %d' % index, fake)
                self.assertEqual(len(fake.calls), 1)
                accepted = outcome == 'accepted'
                self.assertRecordValues(message, [{
                    'status': status, 'textable_state': state,
                    'error_code': False if accepted else outcome,
                    'has_error': not accepted, 'direction': 'outgoing',
                    'from_number': SENDER_A, 'to_number': CONTACT,
                    'textable_number_id': self.number_a.id,
                    'sender_user': self.sender_user.id,
                }])
                self.assertNotEqual(message.status, 'delivered')
                self.env.invalidate_all()
                intent = self._intents(body_hash=sha256_hex('Outcome case %d' % index))
                self.assertEqual(intent.outcome, outcome)

    def test_redirect_is_not_followed(self):
        """A 3xx is a refusal: the request goes out with redirects off, so
        the token never follows a Location header."""
        fake = FakeTextable(302)
        message = self._send(CONTACT, 'Redirected', fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertIs(fake.calls[0].kwargs.get('allow_redirects'), False)
        self.assertEqual(message.error_code, 'rejected_request')

    # ------------------------------------------------------------------
    # Nothing secret in exceptions or logs
    # ------------------------------------------------------------------

    def test_refusals_carry_no_full_number_or_body(self):
        body = 'Secret body %s' % self.run_tag
        self.env['phone.blacklist'].sudo()._add([CONTACT_3])
        error = self._assert_refused('recipient_opted_out', CONTACT_3, body)
        text = str(error)
        for value in (body, CONTACT_3, digits(CONTACT_3), self.key_a, self.uid_a):
            self.assertNotIn(value, text)

    def test_logs_never_carry_secrets_bodies_or_full_numbers(self):
        body = 'Private body %s' % self.run_tag
        answers = [
            200, 401, 404, 500, requests.exceptions.ReadTimeout('read timed out'),
            _refused_connection(),
        ]
        with capture_logs() as logs:
            for index, answer in enumerate(answers):
                self._send(CONTACT, '%s #%d' % (body, index), FakeTextable(answer),
                           outgoing_callerid=SENDER_B)
            with self.assertRaises(TextableGateError):
                self._send(NOT_NANP, body, FakeTextable())
        self.assertTrue(logs.module, 'The send path logged nothing at all')
        logs.assert_clean(
            self, self.key_a, self.uid_a, self.key_b, self.uid_b, body,
            'Bearer', CONTACT, digits(CONTACT), digits(CONTACT)[1:],
            SENDER_B, digits(SENDER_B), NOT_NANP, digits(NOT_NANP))

    # ------------------------------------------------------------------
    # Test message wizard and connection test
    # ------------------------------------------------------------------

    def _system_user(self):
        return new_test_user(
            self.env, login='textable-admin-%s' % self.run_tag,
            groups='base.group_user,base.group_system')

    def test_wizard_sends_once_for_a_system_user(self):
        admin = self._system_user()
        wizard = self.env['connect.textable.test.message'].with_user(admin).create({
            'number_id': self.number_b.id, 'recipient': CONTACT, 'body': 'Wizard test'})
        fake = FakeTextable(200)
        with self.enter_registry_test_mode(), fake.installed():
            action = wizard.action_send()
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0].request.headers['Authorization'],
                         'Bearer %s:%s' % (self.uid_b, self.key_b))
        self.assertEqual(action['params']['type'], 'success')
        self.assertNotIn(self.key_b, json.dumps(action, default=str))

    def test_wizard_goes_through_the_gate(self):
        admin = self._system_user()
        self._number('Foxtrot line', SENDER_C, 'tx-user-foxtrot', 'acct-f', 'key-f-SECRET',
                     approved=False, assigned=False)
        number_c = self.env['connect.textable.number'].search([('phone_number', '=', SENDER_C)])
        wizard = self.env['connect.textable.test.message'].with_user(admin).create({
            'number_id': number_c.id, 'recipient': CONTACT, 'body': 'Wizard gate'})
        fake = FakeTextable()
        with self.assertRaises(TextableGateError) as caught, \
                self.enter_registry_test_mode(), fake.installed():
            wizard.action_send()
        self.assertEqual(caught.exception.textable_code, 'campaign_not_approved')
        self.assertEqual(fake.calls, [])

    def test_wizard_is_system_only(self):
        wizard = self.env['connect.textable.test.message'].create({
            'number_id': self.number_a.id, 'recipient': CONTACT, 'body': 'Wizard denied'})
        fake = FakeTextable()
        with self.assertRaises(AccessError), self.enter_registry_test_mode(), fake.installed():
            wizard.with_user(self.sender_user).action_send()
        self.assertEqual(fake.calls, [])
        with self.assertRaises(AccessError):
            self.env['connect.textable.test.message'].with_user(self.sender_user).create({
                'number_id': self.number_a.id, 'recipient': CONTACT, 'body': 'Wizard denied'})

    def test_connection_test_is_one_get_for_a_system_user(self):
        admin = self._system_user()
        fake = FakeTextable(200)
        with fake.installed():
            action = self.number_a.with_user(admin).action_textable_test_connection()
        self.assertEqual(len(fake.calls), 1)
        request = fake.calls[0].request
        self.assertEqual(request.method, 'GET')
        self.assertEqual(request.url, TENANT_URL + PROBE_PATH)
        self.assertEqual(request.headers['Authorization'], 'Bearer %s:%s' % (self.uid_a, self.key_a))
        self.assertEqual(action['params']['type'], 'success')
        self.assertNotIn(self.key_a, json.dumps(action, default=str))
        self.assertFalse(self._intents(), 'A connection test is not a send')

    def test_connection_test_reports_a_refused_token(self):
        admin = self._system_user()
        fake = FakeTextable(401)
        with fake.installed():
            action = self.number_a.with_user(admin).action_textable_test_connection()
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(action['params']['type'], 'danger')
        self.assertIn('auth_failed', action['params']['message'])

    def test_connection_test_is_system_only(self):
        fake = FakeTextable()
        with fake.installed():
            for action in ('action_textable_test_connection', 'action_textable_open_test_message'):
                with self.assertRaises(AccessError):
                    getattr(self.number_a.with_user(self.sender_user), action)()
        self.assertEqual(fake.calls, [])
