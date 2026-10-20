from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCallerIntelCard(TransactionCase):
    """The ring card payload returns network text as inert plain text and carries clearing links."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = cls.env['connect.caller.source'].create({
            'code': 'card_test', 'name': 'Card Test Registry', 'tier': 'async',
            'clearing_url': 'https://registry.example/clear',
        })
        cls.rep = cls.env['connect.caller.reputation'].create({
            'e164': '+12025550143', 'cnam_last': '<script>alert(1)</script>',
        })
        cls.env['connect.caller.mark'].create([
            {'reputation_id': cls.rep.id, 'source_id': cls.source.id,
             'reporter': 'Card Test Registry', 'category': 'scam', 'report_count': 12},
            {'reputation_id': cls.rep.id, 'source_id': cls.source.id,
             'reporter': 'Unsafe Link', 'report_count': 1, 'clearing_url': 'javascript:alert(1)'},
        ])

    def test_script_cnam_is_plain_text(self):
        card = self.env['connect.call'].caller_intel_card('+12025550143')
        self.assertEqual(card['name'], '<script>alert(1)</script>')
        self.assertIsInstance(card['name'], str)

    def test_marks_carry_reporter_and_clearing_url(self):
        card = self.env['connect.call'].caller_intel_card('+12025550143')
        by_reporter = {m['reporter']: m for m in card['marks']}
        self.assertEqual(by_reporter['Card Test Registry']['clearing_url'], 'https://registry.example/clear')
        self.assertEqual(by_reporter['Unsafe Link']['clearing_url'], '')

    def test_unknown_number_has_no_record(self):
        card = self.env['connect.call'].caller_intel_card('+12025550199')
        self.assertEqual(card['verdict'], 'unknown')
        self.assertEqual(card['marks'], [])
