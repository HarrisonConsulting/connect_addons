from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestRingTour(HttpCase):
    """A spam caller rings the softphone and staff clear the verdict from the card."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref('base.user_admin')
        # Warm the menu cache outside the browser window (same pattern as the connect systray tour).
        cls.env['ir.ui.menu'].with_user(cls.admin).load_web_menus('')
        Settings = cls.env['connect.settings'].sudo()
        Settings.set_param('webrtc_provider', 'twilio')
        Settings.set_param('account_sid', 'AC' + '0' * 32)
        Settings.set_param('twilio_api_key', 'SK' + '0' * 32)
        Settings.set_param('twilio_api_secret', 's' * 32)
        ConnectUser = cls.env['connect.user']
        connect_user = ConnectUser.search([('user', '=', cls.admin.id)], limit=1)
        if connect_user:
            connect_user.client_enabled = True
        else:
            domain = cls.env['connect.domain'].search([], limit=1) or cls.env['connect.domain'].with_context(
                no_twilio_create=True).create({'friendly_name': 'Ring Tour Domain', 'subdomain': 'ringtour'})
            ConnectUser.create({
                'username': 'ringtouradmin', 'domain': domain.id, 'sip_enabled': False,
                'client_enabled': True, 'user': cls.admin.id})

    def test_spam_ring_is_cleared_by_staff(self):
        rep = self.env['connect.caller.reputation'].create({'e164': '+14155550199'})
        self.env['connect.caller.mark'].create({
            'reputation_id': rep.id, 'source_id': self.env.ref('connect_caller_intel.source_staff').id,
            'reporter': 'Staff', 'category': 'spam', 'external_ref': 'staff:ring_tour'})
        call = self.env['connect.call'].create({
            'direction': 'incoming', 'status': 'ringing', 'caller': '+14155550199',
            'called': '+15559990002', 'caller_reputation_id': rep.id, 'caller_verdict': 'likely_spam'})
        self.env['connect.caller.signal'].create({
            'call_id': call.id, 'reputation_id': rep.id, 'code': 'staff_marked',
            'severity': 'high', 'reason': 'Marked as spam by staff'})
        self.start_tour('/odoo', 'connect_caller_intel_ring_tour', login=self.admin.login)
        self.assertEqual(call.caller_signal_ids.state, 'dismissed')
