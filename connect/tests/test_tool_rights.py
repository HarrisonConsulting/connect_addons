# -*- coding: utf-8 -*-
"""Only an admin changes systems and who holds them; holding a line needs no write.

The mint and the selection options are patched, so these cases run the same on a
database with connect alone. Real Twilio is in connect_twilio's TestToolRightsTwilio.
"""

from uuid import uuid4

from lxml import etree

from odoo import api
from odoo.addons.connect.models.settings import Settings as CoreSettings
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from .test_phone_lines import patch_line_options


@tagged('post_install', '-at_install')
class TestToolRights(TransactionCase):

    def setUp(self):
        super().setUp()
        patch_line_options(self)
        self.plain = new_test_user(
            self.env, login='tool-rights-%s' % uuid4().hex,
            groups='base.group_user,connect.group_user')
        self.admin = new_test_user(
            self.env, login='tool-rights-admin-%s' % uuid4().hex,
            groups='base.group_user,connect.group_admin')
        ConnectUser = self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True)
        self.plain_person = ConnectUser.create({'user': self.plain.id})
        self.tool = self.env['connect.tool'].create({'name': 'Desk', 'adapter': 'twilio'})
        self.other_tool = self.env['connect.tool'].create({'name': 'Other', 'adapter': 'other_voice'})
        self.row = self.env['connect.user.tool'].create({
            'user_id': self.plain_person.id,
            'tool_id': self.tool.id,
            'role': 'active',
            'use': 'voice',
        })

    def _grant_values(self):
        return {
            'user_id': self.plain_person.id,
            'tool_id': self.other_tool.id,
            'role': 'active',
            'use': 'voice',
        }

    def test_plain_user_cannot_create_a_tool(self):
        with self.assertRaises(AccessError):
            self.env['connect.tool'].with_user(self.plain).create(
                {'name': 'Mine', 'adapter': 'other_voice'})

    def test_plain_user_cannot_write_a_tool(self):
        with self.assertRaises(AccessError):
            self.tool.with_user(self.plain).write({'name': 'Mine'})
        self.assertEqual(self.tool.name, 'Desk')

    def test_plain_user_cannot_grant_through_the_tool(self):
        with self.assertRaises(AccessError):
            self.other_tool.with_user(self.plain).write(
                {'assignment_ids': [(0, 0, {'user_id': self.plain_person.id})]})
        self.assertFalse(self.other_tool.assignment_ids)

    def test_plain_user_cannot_grant_themselves_a_row(self):
        with self.assertRaises(AccessError):
            self.env['connect.user.tool'].with_user(self.plain).create(self._grant_values())
        self.assertEqual(self.plain_person.tool_assignment_ids, self.row)

    def test_plain_user_cannot_write_their_row(self):
        for values in (
            {'tool_id': self.other_tool.id},
            {'role': 'backup'},
            {'sequence': 99},
            {'use': 'message'},
        ):
            with self.subTest(values=values), self.assertRaises(AccessError):
                self.row.with_user(self.plain).write(values)
        self.assertEqual(self.row.tool_id, self.tool)

    def test_plain_user_cannot_grant_through_the_person(self):
        with self.assertRaises(AccessError):
            self.plain_person.with_user(self.plain).write(
                {'tool_assignment_ids': [(0, 0, {'tool_id': self.other_tool.id})]})
        self.assertEqual(self.plain_person.tool_assignment_ids, self.row)

    def test_plain_user_still_reads_both(self):
        self.assertEqual(self.tool.with_user(self.plain).name, 'Desk')
        self.assertEqual(self.row.with_user(self.plain).tool_id, self.tool)

    def test_admin_creates_and_writes_a_tool(self):
        tool = self.env['connect.tool'].with_user(self.admin).create(
            {'name': 'New', 'adapter': 'other_voice'})
        self.assertEqual(tool.create_uid, self.admin)
        tool.write({'name': 'Renamed'})
        self.assertEqual(tool.name, 'Renamed')

    def test_admin_creates_and_writes_a_row(self):
        row = self.env['connect.user.tool'].with_user(self.admin).create(self._grant_values())
        self.assertEqual(row.create_uid, self.admin)
        row.write({'sequence': 99})
        self.assertEqual(row.sequence, 99)

    def test_admin_edits_rows_on_the_person(self):
        """The Tools list on the Connect user form saves this way."""
        self.plain_person.with_user(self.admin).write({'tool_assignment_ids': [
            (0, 0, {'tool_id': self.other_tool.id, 'sequence': 1, 'use': 'voice'}),
            (1, self.row.id, {'sequence': 2}),
        ]})
        self.assertEqual(self.row.sequence, 2)
        self.assertEqual(self.plain_person.first_tool('voice'), self.other_tool)
        self.assertEqual(self.plain_person.originate_provider, 'other_voice')

    def test_plain_user_still_mints_a_token(self):
        mints = []
        case = self

        @api.model
        def get_client_token(model, nonce=False):
            mints.append(nonce)
            case.assertEqual(model.env.user, case.plain)
            return {'token': 'minted', 'edge': 'roaming'}

        self.patch(type(self.env['connect.user']), 'get_client_token', get_client_token)
        result = self.env['connect.user'].with_user(self.plain).get_phone_lines('tab-1')
        self.assertFalse(result['error'])
        self.assertEqual(mints, ['tab-1'])
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['lines'][0]['provider'], 'twilio')
        self.assertEqual(result['lines'][0]['tool_id'], self.tool.id)
        self.assertEqual(result['lines'][0]['token_data']['token'], 'minted')

    def test_plain_user_still_originates_on_their_line(self):
        """The fence reads the row, so it needs no write on it."""
        remembered = []

        def remember(model, number, res_model, res_id, user=None):
            remembered.append(model.env.user)

        Settings = type(self.env['connect.settings'])
        self.patch(Settings, '_remember_originate_origin', remember)
        self.patch(Settings, '_get_originate_provider', lambda model, user=None: 'elsewhere')
        self.patch(CoreSettings, 'originate_call', lambda model, number, **kwargs: 'reached')
        settings = self.env['connect.settings'].with_user(self.plain)
        try:
            settings.with_context(connect_tool_id=self.tool.id).originate_call('+15550100')
        except AccessError:
            raise
        except UserError as error:
            # With connect alone the wrapper holds the real base method.
            self.assertIn('No telephony module', str(error))
        self.assertEqual(remembered, [self.plain])

    def test_campaign_picker_offers_create_only_to_an_admin(self):
        """Odoo marks the picker from the ACL, so the form needs no no_create."""
        view = self.env.ref('connect.view_connect_campaign_form')

        def can_create(user):
            arch = self.env['connect.campaign'].with_user(user).get_view(view.id, 'form')['arch']
            return etree.fromstring(arch).xpath('//field[@name="tool_id"]')[0].get('can_create')

        self.assertEqual(can_create(self.plain), 'False')
        self.assertEqual(can_create(self.admin), 'True')

    def test_a_tool_the_user_cannot_grant_stays_refused(self):
        with self.assertRaises(AccessError):
            self.env['connect.user.tool'].with_user(self.plain).create(self._grant_values())
        settings = self.env['connect.settings'].with_user(self.plain)
        with self.assertRaises(AccessError):
            settings.with_context(connect_tool_id=self.other_tool.id).originate_call('+15550100')
