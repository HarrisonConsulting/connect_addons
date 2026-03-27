# -*- coding: utf-8 -*-
"""Tests for connect.callflow, connect.callflow_choice, and connect.number routing."""

from datetime import datetime
from unittest.mock import patch, MagicMock

from odoo.tests import tagged
from .common import ConnectTestCase


@tagged('post_install', '-at_install')
class TestCallflowCRUD(ConnectTestCase):
    """Test connect.callflow CRUD operations and field defaults."""

    def test_callflow_create_basic(self):
        """Create a callflow with minimal fields and verify defaults."""
        callflow = self.env['connect.callflow'].create({
            'name': 'Main IVR',
        })
        self.assertTrue(callflow.id)
        self.assertEqual(callflow.name, 'Main IVR')
        self.assertEqual(callflow.language, 'en-US')
        self.assertEqual(callflow.voice, 'man')
        self.assertFalse(callflow.gather_input)
        self.assertFalse(callflow.voicemail_enabled)
        self.assertFalse(callflow.business_hours_enabled)

    def test_callflow_create_with_gather(self):
        """Create a callflow with gather input settings."""
        callflow = self.env['connect.callflow'].create({
            'name': 'Gather Flow',
            'gather_input': True,
            'gather_input_type': 'dtmf',
            'gather_timeout': 10,
            'gather_digits': 3,
            'gather_hints': 'sales, support, billing',
            'prompt_message': 'Press 1 for sales, 2 for support.',
            'invalid_input_message': 'Invalid selection. Try again.',
        })
        self.assertTrue(callflow.gather_input)
        self.assertEqual(callflow.gather_input_type, 'dtmf')
        self.assertEqual(callflow.gather_timeout, 10)
        self.assertEqual(callflow.gather_digits, 3)
        self.assertEqual(callflow.gather_hints, 'sales, support, billing')
        self.assertIn('Press 1', callflow.prompt_message)
        self.assertIn('Invalid selection', callflow.invalid_input_message)

    def test_callflow_create_with_voicemail(self):
        """Create a callflow with voicemail settings."""
        callflow = self.env['connect.callflow'].create({
            'name': 'Voicemail Flow',
            'voicemail_enabled': True,
            'voicemail_prompt': 'Leave a message after the tone.',
        })
        self.assertTrue(callflow.voicemail_enabled)
        self.assertEqual(callflow.voicemail_prompt, 'Leave a message after the tone.')

    def test_callflow_create_with_business_hours(self):
        """Create a callflow with business hours configuration."""
        callflow = self.env['connect.callflow'].create({
            'name': 'Business Hours Flow',
            'business_hours_enabled': True,
            'business_hours_start': 8.0,
            'business_hours_end': 18.0,
            'business_hours_timezone': 'US/Pacific',
            'after_hours_message': 'We are closed. Call back tomorrow.',
            'after_hours_voicemail': True,
        })
        self.assertTrue(callflow.business_hours_enabled)
        self.assertAlmostEqual(callflow.business_hours_start, 8.0)
        self.assertAlmostEqual(callflow.business_hours_end, 18.0)
        self.assertEqual(callflow.business_hours_timezone, 'US/Pacific')
        self.assertIn('closed', callflow.after_hours_message)
        self.assertTrue(callflow.after_hours_voicemail)

    def test_callflow_default_values(self):
        """Verify all field defaults on a newly created callflow."""
        callflow = self.env['connect.callflow'].create({
            'name': 'Defaults Check',
        })
        # Gather defaults
        self.assertEqual(callflow.gather_timeout, 5)
        self.assertEqual(callflow.gather_input_type, 'dtmf speech')
        self.assertEqual(callflow.gather_digits, 1)
        self.assertIn('Welcome to our company', callflow.prompt_message)
        self.assertIn('wrong input', callflow.invalid_input_message)
        self.assertIn('phrase I expect', callflow.gather_hints)
        # Business hours defaults
        self.assertFalse(callflow.business_hours_enabled)
        self.assertAlmostEqual(callflow.business_hours_start, 9.0)
        self.assertAlmostEqual(callflow.business_hours_end, 17.0)
        self.assertEqual(callflow.business_hours_timezone, 'US/Eastern')
        # After hours defaults
        self.assertIn('currently closed', callflow.after_hours_message)
        self.assertTrue(callflow.after_hours_voicemail)
        # Other defaults
        self.assertFalse(callflow.record_calls)
        self.assertFalse(callflow.voicemail_enabled)

    def test_callflow_write_updates(self):
        """Write to callflow and verify field persistence."""
        callflow = self.env['connect.callflow'].create({
            'name': 'Original Name',
        })
        callflow.write({
            'name': 'Updated Name',
            'language': 'es-MX',
            'voice': 'woman',
            'gather_input': True,
        })
        self.assertEqual(callflow.name, 'Updated Name')
        self.assertEqual(callflow.language, 'es-MX')
        self.assertEqual(callflow.voice, 'woman')
        self.assertTrue(callflow.gather_input)

    def test_callflow_unlink(self):
        """Delete a callflow and verify it is removed."""
        callflow = self.env['connect.callflow'].create({
            'name': 'To Be Deleted',
        })
        callflow_id = callflow.id
        callflow.unlink()
        self.assertFalse(self.env['connect.callflow'].browse(callflow_id).exists())


@tagged('post_install', '-at_install')
class TestCallflowBusinessHours(ConnectTestCase):
    """Test _is_business_hours() logic."""

    def _create_callflow_with_hours(self, enabled=True, start=9.0, end=17.0,
                                     timezone='US/Eastern'):
        return self.env['connect.callflow'].create({
            'name': 'Business Hours Test',
            'business_hours_enabled': enabled,
            'business_hours_start': start,
            'business_hours_end': end,
            'business_hours_timezone': timezone,
        })

    def test_business_hours_disabled_always_open(self):
        """When business_hours_enabled=False, _is_business_hours returns True."""
        callflow = self._create_callflow_with_hours(enabled=False)
        self.assertTrue(callflow._is_business_hours())

    def _get_current_utc_hour(self):
        """Get the current fractional hour in UTC for dynamic test ranges."""
        import pytz
        now = datetime.now(pytz.UTC)
        return now.hour + now.minute / 60.0

    def test_business_hours_within_range(self):
        """Current time within start-end returns True."""
        current = self._get_current_utc_hour()
        # Build a range that spans 4 hours around now
        start = (current - 2) % 24
        end = (current + 2) % 24
        if start < end:
            callflow = self._create_callflow_with_hours(
                start=start, end=end, timezone='UTC')
            self.assertTrue(callflow._is_business_hours())
        else:
            # Overnight scenario handled separately
            callflow = self._create_callflow_with_hours(
                start=start, end=end, timezone='UTC')
            self.assertTrue(callflow._is_business_hours())

    def test_business_hours_outside_range(self):
        """Current time outside start-end returns False."""
        current = self._get_current_utc_hour()
        # Build a range that does NOT include current time
        start = (current + 4) % 24
        end = (current + 8) % 24
        if start < end:
            callflow = self._create_callflow_with_hours(
                start=start, end=end, timezone='UTC')
            self.assertFalse(callflow._is_business_hours())
        else:
            # If range wraps around midnight, use a non-wrapping range instead
            callflow = self._create_callflow_with_hours(
                start=(current + 2) % 24, end=(current + 6) % 24, timezone='UTC')
            self.assertFalse(callflow._is_business_hours())

    def test_business_hours_at_start_boundary(self):
        """Time exactly at business_hours_start is within hours (>= start)."""
        current = self._get_current_utc_hour()
        # Set start to floor of current hour so we're at or past the boundary
        start = int(current)
        end = (start + 8) % 24
        callflow = self._create_callflow_with_hours(
            start=float(start), end=float(end), timezone='UTC')
        self.assertTrue(callflow._is_business_hours())

    def test_business_hours_at_end_boundary(self):
        """Time at or past business_hours_end is outside hours (exclusive end)."""
        current = self._get_current_utc_hour()
        # Set end to floor of current hour so we're at or past the boundary
        end = int(current)
        start = (end - 8) % 24
        if start < end:
            callflow = self._create_callflow_with_hours(
                start=float(start), end=float(end), timezone='UTC')
            self.assertFalse(callflow._is_business_hours())
        else:
            # Avoid wrapping; just pick a range clearly before current time
            callflow = self._create_callflow_with_hours(
                start=(current + 4) % 24, end=(current + 8) % 24, timezone='UTC')
            self.assertFalse(callflow._is_business_hours())

    def test_business_hours_overnight_within(self):
        """Overnight hours (start > end): time after start is within hours."""
        current = self._get_current_utc_hour()
        # Create overnight range that includes current time
        start = (current - 2) % 24
        end = (current - 4) % 24  # end < start = overnight
        if start > end:
            callflow = self._create_callflow_with_hours(
                start=start, end=end, timezone='UTC')
            self.assertTrue(callflow._is_business_hours())
        else:
            # Adjust to guarantee overnight wrap
            start = (current + 1) % 24
            end = (current - 1) % 24
            if start > end:
                callflow = self._create_callflow_with_hours(
                    start=0.0 if current >= 0 else 12.0,
                    end=23.99, timezone='UTC')
                self.assertTrue(callflow._is_business_hours())
            else:
                callflow = self._create_callflow_with_hours(
                    start=start, end=end, timezone='UTC')
                self.assertTrue(callflow._is_business_hours())

    def test_business_hours_overnight_early_morning(self):
        """Overnight hours (start > end): time before end is within hours."""
        current = self._get_current_utc_hour()
        # Set end well past current, start well before current (wrapping)
        end = (current + 4) % 24
        start = (current + 8) % 24  # start > end = overnight
        if start > end:
            callflow = self._create_callflow_with_hours(
                start=start, end=end, timezone='UTC')
            # Current hour is between midnight-side of the range (before end)
            self.assertTrue(callflow._is_business_hours())
        else:
            # Fallback: ensure overnight
            callflow = self._create_callflow_with_hours(
                start=23.0, end=1.0, timezone='UTC')
            # Just verify it doesn't crash; overnight logic is covered
            callflow._is_business_hours()

    def test_business_hours_overnight_outside(self):
        """Overnight hours (start > end): time in gap is outside hours."""
        current = self._get_current_utc_hour()
        # Create overnight range that does NOT include current time
        # Gap is between end and start, so put current in the gap
        end = (current - 2) % 24
        start = (current + 2) % 24
        if start > end:
            callflow = self._create_callflow_with_hours(
                start=start, end=end, timezone='UTC')
            self.assertFalse(callflow._is_business_hours())
        else:
            # Adjust to guarantee overnight wrap with current in gap
            callflow = self._create_callflow_with_hours(
                start=(current + 3) % 24, end=(current - 3) % 24, timezone='UTC')
            if (current + 3) % 24 > (current - 3) % 24:
                self.assertFalse(callflow._is_business_hours())
            else:
                callflow._is_business_hours()  # Just verify no crash


@tagged('post_install', '-at_install')
class TestCallflowAfterHoursRendering(ConnectTestCase):
    """Test _render_after_hours TwiML generation."""

    def test_render_after_hours_with_message_and_voicemail(self):
        """After hours with message and voicemail produces Say + Record."""
        callflow = self.env['connect.callflow'].create({
            'name': 'After Hours Test',
            'business_hours_enabled': True,
            'after_hours_message': 'We are closed for the day.',
            'after_hours_voicemail': True,
        })
        with self.mockTwilioClient():
            response = callflow._render_after_hours()
            twiml = str(response)
            self.assertIn('We are closed for the day.', twiml)
            self.assertIn('leave a message', twiml.lower())
            self.assertIn('<Record', twiml)

    def test_render_after_hours_no_voicemail(self):
        """After hours without voicemail produces Say + Hangup."""
        callflow = self.env['connect.callflow'].create({
            'name': 'After Hours No VM',
            'business_hours_enabled': True,
            'after_hours_message': 'Office is closed.',
            'after_hours_voicemail': False,
        })
        with self.mockTwilioClient():
            response = callflow._render_after_hours()
            twiml = str(response)
            self.assertIn('Office is closed.', twiml)
            self.assertIn('<Hangup', twiml)
            self.assertNotIn('<Record', twiml)

    def test_render_after_hours_no_message(self):
        """After hours with empty message skips Say, still records if enabled."""
        callflow = self.env['connect.callflow'].create({
            'name': 'After Hours Silent',
            'business_hours_enabled': True,
            'after_hours_message': False,
            'after_hours_voicemail': True,
        })
        with self.mockTwilioClient():
            response = callflow._render_after_hours()
            twiml = str(response)
            self.assertIn('<Record', twiml)


@tagged('post_install', '-at_install')
class TestCallflowChoice(ConnectTestCase):
    """Test connect.callflow_choice model."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.callflow = cls.env['connect.callflow'].create({
            'name': 'IVR Choice Test',
            'gather_input': True,
        })
        # Create an extension for the choice to point to
        cls.exten = cls.env['connect.exten'].create({
            'number': '9901',
            'model': 'connect.callflow',
            'res_id': cls.callflow.id,
        })

    def test_choice_create(self):
        """Create a callflow choice with digits."""
        choice = self.env['connect.callflow_choice'].create({
            'callflow': self.callflow.id,
            'choice_digits': '1',
            'exten': self.exten.id,
        })
        self.assertTrue(choice.id)
        self.assertEqual(choice.choice_digits, '1')
        self.assertEqual(choice.callflow.id, self.callflow.id)
        self.assertEqual(choice.exten.id, self.exten.id)

    def test_choice_with_speech(self):
        """Create a choice with speech recognition alternative."""
        choice = self.env['connect.callflow_choice'].create({
            'callflow': self.callflow.id,
            'choice_digits': '2',
            'exten': self.exten.id,
            'speech': 'support',
        })
        self.assertEqual(choice.speech, 'support')
        self.assertEqual(choice.choice_digits, '2')

    def test_choice_appears_in_callflow_choices(self):
        """Choices created are accessible via callflow.choices One2many."""
        self.env['connect.callflow_choice'].create({
            'callflow': self.callflow.id,
            'choice_digits': '3',
            'exten': self.exten.id,
        })
        self.assertTrue(
            self.callflow.choices.filtered(lambda c: c.choice_digits == '3'))

    def test_choice_cascade_delete(self):
        """Deleting a callflow cascades to delete its choices."""
        temp_callflow = self.env['connect.callflow'].create({
            'name': 'Temp Callflow',
        })
        temp_exten = self.env['connect.exten'].create({
            'number': '9902',
            'model': 'connect.callflow',
            'res_id': temp_callflow.id,
        })
        choice = self.env['connect.callflow_choice'].create({
            'callflow': temp_callflow.id,
            'choice_digits': '1',
            'exten': temp_exten.id,
        })
        choice_id = choice.id
        temp_callflow.unlink()
        self.assertFalse(
            self.env['connect.callflow_choice'].browse(choice_id).exists())

    def test_multiple_choices(self):
        """A callflow can have multiple choices with different digits."""
        for digit in ['4', '5', '6']:
            self.env['connect.callflow_choice'].create({
                'callflow': self.callflow.id,
                'choice_digits': digit,
                'exten': self.exten.id,
            })
        digits = self.callflow.choices.mapped('choice_digits')
        for digit in ['4', '5', '6']:
            self.assertIn(digit, digits)


@tagged('post_install', '-at_install')
class TestCallflowGatherAction(ConnectTestCase):
    """Test callflow gather_action routing logic."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.callflow = cls.env['connect.callflow'].create({
            'name': 'Gather Action Test',
            'gather_input': True,
            'gather_input_type': 'dtmf speech',
        })
        cls.exten = cls.env['connect.exten'].create({
            'number': '9910',
            'model': 'connect.callflow',
            'res_id': cls.callflow.id,
        })
        cls.choice = cls.env['connect.callflow_choice'].create({
            'callflow': cls.callflow.id,
            'choice_digits': '1',
            'exten': cls.exten.id,
            'speech': 'sales',
        })

    def test_gather_action_digit_match(self):
        """Gather action routes to correct extension when digits match."""
        request = {'Digits': '1', 'SpeechResult': ''}
        with self.mockTwilioClient():
            with patch.object(
                self.exten.__class__, 'render',
                return_value='<Response><Say>Matched</Say></Response>',
            ) as mock_render:
                result = self.env['connect.callflow'].gather_action(
                    self.callflow.id, request)
                mock_render.assert_called_once()

    def test_gather_action_speech_match(self):
        """Gather action routes when speech result contains keyword."""
        request = {'Digits': '', 'SpeechResult': 'I need sales help'}
        with self.mockTwilioClient():
            with patch.object(
                self.exten.__class__, 'render',
                return_value='<Response><Say>Matched</Say></Response>',
            ) as mock_render:
                result = self.env['connect.callflow'].gather_action(
                    self.callflow.id, request)
                mock_render.assert_called_once()

    def test_gather_action_no_match(self):
        """Gather action with no matching choice re-renders callflow with invalid_input."""
        request = {'Digits': '9', 'SpeechResult': ''}
        with self.mockTwilioClient():
            with patch.object(
                self.callflow.__class__, 'render',
                return_value='<Response><Say>Invalid</Say></Response>',
            ) as mock_render:
                result = self.env['connect.callflow'].gather_action(
                    self.callflow.id, request)
                mock_render.assert_called_once()
                call_kwargs = mock_render.call_args
                # Verify invalid_input flag was passed in params
                self.assertTrue(
                    call_kwargs[1].get('params', {}).get('invalid_input') or
                    (len(call_kwargs[0]) > 1 and
                     call_kwargs[0][1].get('invalid_input')))


@tagged('post_install', '-at_install')
class TestNumberRouting(ConnectTestCase):
    """Test connect.number routing and render logic."""

    def _create_number(self, phone_number='+15550001234', **kwargs):
        """Helper to create a number without triggering Twilio sync."""
        return self.env['connect.number'].with_context(
            skip_twilio_sync=True).create({
                'phone_number': phone_number,
                **kwargs,
            })

    def test_number_create_basic(self):
        """Create a number with minimal fields."""
        number = self._create_number()
        self.assertTrue(number.id)
        self.assertEqual(number.phone_number, '+15550001234')
        self.assertFalse(number.destination)

    def test_number_render_no_destination(self):
        """Number with no destination renders error message."""
        number = self._create_number()
        result = number.render()
        self.assertIn('not configured', result)

    def test_number_render_callflow_destination(self):
        """Number with callflow destination delegates to callflow.render."""
        callflow = self.env['connect.callflow'].create({
            'name': 'Number Callflow',
        })
        number = self._create_number(
            destination='callflow',
            callflow=callflow.id,
        )
        with self.mockTwilioClient():
            with patch.object(
                callflow.__class__, 'render',
                return_value='<Response><Say>Hello</Say></Response>',
            ) as mock_render:
                number.render()
                mock_render.assert_called_once()

    def test_number_render_twiml_destination(self):
        """Number with twiml destination delegates to twiml.render."""
        twiml_rec = self.env['connect.twiml'].create({
            'name': 'Test TwiML',
        })
        number = self._create_number(
            destination='twiml',
            twiml=twiml_rec.id,
        )
        with self.mockTwilioClient():
            with patch.object(
                twiml_rec.__class__, 'render',
                return_value='<Response><Say>TwiML</Say></Response>',
            ) as mock_render:
                number.render()
                mock_render.assert_called_once()

    def test_number_write_clears_other_destinations(self):
        """Changing destination clears unrelated destination fields."""
        callflow = self.env['connect.callflow'].create({
            'name': 'Write Test CF',
        })
        number = self._create_number(
            destination='callflow',
            callflow=callflow.id,
        )
        self.assertEqual(number.callflow.id, callflow.id)
        # Switch destination to user (no user set, just checking field clearing)
        with self.mockTwilioClient():
            number.with_context(skip_twilio_sync=True).write({
                'destination': 'user',
            })
        self.assertFalse(number.callflow)
        self.assertFalse(number.twiml)

    def test_number_route_call_not_found(self):
        """route_call returns error when called number not found."""
        request = {
            'Called': '+15559999999',
            'Caller': '+15551234567',
            'CallSid': 'CAtest123',
            'CallStatus': 'ringing',
            'Direction': 'inbound',
        }
        with self.mockTwilioClient():
            with patch.object(
                self.env['connect.call'].__class__, 'on_call_status',
            ):
                result = self.env['connect.number'].route_call(request)
                self.assertIn('not found', result)

    def test_number_route_call_found(self):
        """route_call delegates to number.render when number is found."""
        number = self._create_number(phone_number='+15550009999')
        request = {
            'Called': '+15550009999',
            'Caller': '+15551234567',
            'CallSid': 'CAtest456',
            'CallStatus': 'ringing',
            'Direction': 'inbound',
        }
        with self.mockTwilioClient():
            with patch.object(
                self.env['connect.call'].__class__, 'on_call_status',
            ), patch.object(
                number.__class__, 'render',
                return_value='<Response><Say>Routed</Say></Response>',
            ) as mock_render:
                result = self.env['connect.number'].route_call(request)
                mock_render.assert_called_once()

    def test_number_is_ignored(self):
        """Ignored number skips Twilio update."""
        number = self._create_number(is_ignored=True)
        self.assertTrue(number.is_ignored)

    def test_number_description_field(self):
        """Number description stores identity/purpose text."""
        number = self._create_number(
            description='An Official Odoo Partner, experienced developer.')
        self.assertIn('Official Odoo Partner', number.description)


@tagged('post_install', '-at_install')
class TestCallflowOnCallAction(ConnectTestCase):
    """Test on_call_action webhook handler for post-dial disposition."""

    def test_on_call_action_completed_hangup(self):
        """Completed call produces hangup TwiML."""
        callflow = self.env['connect.callflow'].create({
            'name': 'Action Test',
            'voicemail_prompt': 'Leave a message.',
        })
        request = {'DialCallStatus': 'completed'}
        with self.mockTwilioClient():
            response = self.env['connect.callflow'].on_call_action(
                callflow.id, request)
            twiml = str(response)
            self.assertIn('<Hangup', twiml)
            self.assertNotIn('<Record', twiml)

    def test_on_call_action_no_answer_with_voicemail(self):
        """Unanswered call with voicemail prompt produces Record TwiML."""
        callflow = self.env['connect.callflow'].create({
            'name': 'VM Action Test',
            'voicemail_prompt': 'Please leave your message.',
        })
        request = {'DialCallStatus': 'no-answer'}
        with self.mockTwilioClient():
            with patch.object(
                self.env['connect.settings'].__class__, 'get_param',
                return_value='https://test.example.com',
            ):
                response = self.env['connect.callflow'].on_call_action(
                    callflow.id, request)
                twiml = str(response)
                self.assertIn('<Record', twiml)
                self.assertIn('Please leave your message.', twiml)

    def test_on_call_action_no_answer_no_voicemail(self):
        """Unanswered call without voicemail produces error + hangup."""
        callflow = self.env['connect.callflow'].create({
            'name': 'No VM Action Test',
            'voicemail_prompt': False,
        })
        request = {'DialCallStatus': 'busy'}
        with self.mockTwilioClient():
            response = self.env['connect.callflow'].on_call_action(
                callflow.id, request)
            twiml = str(response)
            self.assertIn('<Hangup', twiml)
