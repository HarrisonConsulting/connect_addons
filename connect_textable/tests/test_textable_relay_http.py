"""The relay webhook over real HTTP: path token, preconditions, payload
checks, inbound messages, keywords, echoes, and what is stored and
logged. Textable signs nothing, so every refusal fails closed."""
import json
import logging
import re
from unittest.mock import patch

import requests

from odoo.tests import HttpCase, tagged

from odoo.addons.connect_textable.controllers import relay as relay_controller
from odoo.addons.connect_textable.models import message as message_module

from .common import (
    CONTACT, CONTACT_2, ORG_ID, SENDER_A, SENDER_B, TEXTABLE_USER_A, TEXTABLE_USER_B,
    FakeTextable, TextableFixtureMixin, capture_logs, digits)

RELAY_PREFIX = '/textable/webhook/relay/'
RELAY_URL = re.compile(r'https://odoo\.example\.com/textable/webhook/relay/([A-Za-z0-9_-]+)')
JSON_HEADERS = {'Content-Type': 'application/json'}
STORED_TABLES = (
    'connect_message', 'connect_textable_send', 'connect_textable_optout',
    'connect_textable_relay', 'connect_settings', 'ir_config_parameter', 'mail_message',
    'ir_logging',
)


@tagged('post_install', '-at_install')
class TestTextableRelayHttp(TextableFixtureMixin, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._textable_fixtures()
        cls.env['ir.config_parameter'].sudo().set_param('connect.api_url', 'https://odoo.example.com')
        cls.webhook_user = cls.env.ref('connect.user_connect_webhook')

    def setUp(self):
        super().setUp()
        message_module._ACCESS_TOKEN_WARNED.clear()
        self.addCleanup(message_module._ACCESS_TOKEN_WARNED.clear)
        # Process-wide state: each test starts with an empty refusal window.
        relay_controller.REFUSAL_LOG.reset()
        self.addCleanup(relay_controller.REFUSAL_LOG.reset)
        self.token = self._rotate(self.number_a)
        self.token_b = self._rotate(self.number_b)
        self.serial = 0

    def _rotate(self, number):
        action = number.action_textable_rotate_relay()
        return RELAY_URL.search(action['params']['message']).group(1)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _payload(self, **overrides):
        self.serial += 1
        payload = {
            'MessageID': 'msg-%s-%d' % (self.run_tag, self.serial),
            'ConversationID': 'conv-%s' % self.run_tag,
            'ToNumber': SENDER_A,
            'FromNumber': CONTACT,
            'MessageBody': 'Inbound hello %d' % self.serial,
            'MessageDirection': 'in',
            'TextableUserID': TEXTABLE_USER_A,
            'TextableOrganizationID': ORG_ID,
            'ContactName': 'Pat Doe',
            'ContactEmail': 'pat@example.com',
            'Media': [],
        }
        payload.update(overrides)
        return payload

    def _post(self, payload=None, token=None, data=None, headers=JSON_HEADERS):
        """POST to the relay with every log record captured; returns
        (response, logs)."""
        if data is None:
            data = json.dumps(payload).encode()
        self.env.flush_all()
        with capture_logs() as logs:
            response = self.url_open(
                RELAY_PREFIX + (token if token is not None else self.token),
                data=data, headers=headers)
        self.env.invalidate_all()
        return response, logs

    def _relay_messages(self):
        return self.env['connect.message'].sudo().search(
            [('textable_number_id', '!=', False)])

    def _assert_nothing_written(self, before_messages, before_optouts=None):
        self.assertEqual(self._relay_messages(), before_messages)
        if before_optouts is not None:
            self.assertEqual(self.env['connect.textable.optout'].sudo().search([]), before_optouts)

    def _critical(self, logs):
        return logs.module_texts(logging.CRITICAL)

    def _rows_containing(self, value):
        self.env.flush_all()
        found = {}
        for table in STORED_TABLES:
            self.env.cr.execute(
                'SELECT count(*) FROM %s t WHERE strpos(row_to_json(t)::text, %%s) > 0' % table,
                [value])
            count = self.env.cr.fetchone()[0]
            if count:
                found[table] = count
        return found

    def _blacklisted(self, phone, active=True):
        return self.env['phone.blacklist'].sudo().with_context(active_test=False).search(
            [('number', '=', phone), ('active', '=', active)])

    def _send_now(self, recipient, body, answer, **kwargs):
        """A send from the test thread; the registry is already in test
        mode for an HttpCase. The fake is removed before any url_open."""
        fake = FakeTextable(answer)
        with fake.installed():
            message = self.env['connect.message'].with_context(
                connect_tool_id=self.textable_tool.id).send(recipient, body, **kwargs)
        self.assertEqual(len(fake.calls), 1)
        return message.sudo()

    @property
    def textable_tool(self):
        tool = self.env['connect.tool'].search([('adapter', '=', 'textable')], limit=1)
        return tool or self.env['connect.tool'].create({'name': 'Textable', 'adapter': 'textable'})

    # ------------------------------------------------------------------
    # The path token and transport preconditions
    # ------------------------------------------------------------------

    def test_a_wrong_or_missing_token_is_404_and_writes_nothing(self):
        before = self._relay_messages()
        Message = type(self.env['connect.message'])
        with patch.object(Message, '_textable_relay') as relay:
            for token in ('not-the-token', self.token[:-1], self.token + 'x'):
                with self.subTest(token=token):
                    response, logs = self._post(self._payload(), token=token)
                    self.assertEqual(response.status_code, 404)
                    logs.assert_clean(self, self.token)
            relay.assert_not_called()
        self._assert_nothing_written(before)

    def test_token_refusals_log_one_critical_line_per_interval(self):
        """Anyone can post to the relay path: a burst of wrong tokens logs
        one CRITICAL line, and the next interval's line counts the rest."""
        clock = [1000.0]
        with patch.object(relay_controller.REFUSAL_LOG, 'clock', lambda: clock[0]):
            response, logs = self._post(self._payload(), token='scan-1')
            self.assertEqual(response.status_code, 404)
            first = self._critical(logs)
            self.assertEqual(len(first), 1)
            self.assertIn('SECURITY', first[0])
            for attempt in range(2, 5):
                response, logs = self._post(self._payload(), token='scan-%d' % attempt)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(self._critical(logs), [], 'A refusal inside the interval was logged')
            clock[0] += relay_controller.REFUSAL_LOG_INTERVAL
            response, logs = self._post(self._payload(), token='scan-5')
            self.assertEqual(response.status_code, 404)
            summary = self._critical(logs)
            self.assertEqual(len(summary), 1)
            self.assertIn('3 more relay posts', summary[0])

    def test_no_configured_secret_is_404(self):
        self.env['connect.textable.relay'].sudo().search([]).write({'token_hash': False})
        before = self._relay_messages()
        response, logs = self._post(self._payload())
        self.assertEqual(response.status_code, 404)
        self.assertTrue(self._critical(logs))
        self._assert_nothing_written(before)

    def test_an_old_token_stops_working_after_rotation(self):
        old = self.token
        self.token = self._rotate(self.number_a)
        response, logs = self._post(self._payload(), token=old)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(self._critical(logs))
        # The other Textable user's URL keeps working.
        response, _logs = self._post(self._payload(
            TextableUserID=TEXTABLE_USER_B, ToNumber=SENDER_B), token=self.token_b)
        self.assertEqual(response.status_code, 200)

    def test_an_oversized_body_is_413_even_with_a_wrong_token(self):
        before = self._relay_messages()
        payload = self._payload(MessageBody='x' * 20000)
        for token in (self.token, 'not-the-token'):
            with self.subTest(valid=token == self.token):
                response, _logs = self._post(payload, token=token)
                self.assertEqual(response.status_code, 413)
        self._assert_nothing_written(before)

    def test_a_body_without_length_is_411(self):
        before = self._relay_messages()
        body = json.dumps(self._payload()).encode()
        response, _logs = self._post(data=iter([body]))
        self.assertEqual(response.status_code, 411)
        self._assert_nothing_written(before)

    def test_a_body_that_is_not_json_is_refused(self):
        before = self._relay_messages()
        body = json.dumps(self._payload()).encode()
        response, _logs = self._post(data=body, headers={'Content-Type': 'text/plain'})
        self.assertEqual(response.status_code, 415)
        response, _logs = self._post(data=b'{"MessageID": ', headers=JSON_HEADERS)
        self.assertEqual(response.status_code, 400)
        self._assert_nothing_written(before)

    def test_only_post_is_routed(self):
        self.env.flush_all()
        with capture_logs():
            response = self.url_open(RELAY_PREFIX + self.token)
        self.assertNotEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # Organization, user and owned number
    # ------------------------------------------------------------------

    def test_a_foreign_organization_or_user_is_refused(self):
        before = self._relay_messages()
        cases = {
            'organization': self._payload(TextableOrganizationID='org-somebody-else'),
            'no organization': {k: v for k, v in self._payload().items()
                                if k != 'TextableOrganizationID'},
            'user': self._payload(TextableUserID='tx-user-unknown'),
            'no user': {k: v for k, v in self._payload().items() if k != 'TextableUserID'},
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                response, logs = self._post(payload)
                self.assertEqual(response.status_code, 404)
                self.assertTrue(any('SECURITY' in t for t in self._critical(logs)))
        self._assert_nothing_written(before)

    def test_one_users_url_cannot_speak_for_another_user(self):
        """Textable user ids are readable by Connect users; only the token
        binds a post to a user. User A's URL carrying user B's id and
        number is refused and writes nothing."""
        before = self._relay_messages()
        before_optouts = self.env['connect.textable.optout'].sudo().search([])
        for payload in (
                self._payload(TextableUserID=TEXTABLE_USER_B, ToNumber=SENDER_B),
                self._payload(TextableUserID=TEXTABLE_USER_B, ToNumber=SENDER_B,
                              MessageBody='STOP'),
                self._payload(TextableUserID=TEXTABLE_USER_B, MessageDirection='out',
                              FromNumber=SENDER_B, ToNumber=CONTACT)):
            with self.subTest(payload=payload['MessageID']):
                response, logs = self._post(payload, token=self.token)
                self.assertEqual(response.status_code, 404)
                self.assertTrue(any('SECURITY' in t for t in self._critical(logs)))
        self._assert_nothing_written(before, before_optouts)
        self.assertFalse(self._blacklisted(CONTACT))

    def test_no_configured_organization_refuses_everything(self):
        self.env['connect.settings'].set_param('textable_organization_id', False)
        before = self._relay_messages()
        response, logs = self._post(self._payload())
        self.assertEqual(response.status_code, 404)
        self.assertTrue(self._critical(logs))
        self._assert_nothing_written(before)

    def test_a_number_the_user_does_not_hold_is_dropped(self):
        """User A's relay carries a message for user B's number: answered
        200 so Textable does not retry, and nothing is written."""
        before = self._relay_messages()
        before_optouts = self.env['connect.textable.optout'].sudo().search([])
        for payload in (
                self._payload(ToNumber=SENDER_B),
                self._payload(ToNumber='+12015550199'),
                self._payload(MessageDirection='out', FromNumber=SENDER_B, ToNumber=CONTACT),
                self._payload(ToNumber=SENDER_B, MessageBody='STOP')):
            with self.subTest(payload=payload['MessageID']):
                response, _logs = self._post(payload)
                self.assertEqual(response.status_code, 200)
        self._assert_nothing_written(before, before_optouts)
        self.assertFalse(self._blacklisted(CONTACT))

    def test_an_archived_number_accepts_nothing(self):
        self.number_a.active = False
        before = self._relay_messages()
        response, _logs = self._post(self._payload())
        self.assertEqual(response.status_code, 200)
        self._assert_nothing_written(before)

    def test_a_malformed_payload_is_400(self):
        before = self._relay_messages()
        for name, payload in {
            'list': ['not', 'an', 'object'],
            'direction': self._payload(MessageDirection='sideways'),
            'no id': {k: v for k, v in self._payload().items() if k != 'MessageID'},
            'body type': self._payload(MessageBody={'text': 'hi'}),
            'long body': self._payload(MessageBody='y' * 5000),
            'media type': self._payload(Media='https://example.com/a.png'),
            'NUL in FromNumber': self._payload(FromNumber=CONTACT + '\x00'),
            'NUL in ToNumber': self._payload(ToNumber=SENDER_A + '\x00'),
            'NUL in MessageID': self._payload(MessageID='msg-\x00-%s' % self.run_tag),
            'NUL in body': self._payload(MessageBody='hello\x00world'),
            'escape in body': self._payload(MessageBody='hello \x1b[31m'),
            'FromNumber not a number': self._payload(FromNumber='sip:pat@example.com'),
            'ToNumber not a number': self._payload(ToNumber='owner'),
        }.items():
            with self.subTest(case=name):
                response, logs = self._post(payload)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(
                    [r for r in logs.module + logs.other if r.levelno >= logging.ERROR],
                    'A malformed payload logged an ERROR')
        self._assert_nothing_written(before)

    def test_deeply_nested_json_is_400(self):
        before = self._relay_messages()
        data = b'[' * 5000 + b']' * 5000
        self.assertLess(len(data), relay_controller.MAX_RELAY_BYTES)
        response, logs = self._post(data=data)
        self.assertEqual(response.status_code, 400)
        self.assertFalse([r for r in logs.module + logs.other if r.levelno >= logging.ERROR])
        self._assert_nothing_written(before)

    def test_line_breaks_in_a_body_are_kept(self):
        payload = self._payload(MessageBody='Line one\nLine two\tend')
        response, _logs = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._relay_messages().body, 'Line one\nLine two\tend')

    # ------------------------------------------------------------------
    # Inbound messages
    # ------------------------------------------------------------------

    def test_an_inbound_message_is_recorded_as_the_webhook_user(self):
        payload = self._payload(MessageBody='Running late, see you at 10')
        response, logs = self._post(payload)
        self.assertEqual(response.status_code, 200)
        message = self._relay_messages()
        self.assertEqual(len(message), 1)
        self.assertRecordValues(message, [{
            'status': 'received', 'textable_state': 'received', 'direction': 'incoming',
            'message_sid': payload['MessageID'], 'from_number': CONTACT, 'to_number': SENDER_A,
            'body': 'Running late, see you at 10', 'textable_number_id': self.number_a.id,
            'textable_conversation_id': payload['ConversationID'],
            'create_uid': self.webhook_user.id,
        }])
        logs.assert_clean(self, self.token, payload['MessageBody'], digits(CONTACT))

    def test_media_is_counted_not_fetched(self):
        payload = self._payload(Media=['https://cdn.example.com/a.jpg', 'https://cdn.example.com/b.jpg'])
        response, _logs = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertRecordValues(self._relay_messages(), [{'num_media': 2, 'message_type': 'mms'}])

    def test_a_repeated_message_id_is_recorded_once(self):
        payload = self._payload()
        for _attempt in range(3):
            response, _logs = self._post(payload)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._relay_messages()), 1)

    def test_an_inbound_body_is_escaped_on_the_record(self):
        partner = self.env['res.partner'].create({'name': 'Relay Contact'})
        self._send_now(CONTACT, 'Your order shipped', 200, outgoing_callerid=SENDER_A,
                       res_model='res.partner', res_id=partner.id)
        payload = self._payload(MessageBody='<img src=x onerror=alert(1)>')
        response, _logs = self._post(payload)
        self.assertEqual(response.status_code, 200)
        reply = self._relay_messages().filtered(lambda m: m.status == 'received')
        self.assertEqual((reply.res_model, reply.res_id), ('res.partner', partner.id))
        posted = partner.message_ids.filtered(lambda m: 'onerror' in (m.body or ''))
        self.assertTrue(posted)
        self.assertNotIn('<img', posted[0].body)

    # ------------------------------------------------------------------
    # STOP / START
    # ------------------------------------------------------------------

    def test_stop_then_start_restores_the_blacklist(self):
        response, _logs = self._post(self._payload(MessageBody='Stop'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._blacklisted(CONTACT), 'STOP did not opt the contact out')
        self.assertEqual(self._relay_messages().textable_keyword, 'STOP')

        response, _logs = self._post(self._payload(MessageBody='START'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self._blacklisted(CONTACT), 'START did not opt the contact back in')
        optouts = self.env['connect.textable.optout'].sudo().search([('phone', '=', CONTACT)])
        self.assertEqual(sorted(optouts.mapped('action')), ['start', 'stop'])

    def test_opt_out_wording_is_recognized(self):
        keyword = type(self.env['connect.message'])._textable_keyword
        for body, expected in (
                ('STOP', 'STOP'), ('Stop', 'STOP'), ('stop!', 'STOP'), ('Stop.', 'STOP'),
                ('Stop, ', 'STOP'), ('stop \U0001F64F', 'STOP'), ('"STOP"', 'STOP'),
                ('STOP ALL', 'STOPALL'), ('stopall', 'STOPALL'), ('Stop  all!', 'STOPALL'),
                ('END', 'END'), ('cancel', 'CANCEL'), ('Quit.', 'QUIT'),
                ('Unsubscribe', 'UNSUBSCRIBE'), ('OPTOUT', 'OPTOUT'), ('Opt-out', 'OPTOUT'),
                ('opt out', 'OPTOUT'), ('OPT_OUT', 'OPTOUT'), ('Revoke', 'REVOKE'),
                ('revoke.', 'REVOKE'), ('Start', 'START'), ('help?', 'HELP'),
                ('Stopping by at 3', None), ("Don't stop", None), ('Please call me', None),
                ('', None), ('\U0001F64F', None)):
            with self.subTest(body=body):
                self.assertEqual(keyword(body), expected)

    def test_opt_out_wording_opts_out_over_the_relay(self):
        for index, body in enumerate(('Opt-out \U0001F64F', 'Revoke', 'STOP ALL')):
            with self.subTest(body=body):
                phone = '+1202555%04d' % (200 + index)
                response, _logs = self._post(self._payload(FromNumber=phone, MessageBody=body))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(self._blacklisted(phone), '%r did not opt the contact out' % body)

    def _post_b(self, **overrides):
        """A post on user B's relay, to number B."""
        return self._post(self._payload(
            TextableUserID=TEXTABLE_USER_B, ToNumber=SENDER_B, **overrides), token=self.token_b)

    def test_start_to_another_number_leaves_the_opt_out(self):
        """STOP to number A, START to number B: the contact never opted back
        in to A, so the global blacklist entry stays."""
        response, _logs = self._post(self._payload(MessageBody='STOP'))
        self.assertEqual(response.status_code, 200)
        response, _logs = self._post_b(MessageBody='START')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._blacklisted(CONTACT), 'START to B lifted the opt-out sent to A')
        response, _logs = self._post(self._payload(MessageBody='START'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self._blacklisted(CONTACT), 'START to A did not lift the opt-out')

    def test_start_needs_every_number_opted_back_in(self):
        """STOP to A (makes the entry), STOP to B; START to B alone keeps
        the entry, START to A then removes it."""
        for post in (lambda: self._post(self._payload(MessageBody='STOP')),
                     lambda: self._post_b(MessageBody='STOP'),
                     lambda: self._post_b(MessageBody='START')):
            response, _logs = post()
            self.assertEqual(response.status_code, 200)
        self.assertTrue(self._blacklisted(CONTACT), 'START to B released the opt-out sent to A')
        response, _logs = self._post(self._payload(MessageBody='START'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self._blacklisted(CONTACT))
        stops = self.env['connect.textable.optout'].sudo().search(
            [('phone', '=', CONTACT), ('action', '=', 'stop')])
        self.assertEqual(stops.mapped('lifted'), [True, True])
        self.assertEqual(sorted(stops.mapped('released')), [False, True])

    def test_start_leaves_an_entry_made_elsewhere(self):
        self.env['phone.blacklist'].sudo()._add([CONTACT_2])
        for body in ('STOP', 'START'):
            response, _logs = self._post(self._payload(FromNumber=CONTACT_2, MessageBody=body))
            self.assertEqual(response.status_code, 200)
        self.assertTrue(self._blacklisted(CONTACT_2), 'START removed an opt-out it did not make')

    def test_start_without_a_stop_changes_nothing(self):
        self.env['phone.blacklist'].sudo()._add([CONTACT_2])
        response, _logs = self._post(self._payload(FromNumber=CONTACT_2, MessageBody='START'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._blacklisted(CONTACT_2))

    def test_help_changes_nothing(self):
        response, _logs = self._post(self._payload(MessageBody='HELP'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self._blacklisted(CONTACT))
        self.assertFalse(self.env['connect.textable.optout'].sudo().search([]))

    def test_keywords_past_the_hourly_cap_change_nothing(self):
        self.number_a.optout_hourly_cap = 1
        response, _logs = self._post(self._payload(MessageBody='STOP'))
        self.assertEqual(response.status_code, 200)
        response, logs = self._post(self._payload(FromNumber=CONTACT_2, MessageBody='STOP'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._blacklisted(CONTACT))
        self.assertFalse(self._blacklisted(CONTACT_2), 'The opt-out cap did not hold')
        self.assertTrue(logs.module_texts(logging.WARNING))
        capped = self.env['connect.textable.optout'].sudo().search([('phone', '=', CONTACT_2)])
        self.assertTrue(capped.capped)

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------

    def test_access_token_and_path_token_are_never_stored_or_logged(self):
        access_token = 'access-%s-SECRET' % self.run_tag
        first, first_logs = self._post(self._payload(AccessToken=access_token))
        second, second_logs = self._post(self._payload(AccessToken=access_token))
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(len(self._relay_messages()), 2)
        for value in (access_token, self.token, self.token_b):
            self.assertEqual(self._rows_containing(value), {}, 'A secret is stored')
        warnings = [t for t in first_logs.module_texts(logging.WARNING) if 'AccessToken' in t]
        self.assertEqual(len(warnings), 1, 'The AccessToken warning names the user once')
        self.assertFalse([t for t in second_logs.module_texts(logging.WARNING) if 'AccessToken' in t])
        for logs in (first_logs, second_logs):
            logs.assert_clean(self, access_token, self.token)

    def test_refusals_log_no_payload(self):
        payload = self._payload(
            TextableOrganizationID='org-%s-leak' % self.run_tag,
            MessageBody='Refused body %s' % self.run_tag, AccessToken='at-%s-SECRET' % self.run_tag)
        _response, logs = self._post(payload)
        logs.assert_clean(
            self, self.token, payload['TextableOrganizationID'], payload['MessageBody'],
            payload['AccessToken'], CONTACT, digits(CONTACT))

    # ------------------------------------------------------------------
    # Echoes of outbound messages
    # ------------------------------------------------------------------

    def _echo(self, body, **overrides):
        return self._payload(
            MessageDirection='out', FromNumber=SENDER_A, ToNumber=CONTACT,
            MessageBody=body, **overrides)

    def test_an_echo_settles_an_unknown_send(self):
        sent = self._send_now(
            CONTACT, 'Appointment moved to 3pm', requests.exceptions.ReadTimeout('read timed out'),
            outgoing_callerid=SENDER_A)
        self.assertEqual(sent.textable_state, 'unknown')
        payload = self._echo('Appointment moved to 3pm')
        response, _logs = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertRecordValues(sent, [{
            'textable_state': 'sent_unconfirmed', 'status': 'sent', 'has_error': False,
            'message_sid': payload['MessageID']}])
        intent = self.env['connect.textable.send'].sudo().search(
            [('send_key', '=', sent.textable_send_key)])
        self.assertEqual(intent.echo_message_uid, payload['MessageID'])
        self.assertFalse(self._relay_messages().filtered(lambda m: m.textable_state == 'foreign'))

    def test_an_echo_of_an_accepted_send_takes_its_message_id(self):
        sent = self._send_now(CONTACT, 'Invoice attached', 200, outgoing_callerid=SENDER_A)
        payload = self._echo('Invoice attached')
        response, _logs = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertRecordValues(sent, [{
            'textable_state': 'sent_unconfirmed', 'message_sid': payload['MessageID']}])
        self.assertEqual(len(self._relay_messages()), 1)

    def test_an_unmatched_echo_is_journaled_as_foreign(self):
        payload = self._echo('Typed in the Textable app')
        response, _logs = self._post(payload)
        self.assertEqual(response.status_code, 200)
        self.assertRecordValues(self._relay_messages(), [{
            'textable_state': 'foreign', 'status': 'sent', 'direction': 'outgoing',
            'message_sid': payload['MessageID'], 'from_number': SENDER_A, 'to_number': CONTACT}])

    def test_an_echo_matching_several_sends_settles_none(self):
        first = self._send_now(
            CONTACT, 'Same words twice', requests.exceptions.ReadTimeout('read timed out'),
            outgoing_callerid=SENDER_A, idempotency_key='first')
        second = self._send_now(
            CONTACT, 'Same words twice', requests.exceptions.ReadTimeout('read timed out'),
            outgoing_callerid=SENDER_A, idempotency_key='second')
        response, logs = self._post(self._echo('Same words twice'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual((first.textable_state, second.textable_state), ('unknown', 'unknown'))
        self.assertTrue(logs.module_texts(logging.WARNING))
        self.assertEqual(len(self._relay_messages().filtered(lambda m: m.textable_state == 'foreign')), 1)

    def test_a_repeated_echo_is_recorded_once(self):
        payload = self._echo('Typed twice in the app')
        for _attempt in range(2):
            response, _logs = self._post(payload)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._relay_messages()), 1)

    def test_an_echo_on_user_b_relay_for_user_b_number(self):
        payload = self._payload(
            MessageDirection='out', FromNumber=SENDER_B, ToNumber=CONTACT,
            TextableUserID=TEXTABLE_USER_B, MessageBody='From the bravo line')
        response, _logs = self._post(payload, token=self.token_b)
        self.assertEqual(response.status_code, 200)
        self.assertRecordValues(self._relay_messages(), [{
            'textable_number_id': self.number_b.id, 'textable_state': 'foreign'}])
