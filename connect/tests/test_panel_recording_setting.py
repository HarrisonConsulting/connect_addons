# -*- coding: utf-8 -*-
"""Recording from the phone panel stays off until a company turns it on.

Nothing here depends on a provider being installed. Each provider's own proof
that it records nothing while off is in its TestRecordingControls.
"""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

FAKE = 'recording_fake'
RECORDING = {
    'recording_state', 'recording_start', 'recording_pause',
    'recording_resume', 'recording_stop',
}


@tagged('post_install', '-at_install')
class TestPanelRecordingSetting(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Channel = self.env['connect.channel']
        self.env.company.connect_panel_recording = False
        self.called = []
        for action in ('state', 'start', 'pause', 'resume', 'stop'):
            patcher = patch.object(
                type(self.Channel), '_softphone_recording_%s_%s' % (action, FAKE),
                lambda model, payload, action=action: self.called.append(action) or {'state': 'on'},
                create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _on(self):
        return {key for key, on in self.Channel.softphone_capabilities(FAKE).items() if on}

    def _payload(self):
        return {'provider': FAKE, 'channel_sid': 'CAx'}

    def test_a_new_company_starts_off(self):
        company = self.env['res.company'].create({'name': 'New recording company'})
        self.assertFalse(company.connect_panel_recording)

    def test_off_offers_no_recording_at_all(self):
        self.assertFalse(self._on() & RECORDING)

    def test_a_provider_hook_cannot_offer_it_while_off(self):
        self.patch(type(self.Channel), '_softphone_capabilities_narrow',
                   lambda model, provider, caps: dict.fromkeys(caps, True))
        self.assertFalse(self._on() & RECORDING)

    def test_on_offers_what_the_provider_has(self):
        self.env.company.connect_panel_recording = True
        self.assertEqual(self._on() & RECORDING, RECORDING)

    def test_off_refuses_every_start_in_words_and_reaches_no_provider(self):
        for method in ('start_softphone_recording', 'resume_softphone_recording'):
            with self.subTest(method=method), self.assertRaisesRegex(UserError, 'turned off'):
                getattr(self.Channel, method)(self._payload())
        self.assertEqual(self.called, [])

    def test_off_holds_for_an_elevated_caller(self):
        with self.assertRaisesRegex(UserError, 'turned off'):
            self.Channel.sudo().start_softphone_recording(self._payload())
        self.assertEqual(self.called, [])

    def test_off_reports_unavailable_without_asking_the_provider(self):
        result = self.Channel.get_softphone_recording_state(self._payload())
        self.assertFalse(result['supported'])
        self.assertEqual(result['state'], 'off')
        self.assertIn('turned off', result['error'])
        self.assertEqual(self.called, [])

    def test_a_recording_can_still_be_ended_after_it_is_turned_off(self):
        self.Channel.pause_softphone_recording(self._payload())
        self.Channel.stop_softphone_recording(self._payload())
        self.assertEqual(self.called, ['pause', 'stop'])

    def test_on_reaches_the_provider(self):
        self.env.company.connect_panel_recording = True
        self.Channel.start_softphone_recording(self._payload())
        self.assertEqual(self.called, ['start'])

    def test_it_belongs_to_one_company(self):
        other = self.env['res.company'].create({'name': 'Other recording company'})
        other.connect_panel_recording = True
        self.assertFalse(self.Channel.with_company(self.env.company)._softphone_recording_allowed())
        self.assertTrue(self.Channel.with_company(other)._softphone_recording_allowed())

    def test_the_settings_screen_writes_only_its_own_company(self):
        other = self.env['res.company'].create({'name': 'Other recording company'})
        self.env.user.company_ids |= other
        settings = self.env['res.config.settings'].with_company(other).create({})
        settings.connect_panel_recording = True
        settings.execute()
        self.assertTrue(other.connect_panel_recording)
        self.assertFalse(self.env.company.connect_panel_recording)

    def test_the_setting_says_it_needs_consent_wording_and_approval(self):
        for model in ('res.company', 'res.config.settings'):
            words = self.env[model]._fields['connect_panel_recording'].help
            self.assertIn('consent wording and approval', words)

    def test_automatic_recording_answers_the_same_either_way(self):
        Settings = self.env['connect.settings'].sudo()
        recorded = self.env['connect.user'].new({'record_calls': True})
        unrecorded = self.env['connect.user'].new({'record_calls': False})
        for all_calls in (True, False):
            Settings.set_param('record_all_calls', all_calls)
            answers = set()
            for setting in (False, True):
                self.env.company.connect_panel_recording = setting
                answers.add((
                    Settings.calls_are_recorded(user=recorded),
                    Settings.calls_are_recorded(user=unrecorded),
                ))
            self.assertEqual(answers, {(True, all_calls)})
