# -*- coding: utf-8 -*-
"""Recording sync keeps the timestamps Twilio reports."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRecordingSync(TransactionCase):

    def _twilio_record(self, start_time):
        return SimpleNamespace(
            sid='RE_sync_test',
            call_sid='CA_sync_test_no_channel',
            media_url='https://api.twilio.com/recording',
            price=None,
            price_unit='USD',
            duration='12',
            source='DialVerb',
            start_time=start_time,
            status='completed',
        )

    def test_aware_start_time_is_stored_as_naive_utc(self):
        """A tz-aware Twilio start time is stored as naive UTC unchanged."""
        data = self.env['connect.recording'].prepare_data(
            self._twilio_record(
                datetime(2026, 10, 3, 22, 14, 51, tzinfo=timezone.utc)
            )
        )
        self.assertEqual(data['start_time'], datetime(2026, 10, 3, 22, 14, 51))

    def test_offset_start_time_is_converted_to_utc(self):
        """A non-UTC aware value is converted to UTC before the tz is dropped."""
        offset = timezone(timedelta(hours=-5))
        data = self.env['connect.recording'].prepare_data(
            self._twilio_record(datetime(2026, 10, 3, 17, 14, 51, tzinfo=offset))
        )
        self.assertEqual(data['start_time'], datetime(2026, 10, 3, 22, 14, 51))

    def test_missing_start_time_is_false(self):
        """A missing Twilio start time stores False."""
        data = self.env['connect.recording'].prepare_data(
            self._twilio_record(None)
        )
        self.assertIs(data['start_time'], False)
