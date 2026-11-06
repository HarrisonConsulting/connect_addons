# -*- coding: utf-8 -*-
"""Browser cover for a mounted Twilio softphone: whether the phone button renders
its connection state in place, rather than raising toasts, and whether the
in-call dialer keeps its hang-up button.
"""

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestSoftphoneTour(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # base.user_admin's login is NOT always 'admin' — a database restored
        # from a real deployment carries that deployment's login, so the tours
        # must read it off the record or they AccessDenied before step one.
        cls.admin = cls.env.ref('base.user_admin')
        # A cold load_menus holds the one test cursor past the lock timeout
        # while the browser fetches assets, so warm it outside the browser.
        cls.env['ir.ui.menu'].with_user(cls.admin).load_web_menus('')

    @classmethod
    def _enable_softphone(cls):
        """Make get_client_token() mint a credential so the phone mounts.

        The values are throwaway, and cost nothing: web.assets_tests swaps the
        provider transport for a loopback one that registers without a network,
        so the credential is never presented to anyone who could reject it. That
        is what makes a mounted softphone hermetic — HttpCase counts ANY browser
        console error as a failure (common.py sets had_failure BEFORE consulting
        error_checker), so a tour that provokes even a filtered error cannot pass
        however far it gets.
        """
        Settings = cls.env['connect.settings'].sudo()
        Settings.set_param('account_sid', 'AC' + '0' * 32)
        Settings.set_param('twilio_api_key', 'SK' + '0' * 32)
        Settings.set_param('twilio_api_secret', 's' * 32)

        Domain = cls.env['connect.twilio.domain']
        domain = Domain.search([], limit=1) or Domain.with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Systray Tour Domain',
            'subdomain': 'systraytour',
        })
        ConnectUser = cls.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True)
        connect_user = ConnectUser.search([('user', '=', cls.admin.id)], limit=1)
        values = {
            'username': 'systrayadmin',
            'domain': domain.id,
            'sip_enabled': False,
            'client_enabled': True,
            'originate_provider': 'twilio',
        }
        if connect_user:
            connect_user.write(values)
            return connect_user
        return ConnectUser.create({**values, 'user': cls.admin.id})

    def test_phone_tray_renders_state_not_toasts(self):
        self._enable_softphone()
        self.start_tour('/odoo', 'connect_phone_tray_state_tour',
                        login=self.admin.login)

    def test_dialer_keeps_hangup_button_visible(self):
        """The in-call dialer must never clip its own hang-up button.

        The phone is a fixed-height flex column with overflow:hidden, so a tall
        in-call body panel used to push the call controls off the bottom edge.
        """
        self._enable_softphone()
        self.start_tour('/odoo', 'connect_dialer_layout_tour',
                        login=self.admin.login)
