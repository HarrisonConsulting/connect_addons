import io
from datetime import date, datetime
from unittest import mock

import requests

from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open

from ..models import nxx as nxx_module
from ..services import nanpa, numbering

UTILIZED = 'connect_caller_intel/tests/data/nanpa_utilized_sample.txt'
AVAILABLE = 'connect_caller_intel/tests/data/nanpa_available_sample.txt'
OWN_DID = '+13125551234'
FIRST_LOAD = datetime(2026, 10, 20, 1, 0, 0)
SECOND_LOAD = datetime(2026, 10, 21, 1, 0, 0)


def _lines(path):
    with file_open(path, 'r', filter_ext=('.txt',)) as handle:
        return handle.read().splitlines(keepends=True)


@tagged('post_install', '-at_install')
class TestNanpaParsing(TransactionCase):

    def test_utilized_rows(self):
        """The utilized file parses assigned and unavailable codes with typed values."""
        rows = {r['npa_nxx']: r for r in nanpa.parse_utilized(_lines(UTILIZED))}
        self.assertEqual(set(rows), {'601605', '601555', '601777', '907211'})
        ridgeland = rows['601555']
        self.assertEqual(
            (ridgeland['state_code'], ridgeland['status'], ridgeland['in_service'], ridgeland['pooled']),
            ('MS', 'assigned', True, True))
        self.assertEqual(ridgeland['company'], 'CELLCO PARTNERSHIP DBA VERIZON WIRELESS - MS')
        self.assertEqual(ridgeland['effective_date'], date(2012, 3, 15))
        self.assertIsNone(rows['601605']['effective_date'])
        self.assertFalse(rows['601777']['in_service'])
        self.assertEqual(rows['907211']['status'], 'unavailable')

    def test_available_rows(self):
        """The available file gives available codes and skips the header and blank lines."""
        rows = list(nanpa.parse_available(_lines(AVAILABLE) + ['\n']))
        self.assertEqual({r['npa_nxx'] for r in rows}, {'601444', '251546', '228309'})
        self.assertEqual({r['status'] for r in rows}, {'available'})

    def test_malformed_rows_are_skipped(self):
        """Rows with a bad code or an unknown use are dropped."""
        lines = ['MS\t601-60\t1\tX\tY\t\tAS\t\t\tY\tYes\n', 'MS\t601-606\t1\tX\tY\t\tZZ\t\t\tY\tYes\n']
        self.assertEqual(list(nanpa.parse_utilized(lines)), [])


@tagged('post_install', '-at_install')
class TestNanpaLoad(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Nxx = cls.env['connect.caller.nxx']
        cls.env.cr.execute('DELETE FROM connect_caller_nxx')
        cls.env.invalidate_all()
        cls.sid_seq = 0

    def _files(self, utilized=None):
        return [(io.StringIO(''.join(utilized or _lines(UTILIZED))), 'utilized'),
                (io.StringIO(''.join(_lines(AVAILABLE))), 'available')]

    def _channel(self, caller):
        type(self).sid_seq += 1
        return self.env['connect.channel'].create({
            'sid': 'CAnxx%04d' % self.sid_seq, 'caller': caller, 'called': OWN_DID,
            'technical_direction': 'inbound', 'status': 'ringing'})

    def _signals(self, channel):
        return set(self.env['connect.caller.signal'].search(
            [('channel_id', '=', channel.id)]).mapped('code'))

    def test_load_and_reload_drops_vanished_rows(self):
        """A second load of a reduced file deletes the exchange that vanished."""
        self.assertEqual(self.Nxx._load_nanpa(self._files(), stamp=FIRST_LOAD), 7)
        self.assertEqual(self.Nxx.search_count([]), 7)
        reduced = [line for line in _lines(UTILIZED) if '601-777' not in line]
        self.Nxx._load_nanpa(self._files(reduced), stamp=SECOND_LOAD)
        self.assertFalse(self.Nxx.search([('npa_nxx', '=', '601777')]))
        self.assertEqual(self.Nxx.search_count([]), 6)
        self.assertEqual(
            self.env['ir.config_parameter'].sudo().get_param(nxx_module.LOADED_PARAM),
            fields.Datetime.to_string(SECOND_LOAD))

    def test_reload_keeps_local_calling_guide_rows(self):
        """A load never removes a row the Local Calling Guide created."""
        self.Nxx.create({'npa_nxx': '416555', 'npa': '416', 'nxx': '555', 'origin': 'lcg',
                         'status': 'assigned'})
        self.Nxx._load_nanpa(self._files(), stamp=FIRST_LOAD)
        self.assertTrue(self.Nxx.search([('npa_nxx', '=', '416555')]))

    def test_download_failure_leaves_table_untouched(self):
        """A failed download logs one warning and changes nothing."""
        self.Nxx._load_nanpa(self._files(), stamp=FIRST_LOAD)
        with mock.patch('requests.get', side_effect=requests.ConnectionError('down')), \
                self.assertLogs(nxx_module.__name__, 'WARNING') as logs:
            self.assertEqual(self.Nxx._cron_load_nanpa(), 0)
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(self.Nxx.search_count([]), 7)

    def test_available_exchange_is_a_high_spoof_signal(self):
        """A call from an available exchange raises nxx_available and a likely_spoof verdict."""
        self.Nxx._load_nanpa(self._files(), stamp=fields.Datetime.now())
        channel = self._channel('+16014440123')
        result = channel._caller_intel_ingest({'attestation': 'none'})
        self.assertIn('nxx_available', self._signals(channel))
        self.assertEqual(result['verdict'], 'likely_spoof')

    def test_unlisted_exchange_in_loaded_area_code_warns(self):
        """An exchange missing from a loaded area code raises nxx_not_in_service."""
        self.Nxx._load_nanpa(self._files(), stamp=fields.Datetime.now())
        channel = self._channel('+16018880123')
        channel._caller_intel_ingest({'attestation': 'none'})
        self.assertIn('nxx_not_in_service', self._signals(channel))

    def test_assigned_exchange_fills_calling_from(self):
        """An assigned exchange writes locality, state and holder into calling_from."""
        self.Nxx._load_nanpa(self._files(), stamp=fields.Datetime.now())
        channel = self._channel('+16015550123')
        channel._caller_intel_ingest({'attestation': 'none'})
        rep = channel.caller_reputation_id
        self.assertEqual(rep.nxx_status, 'assigned')
        self.assertEqual(
            rep.calling_from, 'Ridgeland, MS · CELLCO PARTNERSHIP DBA VERIZON WIRELESS - MS')
        self.assertNotIn('nxx_available', self._signals(channel))

    def test_non_us_numbers_add_no_exchange_work(self):
        """Callers outside the loaded area codes, and outside +1, cost no queries and raise no exchange signal."""
        self.Nxx._load_nanpa(self._files(), stamp=fields.Datetime.now())
        self.Nxx._loaded_npas()
        self.env['ir.config_parameter'].sudo().get_param(nxx_module.LOADED_PARAM)
        channel = self._channel(OWN_DID)
        rep = self.env['connect.caller.reputation'].browse()
        for number in ('+14165550123', '+442079460958'):
            with self.subTest(number=number):
                facts_num = numbering.analyze(number)
                self.env.flush_all()
                with self.assertQueryCount(0):
                    self.assertEqual(channel._caller_intel_extra_facts(facts_num, rep, True), ({}, {}))
        canadian = self._channel('+14165550123')
        canadian._caller_intel_ingest({'attestation': 'none'})
        self.assertFalse(self._signals(canadian) & {'nxx_available', 'nxx_not_in_service'})

    def test_lookup_is_one_indexed_read(self):
        """With caches warm the exchange lookup is a single query."""
        self.Nxx._load_nanpa(self._files(), stamp=fields.Datetime.now())
        self.Nxx._lookup_facts('601555')
        self.env.flush_all()
        with self.assertQueryCount(1):
            facts = self.Nxx._lookup_facts('601555')
        self.assertEqual((facts['status'], facts['in_service'], facts['stale']), ('assigned', True, False))

    def test_stale_load_is_flagged(self):
        """A load older than nxx_stale_days reports the facts as stale."""
        self.Nxx._load_nanpa(self._files(), stamp=datetime(2020, 1, 1))
        self.assertTrue(self.Nxx._lookup_facts('601444')['stale'])
