from datetime import timedelta
from unittest import mock

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.queue_job.exception import RetryableJobError

from ..services.adapters import local_calling_guide
from ..services.adapters.registry import ADAPTERS

BODY = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE root [<!ENTITY nbsp "&#160;"><!ENTITY copy "&#169;">]>
<root><inputdata><npa>601</npa><nxx>605</nxx></inputdata>
<prefixdata><npa>601</npa><nxx>605</nxx><x>A</x><rc>Madison</rc><region>MS</region>
<ocn>9419</ocn><company-name>BELLSOUTH TELECOMM INC DBA SOUTH CENTRAL BELL TEL</company-name>
<lata>482</lata><rc-lat>32.465976</rc-lat><rc-lon>-90.117879</rc-lon></prefixdata>
<prefixdata><npa>601</npa><nxx>605</nxx><x>0</x><rc>Madison</rc><region>MS</region><ocn>9419</ocn></prefixdata>
</root>"""
EMPTY = """<?xml version="1.0"?><root><inputdata><npa>601</npa><nxx>999</nxx></inputdata></root>"""


def _response(body):
    response = mock.Mock(status_code=200, text=body)
    response.raise_for_status.return_value = None
    return response


@tagged('post_install', '-at_install')
class TestLocalCallingGuide(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rep = cls.env['connect.caller.reputation']
        cls.Nxx = cls.env['connect.caller.nxx']
        cls.source = cls.env.ref('connect_caller_intel.source_local_calling_guide')
        cls.adapter = ADAPTERS['local_calling_guide']
        cls.env['connect.caller.source'].search(
            [('tier', '=', 'async'), ('id', '!=', cls.source.id)]).write({'enabled': False})
        cls.Nxx.search([('npa_nxx', 'in', ['601605', '601999'])]).unlink()

    def _unthrottle(self):
        self.source.last_request_at = False

    def test_adapter_is_ready_and_nanp_only(self):
        """The adapter is ready and applies to unplaced +1 numbers only."""
        self.assertTrue(self.adapter.ready)
        unplaced = self.Rep.create({'e164': '+16016050001'})
        self.assertTrue(self.adapter.applies_to(self.env, unplaced))
        self.assertFalse(self.adapter.applies_to(self.env, self.Rep.create({'e164': '+442079460958'})))
        placed = self.Rep.create({'e164': '+16016050002', 'rate_center': 'MADISON'})
        self.assertFalse(self.adapter.applies_to(self.env, placed))
        known = self.Rep.create({'e164': '+16016050003', 'nxx_status': 'assigned'})
        self.assertFalse(self.adapter.applies_to(self.env, known))

    def test_lookup_parses_and_caches_the_exchange(self):
        """A lookup stores an lcg row, sends the polite agent and returns numbering."""
        self._unthrottle()
        with mock.patch('requests.get', return_value=_response(BODY)) as get:
            result = self.adapter.lookup(self.env, '+16016050001')
        self.assertEqual(get.call_args.kwargs['params'], {'npa': '601', 'nxx': '605'})
        self.assertTrue(get.call_args.kwargs['headers']['User-Agent'].startswith('HarrisonConnectCallerIntel/'))
        self.assertEqual(result['numbering'], {
            'rate_center': 'Madison', 'numbering_state': 'MS', 'ocn': '9419',
            'ocn_company': 'BELLSOUTH TELECOMM INC DBA SOUTH CENTRAL BELL TEL',
            'nxx_status': 'assigned'})
        self.assertEqual(result['marks'], [])
        row = self.Nxx.search([('npa_nxx', '=', '601605')])
        self.assertEqual((row.origin, row.lata, row.pooled), ('lcg', '482', True))
        self.assertTrue(row.lcg_checked_at)

    def test_second_number_in_exchange_makes_no_http_call(self):
        """A second number in the same exchange within 30 days is answered from the table."""
        self._unthrottle()
        with mock.patch('requests.get', return_value=_response(BODY)):
            self.adapter.lookup(self.env, '+16016050001')
        with mock.patch('requests.get') as get:
            result = self.adapter.lookup(self.env, '+16016050009')
        get.assert_not_called()
        self.assertTrue(result['found'])
        self.assertEqual(result['numbering']['rate_center'], 'Madison')

    def test_stale_cache_asks_again(self):
        """An answer older than 30 days is fetched again."""
        self._unthrottle()
        with mock.patch('requests.get', return_value=_response(BODY)):
            self.adapter.lookup(self.env, '+16016050001')
        row = self.Nxx.search([('npa_nxx', '=', '601605')])
        row.lcg_checked_at = fields.Datetime.now() - timedelta(days=local_calling_guide.CACHE_DAYS + 1)
        self._unthrottle()
        with mock.patch('requests.get', return_value=_response(BODY)) as get:
            self.adapter.lookup(self.env, '+16016050001')
        get.assert_called_once()

    def test_unknown_prefix_is_not_found(self):
        """An empty guide answer is not found and stores no row."""
        self._unthrottle()
        with mock.patch('requests.get', return_value=_response(EMPTY)):
            result = self.adapter.lookup(self.env, '+16019990001')
        self.assertEqual(result, {'found': False, 'marks': []})
        self.assertFalse(self.Nxx.search([('npa_nxx', '=', '601999')]))

    def test_existing_nanpa_row_keeps_its_values(self):
        """The guide adds locality and LATA to a NANPA row without replacing its holder."""
        self.Nxx.create({'npa_nxx': '601605', 'npa': '601', 'nxx': '605', 'origin': 'nanpa',
                         'status': 'assigned', 'company': 'NANPA HOLDER', 'rate_center': 'MADISON'})
        self._unthrottle()
        with mock.patch('requests.get', return_value=_response(BODY)):
            result = self.adapter.lookup(self.env, '+16016050001')
        row = self.Nxx.search([('npa_nxx', '=', '601605')])
        self.assertEqual((row.origin, row.company, row.locality), ('nanpa', 'NANPA HOLDER', 'Madison'))
        self.assertEqual(result['numbering']['ocn_company'], 'NANPA HOLDER')

    def test_second_distinct_request_within_a_second_is_throttled(self):
        """A request right after another retries without recording an outcome."""
        rep = self.Rep.create({'e164': '+16016050001'})
        self._unthrottle()
        with mock.patch('requests.get', return_value=_response(BODY)):
            self.assertEqual(rep._refresh_source('local_calling_guide'), 'found')
        other = self.Rep.create({'e164': '+16019990001'})
        with mock.patch('requests.get') as get, self.assertRaises(RetryableJobError):
            other._refresh_source('local_calling_guide')
        get.assert_not_called()
        self.assertNotIn('local_calling_guide', other.source_checked_json or {})

    def test_result_fills_the_reputation(self):
        """A refresh writes the guide's numbering onto the reputation."""
        rep = self.Rep.create({'e164': '+16016050001'})
        self._unthrottle()
        with mock.patch('requests.get', return_value=_response(BODY)):
            rep._refresh_source('local_calling_guide')
        self.assertEqual((rep.rate_center, rep.numbering_state, rep.nxx_status),
                         ('Madison', 'MS', 'assigned'))
        self.assertEqual(rep.calling_from, 'Madison, MS · BELLSOUTH TELECOMM INC DBA SOUTH CENTRAL BELL TEL')
