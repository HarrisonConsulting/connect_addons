from urllib.parse import quote

from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestCallerIntelPortal(HttpCase):
    """Portal users see only their own commercial partner's external marks."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        Source = env['connect.caller.source']
        cls.ext = Source.create({
            'code': 'portal_ext', 'name': 'Portal Registry', 'tier': 'async',
            'clearing_url': 'https://registry.example/clear', 'clearing_note': 'Register the number.'})
        # The staff source is seeded by data/source_data.xml; codes are unique.
        cls.staff = env.ref('connect_caller_intel.source_staff')
        cls.remediation = env['connect.caller.remediation'].create({
            'code': 'portal_ext', 'name': 'Portal Registry', 'steps': 'Open the registry and dispute.',
            'source_codes': 'portal_ext', 'removable': False})
        Partner = env['res.partner']
        cls.company_a = Partner.create({'name': 'Company A', 'is_company': True, 'phone': '+12025550101'})
        cls.contact_a = Partner.create({
            'name': 'Contact A', 'parent_id': cls.company_a.id, 'mobile': '+442071838750'})
        cls.company_b = Partner.create({'name': 'Company B', 'is_company': True, 'phone': '+12025550202'})
        cls.user_a = cls._portal_user('portal_a', cls.contact_a)
        cls.user_b = cls._portal_user('portal_b', cls.company_b)
        Rep = env['connect.caller.reputation']
        Mark = env['connect.caller.mark']
        cls.evidence = 'https://evidence.example/secret-row'
        for e164 in ('+12025550101', '+442071838750', '+12025550202'):
            rep = Rep.create({'e164': e164})
            Mark.create({
                'reputation_id': rep.id, 'source_id': cls.ext.id, 'reporter': 'Portal Registry',
                'category': 'scam', 'report_count': 4, 'evidence_url': cls.evidence})
            Mark.create({
                'reputation_id': rep.id, 'source_id': cls.staff.id, 'reporter': 'Agent Smith Staff',
                'category': 'spam'})

    @classmethod
    def _portal_user(cls, login, partner):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'password': login, 'partner_id': partner.id,
            'group_ids': [(6, 0, [cls.env.ref('base.group_portal').id])]})

    def test_helper_scope_and_fields(self):
        numbers = self.company_a._caller_intel_portal_numbers()
        self.assertEqual({n['e164'] for n in numbers}, {'+12025550101', '+442071838750'})
        mark = numbers[0]['marks'][0]
        self.assertEqual(mark['reporter'], 'Portal Registry')
        self.assertEqual(mark['steps'], 'Open the registry and dispute.')
        self.assertFalse(mark['removable'])
        self.assertEqual(len(numbers[0]['marks']), 1)
        self.assertNotIn('evidence_url', mark)

    def test_own_numbers_are_listed_and_counted(self):
        self.authenticate('portal_a', 'portal_a')
        body = self.url_open('/my/caller-reputation').text
        self.assertIn('+12025550101', body)
        self.assertIn('+442071838750', body)
        self.assertNotIn('+12025550202', body)
        self.assertNotIn('Agent Smith Staff', body)
        counters = self.make_jsonrpc_request('/my/counters', {'counters': ['caller_number_count']})
        self.assertEqual(counters['caller_number_count'], 2)

    def test_other_partner_number_is_not_found(self):
        self.authenticate('portal_a', 'portal_a')
        with mute_logger('odoo.http'):
            res = self.url_open('/my/caller-reputation/' + quote('+12025550202'))
        self.assertEqual(res.status_code, 404)

    def test_isolation_between_portal_users(self):
        self.authenticate('portal_b', 'portal_b')
        body = self.url_open('/my/caller-reputation').text
        self.assertIn('+12025550202', body)
        self.assertNotIn('+12025550101', body)
        res = self.url_open('/my/caller-reputation/' + quote('+442071838750'))
        self.assertEqual(res.status_code, 404)

    def test_detail_hides_evidence_and_staff_marks(self):
        self.authenticate('portal_a', 'portal_a')
        res = self.url_open('/my/caller-reputation/' + quote('+442071838750'))
        self.assertEqual(res.status_code, 200)
        self.assertIn('Portal Registry', res.text)
        self.assertIn('Open the registry and dispute.', res.text)
        self.assertIn('This record cannot be removed.', res.text)
        self.assertNotIn(self.evidence, res.text)
        self.assertNotIn('Agent Smith Staff', res.text)

    def test_internal_user_sees_own_company_only(self):
        company = self.env['res.partner'].create({'name': 'Staff Co', 'is_company': True})
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Staff', 'login': 'portal_staff', 'partner_id': company.id,
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        numbers = user.partner_id.commercial_partner_id._caller_intel_portal_numbers()
        self.assertEqual(numbers, [])
