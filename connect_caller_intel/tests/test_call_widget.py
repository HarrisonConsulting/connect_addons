from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

FEDERATION_LOGGERS = (
    'odoo.addons.gdo_platform.services.crib',
    'odoo.addons.gdo_platform.models.gdo_federation_user_identity',
)


@tagged('post_install', '-at_install')
class TestCallWidget(TransactionCase):
    """The softphone reads the caller verdict and the card names the call staff can mark."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rep = cls.env['connect.caller.reputation'].create({'e164': '+14155550198'})
        cls.recent = cls._ring('+14155550198', 'likely_spam')

    @classmethod
    def _ring(cls, e164, verdict):
        return cls.env['connect.call'].create({
            'direction': 'incoming', 'status': 'ringing', 'caller': e164, 'called': '+15559990002',
            'caller_reputation_id': cls.rep.id, 'caller_verdict': verdict,
        })

    def test_widget_calls_carry_the_verdict(self):
        rows = self.env['connect.call'].get_widget_calls([('id', '=', self.recent.id)])
        self.assertEqual(rows[0]['caller_verdict'], 'likely_spam')

    def test_card_names_the_recent_call(self):
        card = self.env['connect.call'].caller_intel_card('+14155550198')
        self.assertEqual(card['call_id'], self.recent.id)

    def test_card_ignores_a_call_older_than_two_hours(self):
        old = fields.Datetime.now() - timedelta(hours=3)
        self.env.cr.execute('UPDATE connect_call SET create_date = %s WHERE id = %s', (old, self.recent.id))
        self.recent.invalidate_recordset()
        card = self.env['connect.call'].caller_intel_card('+14155550198')
        self.assertFalse(card['call_id'])

    def _user(self, login, groups):
        with mute_logger(*FEDERATION_LOGGERS):
            return self.env['res.users'].create({
                'name': login, 'login': login,
                'group_ids': [(6, 0, [g.id for g in groups])]})

    def test_staff_may_mark(self):
        staff = self._user('staff_connect_widget', [
            self.env.ref('base.group_user'), self.env.ref('connect.group_user')])
        card = self.env['connect.call'].with_user(staff).caller_intel_card('+14155550198')
        self.assertTrue(card['can_mark'])

    def test_user_outside_connect_may_not_mark(self):
        outsider = self._user('outside_connect_widget', [self.env.ref('base.group_user')])
        card = self.env['connect.call'].with_user(outsider).sudo().caller_intel_card('+14155550198')
        self.assertFalse(card['can_mark'])

    def test_script_cnam_is_still_plain_text(self):
        self.rep.cnam_last = '<script>alert(1)</script>'
        card = self.env['connect.call'].caller_intel_card('+14155550198')
        self.assertEqual(card['name'], '<script>alert(1)</script>')
