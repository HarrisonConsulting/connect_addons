# -*- coding: utf-8 -*-
"""Browser cover for the two Connect systray surfaces.

Both behaviours here are invisible to a server-side test: whether the phone
button renders its connection state in place (rather than raising toasts), and
whether the active-calls button is present at all. Only a real page load can
say.
"""

from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestSystrayTour(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # base.user_admin's login is NOT always 'admin' — a database restored
        # from a real deployment carries that deployment's login, so the tours
        # must read it off the record or they AccessDenied before step one.
        cls.admin = cls.env.ref('base.user_admin')

        # Warm the menu cache before any browser runs. HttpCase serves the whole
        # browser session through ONE test cursor, and a cold load_menus on a
        # large database holds it past the 20s lock timeout — long enough that
        # the parallel fetch of web.assets_tests.min.js 500s, which leaves the
        # tour registry empty and the tour never becomes ready. Paying for the
        # cache here, outside the browser window, removes the race.
        # load_menus is ormcached on (uid, debug, lang); the webclient asks for
        # it with session.debug, which is '' in a test session.
        cls.env['ir.ui.menu'].with_user(cls.admin).load_web_menus('')

    @classmethod
    def _enable_softphone(cls):
        """Make get_client_token() mint a credential so the phone mounts.

        Only the tours that need the phone's own DOM call this. The values are
        throwaway, and cost nothing: web.assets_tests swaps the provider
        transport for a loopback one that registers without a network, so the
        credential is never presented to anyone who could reject it. That is
        what makes a mounted softphone hermetic — HttpCase counts ANY browser
        console error as a failure (common.py sets had_failure BEFORE
        consulting error_checker), so a tour that provokes even a filtered
        error cannot pass however far it gets.
        """
        Settings = cls.env['connect.settings'].sudo()
        Settings.set_param('webrtc_provider', 'twilio')
        Settings.set_param('account_sid', 'AC' + '0' * 32)
        Settings.set_param('twilio_api_key', 'SK' + '0' * 32)
        Settings.set_param('twilio_api_secret', 's' * 32)

        ConnectUser = cls.env['connect.user']
        connect_user = ConnectUser.search([('user', '=', cls.admin.id)], limit=1)
        if connect_user:
            connect_user.client_enabled = True
            return connect_user
        Domain = cls.env['connect.domain']
        domain = Domain.search([], limit=1) or Domain.with_context(
            no_twilio_create=True,
        ).create({
            'friendly_name': 'Systray Tour Domain',
            'subdomain': 'systraytour',
        })
        return ConnectUser.create({
            'username': 'systrayadmin',
            'domain': domain.id,
            'sip_enabled': False,
            'client_enabled': True,
            'user': cls.admin.id,
        })

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

    def test_active_calls_tray_hidden_without_calls(self):
        # No softphone here: the active-calls button is a separate service and
        # renders without one.
        self.env['connect.call'].search([('status', '=', 'in-progress')]).unlink()
        self.start_tour(
            '/odoo', 'connect_active_calls_absent_tour', login=self.admin.login)

    def test_active_calls_tray_shows_live_count(self):
        self.env['connect.call'].search([('status', '=', 'in-progress')]).unlink()
        self.env['connect.call'].create({
            'direction': 'incoming',
            'status': 'in-progress',
            'caller': '+15551230001',
            'called': '+15559990002',
        })
        self.start_tour(
            '/odoo', 'connect_active_calls_present_tour', login=self.admin.login)
