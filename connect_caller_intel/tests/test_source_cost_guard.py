from psycopg2 import IntegrityError

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged('post_install', '-at_install')
class TestSourceCostGuard(TransactionCase):

    def _src(self, code):
        return self.env.ref(f'connect_caller_intel.source_{code}')

    #: Free sources that ship off until their data host or credential exists.
    FREE_SHIPPED_OFF = {
        'osm': 'no phone extract host is configured yet',
    }

    def test_seed_defaults(self):
        """Paid sources are off; free sources are on unless listed as shipped off for a stated reason."""
        sources = self.env['connect.caller.source'].search([])
        self.assertTrue(sources)
        for source in sources:
            expected = source.cost_class == 'free' and source.code not in self.FREE_SHIPPED_OFF
            self.assertEqual(source.enabled, expected, source.code)

    def test_direct_write_on_paid_raises(self):
        """A direct write of enabled=True on a paid source without ack raises."""
        paid = self._src('twilio_lookup_cnam')
        with self.assertRaises(UserError):
            paid.write({'enabled': True})
        self.assertFalse(paid.enabled)

    def test_create_enabled_paid_raises(self):
        """Creating an enabled paid source without ack raises."""
        with self.assertRaises(UserError):
            self.env['connect.caller.source'].create({
                'code': 'x_paid', 'name': 'X', 'cost_class': 'paid', 'enabled': True})

    def test_action_enable_records_ack(self):
        """Enable records user and datetime; Disable clears them."""
        paid = self._src('twilio_lookup_cnam').with_user(self.env.user)
        paid.action_enable()
        self.assertTrue(paid.enabled)
        self.assertEqual(paid.cost_acknowledged_by, self.env.user)
        self.assertTrue(paid.cost_acknowledged_at)
        paid.action_disable()
        self.assertFalse(paid.enabled)
        self.assertFalse(paid.cost_acknowledged_by)

    def test_free_enable_needs_no_ack(self):
        """A free source toggles without acknowledgement."""
        free = self._src('staff')
        free.action_disable()
        free.write({'enabled': True})
        self.assertTrue(free.enabled)
        self.assertFalse(free.cost_acknowledged_by)

    def test_code_unique(self):
        """Source codes are unique."""
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError), self.cr.savepoint():
            self.env['connect.caller.source'].create({'code': 'staff', 'name': 'Dup'})
