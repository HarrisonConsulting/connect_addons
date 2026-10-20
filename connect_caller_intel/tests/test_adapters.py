from unittest import mock

import requests

from odoo.tests import TransactionCase, tagged

from odoo.addons.queue_job.exception import RetryableJobError

from ..services.adapters import base
from ..services.adapters.registry import ADAPTERS


def _response(status=200, payload=None):
    resp = mock.Mock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status.side_effect = (
        requests.HTTPError(str(status)) if status >= 400 else None)
    return resp


@tagged('post_install', '-at_install')
class TestCallerIntelAdapters(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Source = cls.env['connect.caller.source']
        cls.Rep = cls.env['connect.caller.reputation']
        cls.fcc = cls.Source.search([('code', '=', 'fcc_complaints')], limit=1)
        cls.lookup = cls.Source.search([('code', '=', 'twilio_lookup_line_type')], limit=1)
        cls.fcc.enabled = True
        cls.rep = cls.Rep.create({'e164': '+14023827125'})
        base.reset_warnings()

    def test_fcc_found_creates_mark_and_caches(self):
        rows = [{'date_created': '2016-06-02T14:25:15.000', 'issue': 'Robocalls'},
                {'date_created': '2016-07-02T14:25:15.000', 'issue': 'Robocalls'}]
        with mock.patch('requests.get', return_value=_response(200, rows)) as get:
            self.assertEqual(self.rep._refresh_source('fcc_complaints'), 'found')
        self.assertEqual(get.call_args.kwargs['params']['$where'].split(' AND ')[0],
                         "caller_id_number='402-382-7125'")
        mark = self.rep.mark_ids
        self.assertEqual((mark.report_count, mark.category, mark.state), (2, 'robocall', 'active'))
        self.assertEqual(self.rep.source_checked_json['fcc_complaints']['outcome'], 'found')

    def test_fcc_negative_result_is_cached(self):
        with mock.patch('requests.get', return_value=_response(200, [])):
            self.assertEqual(self.rep._refresh_source('fcc_complaints'), 'not_found')
        self.assertTrue(self.rep._source_is_fresh(self.fcc, self.env.cr.now()))

    def test_transient_failure_retries_and_is_not_cached(self):
        with mock.patch('requests.get', return_value=_response(503, None)):
            with self.assertRaises(RetryableJobError):
                self.rep._refresh_source('fcc_complaints')
        self.assertFalse(self.rep._source_is_fresh(self.fcc, self.env.cr.now()))

    def test_enqueue_dedups_by_identity_key(self):
        key = 'caller_intel:+14023827125:fcc_complaints'
        jobs = self.env['queue.job']
        before = jobs.search_count([('identity_key', '=', key)])
        self.rep._enqueue_stale_sources()
        self.rep._enqueue_stale_sources()
        self.assertEqual(jobs.search_count([('identity_key', '=', key)]) - before, 1)

    def test_paid_adapter_never_called_while_disabled(self):
        self.lookup.sudo().write({'enabled': False})
        with mock.patch('requests.get') as get:
            self.assertEqual(self.rep._refresh_source('twilio_lookup_line_type'), 'skipped')
            self.rep._enqueue_stale_sources()
        get.assert_not_called()
        self.assertNotIn('twilio_lookup_line_type',
                         self.rep._usable_async_sources().mapped('code'))

    def test_missing_key_is_unavailable_with_one_warning(self):
        self.env['ir.config_parameter'].sudo().set_param('connect_caller_intel.ftc_api_key', '')
        adapter = ADAPTERS['ftc_dnc']
        with self.assertLogs(base._logger.name, level='WARNING') as logs:
            for _ in range(3):
                self.assertFalse(adapter.available(self.env))
        self.assertEqual(len(logs.records), 1)

    def test_ftc_bulk_load_aggregates_by_number(self):
        ftc = self.Source.search([('code', '=', 'ftc_dnc')], limit=1)
        ftc.enabled = True
        self.env['ir.config_parameter'].sudo().set_param('connect_caller_intel.ftc_api_key', 'k')
        payload = {'data': [
            {'attributes': {'company-phone-number': '(555) 010-0199',
                            'created-date': '2026-10-15 10:00:00',
                            'recorded-message-or-robocall': 'Y'}},
            {'attributes': {'company-phone-number': '5550100199',
                            'created-date': '2026-10-15 11:00:00',
                            'recorded-message-or-robocall': 'N'}},
        ]}
        adapter = ADAPTERS['ftc_dnc']
        with mock.patch('requests.get', return_value=_response(200, payload)):
            found = adapter.fetch_day(self.env, '2026-10-15')
        self.assertEqual(found['+15550100199']['count'], 2)
        self.assertTrue(found['+15550100199']['robocall'])
        self.Rep._ftc_upsert(ftc, '+15550100199', None, found['+15550100199'])
        rep = self.Rep.search([('e164', '=', '+15550100199')])
        self.assertEqual(rep.mark_ids.report_count, 2)
        self.assertTrue(adapter.lookup(self.env, '+15550100199')['found'])

    def test_twilio_lookup_maps_line_type_when_enabled(self):
        self.lookup.sudo().write({'enabled': True})
        settings = self.env['connect.settings']
        payload = {'line_type_intelligence': {'type': 'nonFixedVoip', 'carrier_name': 'Acme'}}
        with mock.patch.object(type(settings), 'get_param', return_value='x'), \
                mock.patch('requests.get', return_value=_response(200, payload)) as get:
            self.rep._refresh_source('twilio_lookup_line_type')
        get.assert_called_once()
        self.assertEqual((self.rep.line_type, self.rep.carrier_name), ('voip', 'Acme'))
