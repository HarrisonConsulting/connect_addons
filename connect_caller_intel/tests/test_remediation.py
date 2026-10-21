from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRemediation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Remediation = cls.env['connect.caller.remediation']
        cls.rep = cls.env['connect.caller.reputation']._get_or_create('+12025550178')

    def _mark(self, source_xmlid, reporter, ref):
        return self.env['connect.caller.mark'].create({
            'reputation_id': self.rep.id,
            'source_id': self.env.ref(source_xmlid).id,
            'reporter': reporter, 'external_ref': ref})

    def _code(self, code):
        return self.Remediation.search([('code', '=', code)])

    def test_seeded_codes(self):
        """The nine registry rows exist."""
        codes = {'free_caller_registry', 'hiya', 'tns_call_guardian', 'first_orion', 'ftc_dnc',
                 'fcc_complaints', 'complaint_board', 'ipqs', 'twilio_cnam'}
        self.assertEqual(set(self.Remediation.search([('code', 'in', list(codes))]).mapped('code')), codes)

    def test_att_routes_to_hiya(self):
        """AT&T has no form of its own; its guidance is Hiya's."""
        hiya = self._code('hiya')
        self.assertIn('AT&T', hiya.carriers)
        self.assertIn('hiyahelp.zendesk.com', hiya.dispute_url)

    def test_public_record_is_not_removable(self):
        """FTC and FCC complaints cannot be removed and name no dispute page."""
        for code in ('ftc_dnc', 'fcc_complaints'):
            row = self._code(code)
            self.assertFalse(row.removable)
            self.assertFalse(row.dispute_url)
            self.assertIn('No verified first-party dispute page', row.steps)

    def test_free_caller_registry_is_a_standing_step(self):
        """Only the registry is shown on every own number."""
        standing = self.Remediation.search([('applies_to_own_numbers', '=', True)])
        self.assertEqual(standing.mapped('code'), ['free_caller_registry'])

    def test_source_match(self):
        """An FTC mark finds the FTC guidance by source code."""
        mark = self._mark('connect_caller_intel.source_ftc_dnc', 'FTC Do Not Call complaints', 'm1')
        self.assertEqual(mark.remediation_id, self._code('ftc_dnc'))

    def test_reporter_match_wins_over_source_match(self):
        """A reporter listed on a row beats the row for the mark's source."""
        board = self._code('complaint_board')
        mark = self._mark('connect_caller_intel.source_ftc_dnc', '800notes.com', 'm2')
        self.assertEqual(mark.remediation_id, board)

    def test_reporter_match_is_case_insensitive_and_exact(self):
        """The board reporter match ignores case and does not match a longer name."""
        board = self._code('complaint_board')
        self.assertEqual(
            self._mark('connect_caller_intel.source_ftc_dnc', 'WhoCallsMe.com', 'm3').remediation_id, board)
        self.assertFalse(
            self._mark('connect_caller_intel.source_staff', 'whocallsme.com.evil', 'm4').remediation_id)

    def test_own_number_guidance_unions_marks_and_standing_steps(self):
        """Our Numbers lists the guidance for each flagged report plus the registry."""
        self.env['connect.twilio.number'].create({'phone_number': '+13125550111'})
        Own = self.env['connect.caller.own.number']
        Own._sync_numbers()
        own = Own.search([('e164', '=', '+13125550111')])
        mark = self.env['connect.caller.mark'].create({
            'reputation_id': own.reputation_id.id,
            'source_id': self.env.ref('connect_caller_intel.source_ftc_dnc').id,
            'reporter': 'FTC Do Not Call complaints', 'external_ref': 'u1'})
        self.assertEqual(own.flagged_mark_ids, mark)
        self.assertEqual(own.remediation_ids, self._code('ftc_dnc') | self._code('free_caller_registry'))
