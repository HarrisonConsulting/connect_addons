# -*- coding: utf-8 -*-
"""Browser cover for the active-calls systray button.

Whether the active-calls button is present at all is invisible to a server-side
test. Only a real page load can say. The tours that mount a softphone need a
provider, so they live with it: connect_twilio/tests/test_softphone_tour.py.
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

    def test_active_calls_tray_hidden_without_calls(self):
        # The active-calls button is a separate service and renders without a
        # softphone.
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
