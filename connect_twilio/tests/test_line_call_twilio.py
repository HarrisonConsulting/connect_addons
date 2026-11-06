# -*- coding: utf-8 -*-
"""Twilio claims a call at read time, on its own mark, for the person reading it."""

from uuid import uuid4

from odoo.addons.connect.models.call import Call as CoreCall
from odoo.tests import new_test_user, tagged

from .common import TwilioTestCommon

OLD_WRITE_DATE = '2001-02-03 04:05:06'
SID = 'CA' + '0' * 32


@tagged('post_install', '-at_install')
class TestLineCallTwilio(TwilioTestCommon):

    def setUp(self):
        super().setUp()
        self.odoo_user = new_test_user(
            self.env, login='line-call-%s' % uuid4().hex,
            groups='base.group_user,connect.group_user')
        self.connect_user = self._connect_user(self.odoo_user)
        self.Call = self.env['connect.call']

    def _connect_user(self, odoo_user):
        return self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True,
        ).create({'user': odoo_user.id, 'sip_enabled': False, 'client_enabled': False})

    def _row(self, connect_user=None, name='Twilio desk', adapter='twilio', **values):
        tool = self.env['connect.tool'].create({'name': name, 'adapter': adapter})
        self.env['connect.user.tool'].create({
            'user_id': (connect_user or self.connect_user).id,
            'tool_id': tool.id,
            'role': 'active',
            'use': 'voice',
            **values,
        })
        return tool

    def _call(self, **values):
        return self.Call.create({'direction': 'outgoing', 'called': '+15550199', **values})

    def _read(self, call, user=None):
        return self.Call.with_user(user or self.odoo_user).get_widget_calls([('id', '=', call.id)])[0]

    def test_marked_row_is_the_readers_twilio_line(self):
        tool = self._row()
        call = self._call(call_sid=SID, caller_user=self.odoo_user.id)
        self.assertEqual(call.with_user(self.odoo_user)._widget_tool(), tool)
        row = self._read(call)
        self.assertEqual(row['line_key'], 'twilio')
        self.assertEqual(row['line_label'], 'Twilio desk')

    def test_unmarked_row_gets_no_line(self):
        self._row()
        call = self._call(caller_user=self.odoo_user.id)
        row = self._read(call)
        self.assertIs(row['line_key'], False)
        self.assertIs(row['line_label'], False)

    def test_unmarked_row_is_left_to_the_next_claim(self):
        """So the order providers load in cannot matter."""
        other = self.env['connect.tool'].create({'name': 'Other', 'adapter': 'sip'})
        self.patch(CoreCall, '_widget_tool_claim', lambda call: other)
        self.assertEqual(self._call(caller_user=self.odoo_user.id)._widget_tool(), other)
        self._row()
        marked = self._call(call_sid=SID, caller_user=self.odoo_user.id)
        self.assertEqual(marked.with_user(self.odoo_user)._widget_tool().adapter, 'twilio')

    def test_a_stamp_wins_over_the_mark(self):
        self._row()
        stamp = self.env['connect.tool'].create({'name': 'Stamped', 'adapter': 'sip'})
        call = self._call(call_sid=SID, tool_id=stamp.id, caller_user=self.odoo_user.id)
        self.assertEqual(self._read(call)['line_label'], 'Stamped')

    def test_first_voice_row_with_the_adapter(self):
        self._row(name='Second', sequence=20)
        self._row(name='First', sequence=10)
        self._row(name='Other kind', adapter='sip', sequence=1)
        self._row(name='Messages only', use='message', sequence=2)
        call = self._call(call_sid=SID, caller_user=self.odoo_user.id)
        self.assertEqual(self._read(call)['line_label'], 'First')

    def test_a_reader_with_no_twilio_row_hears_the_line_has_gone(self):
        """The row is known to be Twilio's, so a key no line of theirs has makes the
        redial say the line has gone, instead of going out on the selection."""
        self._row(adapter='sip', name='Not Twilio')
        call = self._call(call_sid=SID, caller_user=self.odoo_user.id)
        row = self._read(call)
        self.assertEqual(row['line_key'], 'twilio')
        self.assertIs(row['line_label'], False)

    def test_each_reader_gets_their_own_row(self):
        other_user = new_test_user(
            self.env, login='line-call-%s' % uuid4().hex,
            groups='base.group_user,connect.group_user')
        other_connect = self._connect_user(other_user)
        self._row(name='Mine')
        self._row(other_connect, name='Theirs')
        call = self._call(call_sid=SID, caller_user=self.odoo_user.id)
        call.called_users = other_user
        self.assertEqual(self._read(call)['line_label'], 'Mine')
        self.assertEqual(self._read(call, other_user)['line_label'], 'Theirs')

    def test_the_claim_is_never_written_back(self):
        self._row()
        call = self._call(call_sid=SID, caller_user=self.odoo_user.id)
        self.env.flush_all()
        self.env.cr.execute(
            'UPDATE connect_call SET write_date = %s, write_uid = 1 WHERE id = %s',
            (OLD_WRITE_DATE, call.id))
        call.invalidate_recordset()
        self.assertEqual(self._read(call)['line_key'], 'twilio')
        self.env.flush_all()
        self.env.cr.execute('SELECT write_date::text, write_uid, tool_id FROM connect_call WHERE id = %s', (call.id,))
        self.assertEqual(self.env.cr.fetchone(), (OLD_WRITE_DATE, 1, None))
