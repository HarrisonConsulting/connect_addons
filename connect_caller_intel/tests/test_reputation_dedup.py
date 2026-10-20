from psycopg2 import IntegrityError

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestReputationDedup(TransactionCase):

    def test_e164_unique(self):
        """A second reputation for the same number is rejected by the database."""
        Rep = self.env['connect.caller.reputation']
        Rep.create({'e164': '+12025550143'})
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            Rep.create({'e164': '+12025550143'})

    def test_get_or_create_returns_single_row(self):
        """_get_or_create returns the same row on repeat calls."""
        Rep = self.env['connect.caller.reputation']
        first = Rep._get_or_create('+12025550144')
        self.assertEqual(Rep._get_or_create('+12025550144'), first)
        self.assertEqual(Rep.search_count([('e164', '=', '+12025550144')]), 1)

    def test_mark_upsert_key_and_score(self):
        """A mark is unique per (source, number, external_ref) and drives the score."""
        rep = self.env['connect.caller.reputation']._get_or_create('+12025550145')
        source = self.env.ref('connect_caller_intel.source_ftc_dnc')
        vals = {'reputation_id': rep.id, 'source_id': source.id,
                'reporter': 'FTC Do Not Call complaints', 'external_ref': 'r1', 'report_count': 5}
        self.env['connect.caller.mark'].create(vals)
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            self.env['connect.caller.mark'].create(vals)
        self.assertEqual(rep.active_mark_count, 1)
        self.assertAlmostEqual(rep.spam_score, 0.5)
        self.assertEqual(rep.mark_ids.clearing_url, source.clearing_url)
