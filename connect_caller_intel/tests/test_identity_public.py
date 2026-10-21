import os
import tempfile
from unittest import mock

from odoo.tests import TransactionCase, tagged

from odoo.addons.queue_job.exception import RetryableJobError

from ..services.adapters import base
from ..services.adapters.base import AdapterThrottled
from ..services.adapters.registry import ADAPTERS

SMITHSONIAN = '+12026337970'
LONDON = '+442079460958'

WIKIDATA_CSV = (
    'item,tel\n'
    'http://www.wikidata.org/entity/Q1192305,"+1 202-633-7970"\n'
    'http://www.wikidata.org/entity/Q876537,"+1 (202) 633-7970"\n'
    'http://www.wikidata.org/entity/Q42,"+44 20 7946 0958"\n'
    'http://www.wikidata.org/entity/Q7,"not a number"\n'
    'http://www.wikidata.org/entity/Q8,"202-633-7970"\n')
OSM_CSV = (
    'phone,osm_type,osm_id,name,lat,lon\n'
    '+1 202-633-1000;202 633 7970,node,611558742,Smithsonian American Art Museum,38.89,-77.02\n'
    '+1-202-633-7970,way,26765042,National Museum of Asian Art,38.88,-77.03\n'
    '+1 202-633-7970,node,1,,38.0,-77.0\n')


def _text(body, status=200):
    resp = mock.Mock()
    resp.status_code = status
    resp.text = body
    resp.json.return_value = {}
    resp.raise_for_status.return_value = None
    return resp


def _labels(**qids):
    resp = _text('')
    resp.json.return_value = {'entities': {
        qid: {'labels': {'en': {'value': label}}} for qid, label in qids.items()}}
    return resp


@tagged('post_install', '-at_install')
class TestPublicIdentity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Source = cls.env['connect.caller.source']
        cls.Rep = cls.env['connect.caller.reputation']
        cls.Wikidata = cls.env['connect.caller.wikidata']
        cls.Osm = cls.env['connect.caller.osm']
        cls.wikidata = cls.Source.search([('code', '=', 'wikidata')], limit=1)
        cls.osm = cls.Source.search([('code', '=', 'osm')], limit=1)
        cls.wikidata.enabled = True
        cls.osm.with_context(caller_intel_cost_ack=True).enabled = True
        cls.Wikidata.search([]).unlink()
        cls.Osm.search([]).unlink()
        cls.Wikidata.create({'e164': SMITHSONIAN, 'qid': 'Q1192305'})
        cls.Osm.create({'e164': SMITHSONIAN, 'osm_type': 'node', 'osm_id': 611558742,
                        'name': 'Smithsonian American Art Museum'})
        cls.rep = cls.Rep.create({'e164': SMITHSONIAN, 'line_type': 'fixed'})
        base.reset_warnings()

    def test_seed_attribution(self):
        self.assertEqual(self.wikidata.attribution, 'Wikidata (CC0)')
        self.assertEqual(self.osm.attribution, '© OpenStreetMap contributors')

    def test_wikidata_label_fetched_once_and_identity_attributed(self):
        with mock.patch('requests.get', return_value=_labels(Q1192305='Smithsonian American Art Museum')) as get:
            self.assertEqual(self.rep._refresh_source('wikidata'), 'found')
            self.assertEqual(get.call_count, 1)
            self.assertIn('HarrisonConnectCallerIntel', get.call_args.kwargs['headers']['User-Agent'])
        identity = self.rep.identity_ids
        self.assertEqual((identity.name, identity.external_ref, identity.attribution),
                         ('Smithsonian American Art Museum', 'Q1192305', 'Wikidata (CC0)'))
        self.assertEqual(identity.url, 'https://www.wikidata.org/wiki/Q1192305')
        self.assertEqual(identity.confidence, 0.3)
        self.assertEqual(self.rep.candidate_business_name, 'Smithsonian American Art Museum')
        with mock.patch('requests.get') as get:
            ADAPTERS['wikidata'].lookup(self.env, SMITHSONIAN)
            get.assert_not_called()

    def test_wikidata_user_agent_uses_configured_contact(self):
        self.env['ir.config_parameter'].set_param('connect_caller_intel.http_contact', 'ops@example.com')
        with mock.patch('requests.get', return_value=_labels(Q1192305='X')) as get:
            self.rep._refresh_source('wikidata')
        self.assertIn('ops@example.com', get.call_args.kwargs['headers']['User-Agent'])

    def test_unmatched_number_makes_no_request_and_caches_not_found(self):
        rep = self.Rep.create({'e164': '+12025550100'})
        with mock.patch('requests.get') as get:
            self.assertEqual(rep._refresh_source('wikidata'), 'not_found')
            get.assert_not_called()
        self.assertTrue(rep._source_is_fresh(self.wikidata, self.env.cr.now()))

    def test_throttled_label_fetch_retries(self):
        with mock.patch.object(type(self.Source), '_caller_intel_throttle',
                               side_effect=AdapterThrottled('paced', seconds=2)), \
                mock.patch('requests.get') as get:
            with self.assertRaises(RetryableJobError):
                self.rep._refresh_source('wikidata')
            get.assert_not_called()
        self.assertFalse(self.rep._source_is_fresh(self.wikidata, self.env.cr.now()))

    def test_partner_matched_and_mobile_numbers_are_never_queried(self):
        self.env['res.partner'].create({'name': 'Known', 'phone': SMITHSONIAN})
        with mock.patch('requests.get') as get:
            self.assertEqual(self.rep._refresh_source('wikidata'), 'skipped')
            self.assertEqual(self.rep._refresh_source('osm'), 'skipped')
            get.assert_not_called()
        mobile = self.Rep.create({'e164': '+12025550111', 'line_type': 'mobile'})
        self.Wikidata.create({'e164': mobile.e164, 'qid': 'Q99'})
        with mock.patch('requests.get') as get:
            self.assertEqual(mobile._refresh_source('wikidata'), 'skipped')
            self.assertEqual(mobile._refresh_source('osm'), 'skipped')
            get.assert_not_called()
        self.assertFalse(mobile.identity_ids)

    def test_non_nanp_number_skips_osm_and_runs_wikidata(self):
        london = self.Rep.create({'e164': LONDON, 'line_type': 'fixed'})
        self.Wikidata.create({'e164': LONDON, 'qid': 'Q42'})
        self.assertFalse(ADAPTERS['osm'].applies_to(self.env, london))
        self.assertTrue(ADAPTERS['wikidata'].applies_to(self.env, london))
        with mock.patch('requests.get', return_value=_labels(Q42='Douglas Adams Institute')):
            self.assertEqual(london._refresh_source('wikidata'), 'found')
        self.assertEqual(london._refresh_source('osm'), 'skipped')

    def test_osm_identity_from_local_extract(self):
        with mock.patch('requests.get') as get:
            self.assertEqual(self.rep._refresh_source('osm'), 'found')
            get.assert_not_called()
        identity = self.rep.identity_ids.filtered(lambda i: i.source_id == self.osm)
        self.assertEqual((identity.external_ref, identity.attribution, identity.url),
                         ('node/611558742', '© OpenStreetMap contributors',
                          'https://www.openstreetmap.org/node/611558742'))

    def test_empty_osm_table_leaves_source_unavailable(self):
        self.Osm.search([]).unlink()
        base.reset_warnings()
        with mock.patch('requests.get') as get:
            self.assertEqual(self.rep._refresh_source('osm'), 'skipped')
            get.assert_not_called()
        self.assertFalse(self.rep._source_is_fresh(self.osm, self.env.cr.now()))

    def test_wikidata_bulk_import_replaces_table_and_keeps_labels(self):
        self.Wikidata.search([('qid', '=', 'Q1192305')]).label = 'Kept label'
        with mock.patch('requests.get', return_value=_text(WIKIDATA_CSV)) as get:
            self.assertEqual(ADAPTERS['wikidata'].import_bulk(self.env), 3)
            self.assertEqual(get.call_args.kwargs['headers']['Accept'], 'text/csv')
        rows = {(r.e164, r.qid): r.label for r in self.Wikidata.search([])}
        self.assertEqual(rows, {(SMITHSONIAN, 'Q1192305'): 'Kept label',
                                (SMITHSONIAN, 'Q876537'): False, (LONDON, 'Q42'): False})

    def test_wikidata_bulk_import_of_an_empty_export_keeps_the_table(self):
        with mock.patch('requests.get', return_value=_text('item,tel\n')):
            self.assertEqual(ADAPTERS['wikidata'].import_bulk(self.env), 0)
        self.assertEqual(self.Wikidata.search_count([]), 1)

    def test_osm_import_from_file_and_url(self):
        params = self.env['ir.config_parameter']
        adapter = ADAPTERS['osm']
        self.assertEqual(adapter.import_extract(self.env), 0)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'extract.csv')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(OSM_CSV)
            params.set_param('connect_caller_intel.osm_extract', path)
            self.assertEqual(adapter.import_extract(self.env), 3)
        rows = {(r.e164, r.osm_type, r.osm_id) for r in self.Osm.search([])}
        self.assertEqual(rows, {('+12026331000', 'node', 611558742), (SMITHSONIAN, 'node', 611558742),
                                (SMITHSONIAN, 'way', 26765042)})
        params.set_param('connect_caller_intel.osm_extract', 'https://extracts.example.com/us.csv')
        with mock.patch('requests.get', return_value=_text(OSM_CSV)) as get:
            self.assertEqual(adapter.import_extract(self.env), 3)
            self.assertEqual(get.call_args.args[0], 'https://extracts.example.com/us.csv')
