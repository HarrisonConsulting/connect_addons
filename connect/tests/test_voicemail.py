# -*- coding: utf-8 -*-
"""Tests for voicemail flow: on_vm_recording_status, voicemail fields, email notifications."""

from unittest.mock import patch, MagicMock

from odoo.tests import tagged
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestVoicemailRecordingStatus(ConnectTestCase):
    """Test on_vm_recording_status webhook handler."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Call = cls.env['connect.call']
        cls.Channel = cls.env['connect.channel']

    def _create_call_with_channel(self, call_status='no-answer', **kwargs):
        """Helper: create a call + channel, return (call, channel)."""
        call = self._create_test_call(
            direction='incoming',
            status=call_status,
            caller='+15551234567',
            called='+15559876543',
            partner=self.partner_1.id,
            **kwargs,
        )
        channel = self.Channel.create({
            'call': call.id,
            'sid': 'CA_vm_' + 'v' * 28,
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': call_status,
            'technical_direction': 'inbound',
        })
        return call, channel

    def _vm_params(self, channel_sid, **overrides):
        """Build standard voicemail recording-status webhook params."""
        params = {
            'CallSid': channel_sid,
            'RecordingUrl': 'https://api.twilio.com/recordings/REvm01.mp3',
            'RecordingDuration': '15',
        }
        params.update(overrides)
        return params

    # ------------------------------------------------------------------
    # Core voicemail recording status
    # ------------------------------------------------------------------

    def test_voicemail_url_stored_on_call(self):
        """Recording status webhook stores voicemail_url on the call."""
        call, channel = self._create_call_with_channel()
        params = self._vm_params(channel.sid)

        self.Call.on_vm_recording_status(params)
        call.invalidate_recordset()

        self.assertEqual(call.voicemail_url, 'https://api.twilio.com/recordings/REvm01.mp3')

    def test_voicemail_duration_stored(self):
        """Voicemail duration captured from webhook params."""
        call, channel = self._create_call_with_channel()
        params = self._vm_params(channel.sid, RecordingDuration='42')

        self.Call.on_vm_recording_status(params)
        call.invalidate_recordset()

        self.assertEqual(call.voicemail_duration, 42)

    def test_voicemail_status_set_when_not_answered(self):
        """Call status updated to 'voicemail' when call was not answered."""
        call, channel = self._create_call_with_channel(call_status='no-answer')
        params = self._vm_params(channel.sid)

        self.Call.on_vm_recording_status(params)
        call.invalidate_recordset()

        self.assertEqual(call.status, 'voicemail')

    def test_voicemail_status_not_overwritten_when_answered(self):
        """Call already answered keeps 'answered' status (answered > voicemail)."""
        call, channel = self._create_call_with_channel(call_status='answered')
        params = self._vm_params(channel.sid)

        self.Call.on_vm_recording_status(params)
        call.invalidate_recordset()

        self.assertEqual(call.status, 'answered',
                         "Answered status must not be downgraded to voicemail")
        # But voicemail_url is still stored
        self.assertTrue(call.voicemail_url)

    def test_voicemail_with_no_matching_channel(self):
        """Webhook for unknown CallSid handles gracefully without error."""
        params = self._vm_params('CA_nonexistent_0000000000000000000')

        # Should not raise
        result = self.Call.on_vm_recording_status(params)
        self.assertTrue(result)

    def test_voicemail_with_channel_but_no_call(self):
        """Channel exists but has no linked call -- no crash."""
        channel = self.Channel.create({
            'sid': 'CA_orphan_vm_00000000000000000000',
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': 'completed',
            'technical_direction': 'inbound',
        })
        params = self._vm_params(channel.sid)

        result = self.Call.on_vm_recording_status(params)
        self.assertTrue(result)


@tagged('post_install', '-at_install')
class TestVoicemailComputedFields(ConnectTestCase):
    """Test voicemail-related computed fields on connect.call."""

    def test_call_result_voicemail(self):
        """call_result is 'voicemail' when voicemail_url is set."""
        call = self._create_test_call(direction='incoming', status='no-answer')
        call.voicemail_url = 'https://api.twilio.com/recordings/test.mp3'
        call.invalidate_recordset()

        self.assertEqual(call.call_result, 'voicemail')
        self.assertFalse(call.is_missed)

    def test_voicemail_icon_present(self):
        """voicemail_icon contains envelope icon when voicemail_url is set."""
        call = self._create_test_call(direction='incoming', status='voicemail')
        call.voicemail_url = 'https://example.com/vm.mp3'
        call.invalidate_recordset()

        self.assertIn('fa-envelope', call.voicemail_icon)

    def test_voicemail_icon_empty(self):
        """voicemail_icon is empty when no voicemail_url."""
        call = self._create_test_call(direction='incoming', status='completed')
        self.assertEqual(call.voicemail_icon, '')

    def test_voicemail_widget_audio_tag(self):
        """voicemail_widget generates <audio> tag when voicemail_url is set."""
        call = self._create_test_call(direction='incoming', status='voicemail')
        call.voicemail_url = 'https://example.com/vm.mp3'
        call.invalidate_recordset()

        self.assertIn('<audio', call.voicemail_widget)

    def test_voicemail_widget_empty(self):
        """voicemail_widget is empty when no voicemail_url."""
        call = self._create_test_call(direction='incoming', status='completed')
        self.assertEqual(call.voicemail_widget, '')


@tagged('post_install', '-at_install')
class TestVoicemailEmail(ConnectTestCase):
    """Test _send_voicemail_email notification flow."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Call = cls.env['connect.call']
        cls.Channel = cls.env['connect.channel']

    def _create_voicemail_call(self):
        """Create a call that looks like a voicemail was left."""
        call = self._create_test_call(
            direction='incoming',
            status='voicemail',
            caller='+15551234567',
            called='+15559876543',
            partner=self.partner_1.id,
        )
        call.voicemail_url = 'https://api.twilio.com/recordings/REvm_email.mp3'
        call.voicemail_duration = 30
        return call

    def test_voicemail_email_sent(self):
        """Voicemail email notification sent to user with voicemail_email_enabled."""
        call = self._create_voicemail_call()

        # Create a dedicated Odoo user for this test (avoid UNIQUE constraint on connect.user)
        test_odoo_user = self.env['res.users'].create({
            'name': 'VM Test User',
            'login': 'vmtestuser',
            'email': 'vmtest@example.com',
        })

        # Create a connect user with voicemail email enabled
        domain = self.env['connect.domain'].search([], limit=1)
        if not domain:
            domain = self.env['connect.domain'].with_context(
                no_twilio_create=True,
            ).create({
                'friendly_name': 'Test Domain',
                'subdomain': 'test',
            })
        connect_user = self.env['connect.user'].with_context(
            no_twilio_create=True,
            no_clear_cache=True,
        ).create({
            'username': 'testvmuser',
            'user': test_odoo_user.id,
            'domain': domain.id,
            'voicemail_enabled': True,
            'voicemail_email_enabled': True,
        })

        # Set the called_users on the call
        call.called_users = [(4, test_odoo_user.id)]

        # Track mail creation via mock since auto_delete=True may remove records
        from unittest.mock import patch
        mail_created = []
        original_create = self.env['mail.mail'].__class__.create

        def track_create(self_inner, vals_list):
            result = original_create(self_inner, vals_list)
            mail_created.append(result)
            return result

        with patch.object(
            self.env['mail.mail'].__class__, 'create', track_create,
        ):
            call._send_voicemail_email()

        self.assertTrue(mail_created,
                        "At least one email should have been created")

    def test_voicemail_email_not_sent_when_disabled(self):
        """No email when voicemail_email_enabled is False."""
        call = self._create_voicemail_call()

        domain = self.env['connect.domain'].search([], limit=1)
        if not domain:
            domain = self.env['connect.domain'].with_context(
                no_twilio_create=True,
            ).create({
                'friendly_name': 'Test Domain 2',
                'subdomain': 'test2',
            })
        connect_user = self.env['connect.user'].create({
            'username': 'testvmnoemail',
            'user': self.env.user.id,
            'domain': domain.id,
            'voicemail_enabled': True,
            'voicemail_email_enabled': False,
        })

        call.called_users = [(4, self.env.user.id)]

        mail_before = self.env['mail.mail'].search_count([])

        call._send_voicemail_email()

        mail_after = self.env['mail.mail'].search_count([])
        self.assertEqual(mail_after, mail_before,
                         "No email should be sent when voicemail_email_enabled is off")

    def test_voicemail_email_skipped_without_url(self):
        """_send_voicemail_email returns early when voicemail_url is empty."""
        call = self._create_test_call(
            direction='incoming', status='no-answer',
        )
        self.assertFalse(call.voicemail_url)

        mail_before = self.env['mail.mail'].search_count([])

        call._send_voicemail_email()

        mail_after = self.env['mail.mail'].search_count([])
        self.assertEqual(mail_after, mail_before)

    def test_voicemail_email_exception_does_not_crash_webhook(self):
        """on_vm_recording_status catches email errors without crashing."""
        call, channel = self._create_call_with_channel_for_email()

        # Patch _send_voicemail_email to raise
        with patch.object(
            self.Call.__class__, '_send_voicemail_email',
            side_effect=Exception('SMTP connection failed'),
        ):
            # Should not raise
            result = self.Call.on_vm_recording_status(
                self._vm_params_for_email(channel.sid))
            self.assertTrue(result)

        # Voicemail data should still be stored
        call.invalidate_recordset()
        self.assertTrue(call.voicemail_url)

    def _create_call_with_channel_for_email(self):
        """Helper for email exception test."""
        call = self._create_test_call(
            direction='incoming', status='no-answer',
            caller='+15551234567', called='+15559876543',
        )
        channel = self.Channel.create({
            'call': call.id,
            'sid': 'CA_vm_email_' + 'e' * 22,
            'caller': '+15551234567',
            'called': '+15559876543',
            'status': 'no-answer',
            'technical_direction': 'inbound',
        })
        return call, channel

    def _vm_params_for_email(self, channel_sid):
        return {
            'CallSid': channel_sid,
            'RecordingUrl': 'https://api.twilio.com/recordings/REvm_exc.mp3',
            'RecordingDuration': '10',
        }


@tagged('post_install', '-at_install')
class TestVoicemailMaxLength(ConnectTestCase):
    """Test voicemail max length setting."""

    def test_voicemail_max_length_setting(self):
        """voicemail_max_length is retrievable from settings."""
        max_len = self.env['connect.settings'].get_param('voicemail_max_length')
        self.assertIsInstance(max_len, int)
        self.assertGreater(max_len, 0)

    def test_voicemail_max_length_default(self):
        """Default voicemail max length is 120 seconds."""
        settings = self.env['connect.settings'].search([], limit=1)
        if not settings:
            settings = self.env['connect.settings'].sudo().create({})
        self.assertEqual(settings.voicemail_max_length, 120)
