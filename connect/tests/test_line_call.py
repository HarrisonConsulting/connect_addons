# -*- coding: utf-8 -*-
"""A call knows its line by its stamp or a provider's claim, and rows stay as they are.

The options are patched so these run the same with connect alone. Twilio's claim on
its own mark is in connect_twilio's TestLineCallTwilio.
"""

from uuid import uuid4

from odoo.tests import TransactionCase, new_test_user, tagged

from .test_phone_lines import patch_line_options

OLD_WRITE_DATE = '2001-02-03 04:05:06'


@tagged('post_install', '-at_install')
class TestLineCall(TransactionCase):

    def setUp(self):
        super().setUp()
        patch_line_options(self)
        self.odoo_user = new_test_user(
            self.env, login='line-call-%s' % uuid4().hex,
            groups='base.group_user,connect.group_user')
        self.Call = self.env['connect.call']
        self.tool = self.env['connect.tool'].create({'name': 'Sales desk', 'adapter': 'other_voice'})

    def _call(self, **values):
        return self.Call.create({
            'direction': 'outgoing', 'called': '+15550199', 'caller_user': self.odoo_user.id, **values,
        })

    def _widget_row(self, call):
        rows = self.Call.with_user(self.odoo_user).get_widget_calls([('id', '=', call.id)])
        self.assertEqual(len(rows), 1)
        return rows[0]

    def _age(self, call):
        """Back-date a row: a transaction's clock stands still, so now would prove nothing."""
        self.env.flush_all()
        self.env.cr.execute(
            'UPDATE connect_call SET write_date = %s, write_uid = 1 WHERE id = %s',
            (OLD_WRITE_DATE, call.id))
        call.invalidate_recordset()

    def _stored(self, call):
        self.env.flush_all()
        self.env.cr.execute(
            'SELECT write_date::text, write_uid, tool_id FROM connect_call WHERE id = %s', (call.id,))
        return self.env.cr.fetchone()

    def test_stamped_row_has_its_line(self):
        call = self._call(tool_id=self.tool.id)
        self.assertEqual(call._widget_tool(), self.tool)
        row = self._widget_row(call)
        self.assertEqual(row['line_key'], 'other_voice')
        self.assertEqual(row['line_label'], 'Sales desk')

    def test_unstamped_row_with_no_mark_has_no_line(self):
        call = self._call()
        self.assertFalse(call._widget_tool())
        self.assertEqual(call._widget_tool()._name, 'connect.tool')
        row = self._widget_row(call)
        self.assertIs(row['line_key'], False)
        self.assertIs(row['line_label'], False)

    def test_key_is_the_lines_identity_not_the_adapter(self):
        """A history row and the From row have to name a line the same way."""
        def identity(_model, tool):
            return 'account-%s' % tool.id
        self.patch(type(self.env['connect.user']), '_phone_line_identity', identity)
        row = self._widget_row(self._call(tool_id=self.tool.id))
        self.assertEqual(row['line_key'], 'account-%s' % self.tool.id)

    def _patch_claim(self, tool):
        """A provider claiming only rows carrying its mark, here a called number."""
        def claim(call):
            return tool if call.called == 'marked' else self.env['connect.tool']
        self.patch(type(self.Call), '_widget_tool_claim', claim)

    def test_a_claim_answers_only_for_an_unstamped_row(self):
        claimed = self.env['connect.tool'].create({'name': 'Claimed', 'adapter': 'twilio'})
        self._patch_claim(claimed)
        self.assertEqual(self._call(called='marked')._widget_tool(), claimed)
        self.assertEqual(self._call(called='marked', tool_id=self.tool.id)._widget_tool(), self.tool)
        self.assertFalse(self._call()._widget_tool())
        row = self._widget_row(self._call(called='marked'))
        self.assertEqual(row['line_key'], 'twilio')
        self.assertEqual(row['line_label'], 'Claimed')

    def test_a_retired_tool_still_names_the_line(self):
        """The browser is what says the line is gone, so the name has to reach it."""
        call = self._call(tool_id=self.tool.id)
        self.tool.active = False
        row = self._widget_row(call)
        self.assertEqual(row['line_key'], 'other_voice')
        self.assertEqual(row['line_label'], 'Sales desk')

    def test_widget_fields_name_the_line(self):
        self.assertLessEqual({'line_key', 'line_label'}, set(self.Call.get_widget_fields()))

    def test_reading_rewrites_no_row(self):
        stamped = self._call(tool_id=self.tool.id)
        claimed = self._call(called='marked')
        unmarked = self._call()
        self._patch_claim(self.tool)
        for call in stamped + claimed + unmarked:
            self._age(call)
        before = {call.id: self._stored(call) for call in stamped + claimed + unmarked}
        self.assertEqual(before[stamped.id][0], OLD_WRITE_DATE)

        self.assertEqual(self._widget_row(claimed)['line_key'], 'other_voice')
        self._widget_row(stamped)
        self._widget_row(unmarked)
        self.Call.with_user(self.odoo_user).browse(claimed.id).read(['line_key', 'line_label'])

        after = {call.id: self._stored(call) for call in stamped + claimed + unmarked}
        self.assertEqual(after, before)
        self.assertFalse(after[claimed.id][2], 'a claim is never written back as a stamp')

    def test_the_line_is_read_time_only(self):
        self.assertFalse(self.Call._fields['line_key'].store)
        self.assertFalse(self.Call._fields['line_label'].store)
        self.assertNotIn('tool_id', self.env['connect.channel']._fields)

    def test_favorite_keeps_a_line_and_needs_none(self):
        Favorite = self.env['connect.favorite'].with_user(self.odoo_user)
        with_line = Favorite.create({'phone_number': '+15550199', 'line_key': 'other_voice'})
        without = Favorite.create({'phone_number': '+15550198'})
        self.assertEqual(with_line.line_key, 'other_voice')
        self.assertFalse(without.line_key)
