from contextlib import ExitStack
from unittest import mock

from odoo.tests import TransactionCase, tagged

from odoo.addons.connect_caller_intel.services.adapters.base import AdapterError
from odoo.addons.connect_caller_intel.services.adapters.registry import ADAPTERS

from odoo.addons.modelnexus_ascertain.services import fetch as fetcher

from ..services import web_reports

E164 = '+15550100199'
BOARD_URL = 'https://800notes.com/Phone.aspx/1-555-010-0199'
OTHER_URL = 'https://example.org/blog/spam-numbers'
FETCH = 'odoo.addons.modelnexus_ascertain.services.fetch'
PAGE = 'Comments for 555-010-0199: this number is a scam, do not answer.'


@tagged('post_install', '-at_install')
class TestWebReports(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Inquiry = cls.env['modelnexus.ascertain.inquiry']
        cls.source = cls.env.ref('connect_caller_intel_ascertain.source_web_reports')
        cls.rep = cls.env['connect.caller.reputation'].create({'e164': E164})

    def _search(self, urls):
        results = [{'rank': i, 'url': u, 'title': '', 'snippet': ''}
                   for i, u in enumerate(urls, 1)]
        return mock.patch(
            'odoo.addons.modelnexus_ascertain.models.inquiry_search._run_search',
            return_value=('searxng', 'https://search.gdo.sh', results, None))

    def _fetch(self, text=PAGE, final_url=BOARD_URL):
        body = ('<html><body><p>%s</p></body></html>' % text).encode()
        result = fetcher.FetchResult(
            200, final_url, 'text/html; charset=utf-8', body,
            {'Content-Type': 'text/html; charset=utf-8'}, False)
        stack = ExitStack()
        stack.enter_context(mock.patch(FETCH + '.refusal_reason', return_value=None))
        stack.enter_context(mock.patch(FETCH + '.robots_allows', return_value=True))
        stack.enter_context(mock.patch(FETCH + '.fetch', return_value=result))
        return stack

    def test_registered_and_seeded(self):
        self.assertIn('web_reports', ADAPTERS)
        self.assertTrue(self.source.enabled)
        self.assertEqual(self.source.cost_class, 'free')

    def test_query_names_both_formats(self):
        self.assertEqual(
            web_reports.search_query(E164),
            '"+1 555 010 0199" OR "555-010-0199" spam OR scam OR caller')

    def test_one_inquiry_per_refresh_with_domain_marks(self):
        before = self.Inquiry.search_count([])
        with self._search([BOARD_URL, OTHER_URL]), self._fetch():
            self.assertEqual(self.rep._refresh_source('web_reports'), 'found')
        inquiries = self.Inquiry.search([], order='id desc', limit=1)
        self.assertEqual(self.Inquiry.search_count([]), before + 1)
        self.assertEqual(inquiries.state, 'concluded')
        kinds = inquiries.step_ids.mapped('kind')
        for kind in ('search', 'fetch', 'finding', 'conclude'):
            self.assertIn(kind, kinds)
        mark = self.rep.mark_ids
        self.assertEqual(len(mark), 1)
        self.assertEqual(mark.reporter, '800notes.com')
        self.assertEqual(mark.evidence_url, BOARD_URL)
        self.assertEqual(mark.category, 'scam')
        self.assertEqual(mark.inquiry_id, inquiries)
        self.assertEqual(mark.capture_id.final_url, BOARD_URL)
        self.assertEqual(self.rep.web_inquiry_id, inquiries)
        self.assertEqual(self.rep.web_capture_id, mark.capture_id)

    def test_non_board_results_are_not_fetched(self):
        with self._search([OTHER_URL]), self._fetch():
            self.assertEqual(self.rep._refresh_source('web_reports'), 'not_found')
        self.assertFalse(self.rep.mark_ids)
        inquiry = self.Inquiry.search([], order='id desc', limit=1)
        self.assertEqual(inquiry.state, 'not_found')
        self.assertNotIn('fetch', inquiry.step_ids.mapped('kind'))

    def test_board_page_without_the_number_is_not_found(self):
        with self._search([BOARD_URL]), self._fetch(text='Nothing about this caller.'):
            self.assertEqual(self.rep._refresh_source('web_reports'), 'not_found')
        self.assertFalse(self.rep.mark_ids)
        self.assertEqual(self.Inquiry.search([], order='id desc', limit=1).state, 'not_found')

    def test_search_failure_closes_unknown_and_retries(self):
        failed = ('searxng', 'https://search.gdo.sh', [], 'unreachable')
        with mock.patch(
                'odoo.addons.modelnexus_ascertain.models.inquiry_search._run_search',
                return_value=failed):
            with self.assertRaises(AdapterError):
                ADAPTERS['web_reports'].lookup(self.env, E164)
