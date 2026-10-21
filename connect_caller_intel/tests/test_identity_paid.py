from unittest import mock

import requests

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..services.adapters import base
from ..services.adapters.registry import ADAPTERS

PARAMS = {
    'google_places': 'connect_caller_intel.google_places_api_key',
    'telnyx_lookup': 'connect_caller_intel.telnyx_api_key',
    'ipqs': 'connect_caller_intel.ipqs_api_key',
    'trestle': 'connect_caller_intel.trestle_api_key',
}
KEY = 'SECRETKEY123'


def _response(status=200, payload=None):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status.side_effect = (
        requests.HTTPError('%s for url: https://x/%s/y' % (status, KEY)) if status >= 400 else None)
    return resp


@tagged('post_install', '-at_install')
class TestPaidIdentity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Source = cls.env['connect.caller.source']
        cls.Rep = cls.env['connect.caller.reputation']
        cls.sources = {code: cls.Source.search([('code', '=', code)], limit=1) for code in PARAMS}
        params = cls.env['ir.config_parameter'].sudo()
        for param in PARAMS.values():
            params.set_param(param, KEY)
        cls.rep = cls.Rep.create({'e164': '+16015550100', 'country_code': 'US'})
        base.reset_warnings()

    def _enable(self, code):
        source = self.sources[code]
        if source.requires_purpose_ack:
            source.action_enable_with_purpose()
        else:
            source.action_enable()
        return source

    def test_seeded_off_with_attribution(self):
        for code, source in self.sources.items():
            self.assertTrue(source, code)
            self.assertFalse(source.enabled, code)
            self.assertEqual(source.tier, 'async')
            self.assertTrue(source.attribution, code)
            self.assertTrue(source.cost_note, code)
            self.assertTrue(source.adapter_available, code)
            self.assertEqual(source.credential_param, PARAMS[code])
        self.assertEqual(self.sources['ipqs'].cost_class, 'freemium')
        self.assertIn('$35', self.sources['google_places'].cost_note)
        self.assertIn('0.003', self.sources['telnyx_lookup'].cost_note)

    def test_disabled_sources_make_no_http_call(self):
        with mock.patch('requests.get') as get, mock.patch('requests.post') as post:
            self.rep._enqueue_stale_sources()
            for code in PARAMS:
                self.assertEqual(self.rep._refresh_source(code), 'skipped')
        get.assert_not_called()
        post.assert_not_called()
        self.assertFalse(set(PARAMS) & set(self.rep._usable_async_sources().mapped('code')))

    def test_enable_needs_cost_acknowledgement(self):
        source = self.sources['telnyx_lookup']
        with self.assertRaises(UserError):
            source.write({'enabled': True})
        source.action_enable()
        self.assertTrue(source.enabled)
        self.assertEqual(source.cost_acknowledged_by, self.env.user)
        self.assertFalse(source.purpose_acknowledged_by)

    def test_trestle_needs_purpose_acknowledgement(self):
        source = self.sources['trestle']
        with self.assertRaises(UserError):
            source.action_enable()
        self.assertFalse(source.enabled)
        source.action_enable_with_purpose()
        self.assertTrue(source.enabled)
        self.assertEqual(source.cost_acknowledged_by, self.env.user)
        self.assertEqual(source.purpose_acknowledged_by, self.env.user)
        self.assertTrue(source.purpose_acknowledged_at)

    def test_google_places_stores_only_the_place_id(self):
        self._enable('google_places')
        payload = {'places': [{
            'id': 'ChIJabc', 'displayName': {'text': 'Acme Plumbing'},
            'formattedAddress': '1 Main St, Jackson, MS',
            'internationalPhoneNumber': '+1 601-555-0100'}]}
        with mock.patch('requests.post', return_value=_response(200, payload)) as post:
            self.assertEqual(self.rep._refresh_source('google_places'), 'found')
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs['headers']['X-Goog-Api-Key'], KEY)
        self.assertIn('places.internationalPhoneNumber', kwargs['headers']['X-Goog-FieldMask'])
        self.assertEqual(kwargs['json'], {'textQuery': '+16015550100', 'regionCode': 'US'})
        identity = self.rep.identity_ids
        self.assertEqual(identity.external_ref, 'ChIJabc')
        self.assertEqual(identity.name, 'ChIJabc')
        self.assertFalse(identity.address)
        self.assertEqual(identity.attribution, 'Google Maps')
        self.assertEqual(identity.url, 'https://www.google.com/maps/place/?q=place_id:ChIJabc')
        self.assertNotIn('Acme Plumbing', identity.mapped('name'))
        self.assertFalse(self.rep.candidate_business_name)

    def test_google_places_rejects_a_place_with_another_phone(self):
        self._enable('google_places')
        payload = {'places': [{
            'id': 'ChIJxyz', 'displayName': {'text': 'Other Co'},
            'internationalPhoneNumber': '+1 601-555-9999'}]}
        with mock.patch('requests.post', return_value=_response(200, payload)):
            self.assertEqual(self.rep._refresh_source('google_places'), 'not_found')
        self.assertFalse(self.rep.identity_ids)

    def test_telnyx_sets_carrier_line_type_and_cnam(self):
        self._enable('telnyx_lookup')
        payload = {'data': {'carrier': {'name': 'Verizon', 'type': 'mobile'},
                            'caller_name': {'caller_name': 'ACME CORP'}}}
        with mock.patch('requests.get', return_value=_response(200, payload)) as get:
            self.assertEqual(self.rep._refresh_source('telnyx_lookup'), 'found')
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer ' + KEY)
        self.assertEqual((self.rep.carrier_name, self.rep.line_type, self.rep.cnam_last),
                         ('Verizon', 'mobile', 'ACME CORP'))

    def test_ipqs_high_score_records_a_scam_mark(self):
        self._enable('ipqs')
        payload = {'success': True, 'valid': True, 'fraud_score': 92, 'line_type': 'VOIP',
                   'carrier': 'Bandwidth', 'VOIP': True}
        with mock.patch('requests.get', return_value=_response(200, payload)) as get:
            self.assertEqual(self.rep._refresh_source('ipqs'), 'found')
        self.assertIn(KEY, get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs['params']['country[]'], 'US')
        mark = self.rep.mark_ids
        self.assertEqual((mark.reporter, mark.category, mark.report_count, mark.external_ref),
                         ('IPQualityScore reputation', 'scam', 1, 'ipqs:+16015550100'))
        self.assertEqual(self.rep.line_type, 'voip')
        self.assertAlmostEqual(self.rep.spam_score, 0.5 * 0.1, places=3)

    def test_ipqs_low_score_records_no_mark(self):
        self._enable('ipqs')
        payload = {'success': True, 'valid': True, 'fraud_score': 40, 'recent_abuse': False,
                   'spammer': False, 'line_type': 'Landline', 'carrier': 'AT&T'}
        with mock.patch('requests.get', return_value=_response(200, payload)):
            self.rep._refresh_source('ipqs')
        self.assertFalse(self.rep.mark_ids)
        self.assertEqual(self.rep.line_type, 'fixed')

    def test_ipqs_spammer_flag_records_a_spam_mark(self):
        self._enable('ipqs')
        payload = {'success': True, 'valid': True, 'fraud_score': 40, 'spammer': True}
        with mock.patch('requests.get', return_value=_response(200, payload)):
            self.rep._refresh_source('ipqs')
        self.assertEqual(self.rep.mark_ids.category, 'spam')

    def test_ipqs_threshold_is_a_setting(self):
        self._enable('ipqs')
        self.env['ir.config_parameter'].sudo().set_param('connect_caller_intel.ipqs_mark_from', '30')
        payload = {'success': True, 'valid': True, 'fraud_score': 40}
        with mock.patch('requests.get', return_value=_response(200, payload)):
            self.rep._refresh_source('ipqs')
        self.assertEqual(self.rep.mark_ids.category, 'spam')

    def test_trestle_hint_is_never_stored(self):
        source = self._enable('trestle')
        self.assertEqual(source.ttl_hours, 0)
        payload = {'belongs_to': [{'name': 'Jane Roe', 'type': 'Person'}],
                   'current_addresses': [{'street_line_1': '2 Elm St', 'city': 'Jackson',
                                          'state_code': 'MS', 'postal_code': '39201'}]}
        with mock.patch('requests.get', return_value=_response(200, payload)) as get:
            self.assertEqual(self.rep._refresh_source('trestle'), 'found')
            self.assertFalse(self.rep._source_is_fresh(source, fields.Datetime.now()))
        self.assertEqual(get.call_args.kwargs['headers']['x-api-key'], KEY)
        self.assertEqual(get.call_args.kwargs['params']['phone'], '6015550100')
        self.assertTrue(source.no_store)
        self.assertFalse(self.rep.identity_ids)
        self.assertFalse(self.rep.candidate_business_name)
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'Jane Roe')]))
        with mock.patch('requests.get', return_value=_response(200, payload)) as again:
            self.rep._enqueue_stale_sources()
            self.rep._refresh_source('trestle')
        again.assert_called_once()

    def test_rejected_key_raises_and_warns_with_the_parameter_only(self):
        self._enable('telnyx_lookup')
        with mock.patch('requests.get', return_value=_response(401, None)):
            with self.assertLogs('odoo.addons.connect_caller_intel.services.adapters.base',
                                 level='WARNING') as logs:
                with self.assertRaises(Exception):
                    ADAPTERS['telnyx_lookup'].lookup(self.env, '+16015550100')
        self.assertEqual(len(logs.records), 1)
        message = logs.output[0]
        self.assertIn(PARAMS['telnyx_lookup'], message)
        self.assertNotIn(KEY, message)

    def test_ipqs_errors_never_carry_the_key(self):
        self._enable('ipqs')
        for response in (_response(401, None), _response(404, None)):
            with mock.patch('requests.get', return_value=response):
                with self.assertRaises(base.AdapterError) as caught:
                    ADAPTERS['ipqs'].lookup(self.env, '+16015550100')
            self.assertNotIn(KEY, str(caught.exception))

    def test_trestle_skips_non_nanp_and_ipqs_runs(self):
        self._enable('trestle')
        self._enable('ipqs')
        uk = self.Rep.create({'e164': '+442071234567', 'country_code': 'GB'})
        payload = {'success': True, 'valid': True, 'fraud_score': 10}
        with mock.patch('requests.get', return_value=_response(200, payload)) as get:
            self.assertEqual(uk._refresh_source('trestle'), 'skipped')
            self.assertEqual(uk._refresh_source('ipqs'), 'found')
        get.assert_called_once()
        self.assertIn('ipqualityscore', get.call_args.args[0])
