# -*- coding: utf-8 -*-
"""Naming a tool or campaign on a call does not make it the caller's line."""

from uuid import uuid4

from odoo.addons.connect.models.settings import Settings as CoreSettings
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from .test_phone_lines import patch_line_options

NUMBER = '+15550100'


@tagged('post_install', '-at_install')
class TestLineAuthority(TransactionCase):

    def setUp(self):
        super().setUp()
        patch_line_options(self)
        self.odoo_user = new_test_user(
            self.env, login='line-auth-%s' % uuid4().hex,
            groups='base.group_user,connect.group_user')
        self.connect_user = self.env['connect.user'].with_context(
            no_clear_cache=True, no_twilio_create=True,
        ).create({'user': self.odoo_user.id})
        self.other_user = new_test_user(self.env, login='line-auth-other-%s' % uuid4().hex)
        self.tool = self.env['connect.tool'].create({'name': 'Line', 'adapter': 'other_voice'})
        self.settings = self.env['connect.settings'].with_user(self.odoo_user)
        self.reached = []
        self.contexts = []
        case = self

        def base(model, number, res_model=None, res_id=None, user=None, **kwargs):
            case.reached.append(kwargs)
            return 'reached'

        def remember(model, number, res_model, res_id, user=None):
            case.contexts.append(dict(model.env.context))

        Settings = type(self.env['connect.settings'])
        self.patch(CoreSettings, 'originate_call', base)
        self.patch(Settings, '_remember_originate_origin', remember)
        self.patch(Settings, '_get_originate_provider', lambda model, user=None: 'elsewhere')

    def _row(self, tool=None, **values):
        return self.env['connect.user.tool'].create({
            'user_id': self.connect_user.id,
            'tool_id': (tool or self.tool).id,
            'role': 'active',
            'use': 'voice',
            **values,
        })

    def _assert_passes(self, settings, **kwargs):
        """The call got past the fence. With connect alone nothing handles it, and
        the wrapper holds the real base method, so the base refuses it instead."""
        try:
            settings.originate_call(NUMBER, **kwargs)
        except AccessError:
            raise
        except UserError as error:
            self.assertIn('No telephony module', str(error))
        self.assertTrue(self.contexts, 'The call passed the fence.')

    def _assert_refused(self, settings, **kwargs):
        with self.assertRaises(AccessError):
            settings.originate_call(NUMBER, **kwargs)
        self.assertFalse(self.reached)
        self.assertFalse(self.contexts, 'Nothing is remembered for a refused call.')

    def test_superuser_passes(self):
        self._assert_passes(self.settings.sudo().with_context(connect_tool_id=self.tool.id))

    def test_row_passes(self):
        self._row()
        self._assert_passes(self.settings.with_context(connect_tool_id=self.tool.id))

    def test_no_row_refused(self):
        self._assert_refused(self.settings.with_context(connect_tool_id=self.tool.id))

    def test_no_connect_user_refused(self):
        stranger = new_test_user(self.env, login='line-auth-none-%s' % uuid4().hex)
        self._assert_refused(
            self.env['connect.settings'].with_user(stranger).with_context(
                connect_tool_id=self.tool.id))

    def test_backup_row_refused(self):
        self._row(role='backup')
        self._assert_refused(self.settings.with_context(connect_tool_id=self.tool.id))

    def test_message_row_refused(self):
        self._row(use='message')
        self._assert_refused(self.settings.with_context(connect_tool_id=self.tool.id))

    def test_retired_tool_refused(self):
        self._row()
        self.tool.active = False
        self._assert_refused(self.settings.with_context(connect_tool_id=self.tool.id))

    def test_other_company_tool_refused(self):
        self._row()
        self.tool.company_id = self.env['res.company'].create({'name': 'Other %s' % uuid4().hex})
        self._assert_refused(self.settings.with_context(connect_tool_id=self.tool.id))

    def test_own_campaign_gives_no_authority(self):
        campaign = self.env['connect.campaign'].with_user(self.odoo_user).create({
            'name': 'Mine', 'tool_id': self.tool.id,
        })
        self._assert_refused(self.settings.with_context(
            connect_tool_id=self.tool.id, connect_campaign_id=campaign.id))
        self._assert_refused(self.settings.with_context(connect_campaign_id=campaign.id))
        self._assert_refused(
            self.settings.with_context(connect_campaign_id=campaign.id),
            user=self.other_user.id)

    def test_campaign_uses_the_campaign_tool(self):
        self._row()
        campaign = self.env['connect.campaign'].with_user(self.odoo_user).create({
            'name': 'Mine', 'tool_id': self.tool.id,
        })
        self._assert_passes(self.settings.with_context(connect_campaign_id=campaign.id))

    def test_another_user_refused_with_a_context_tool(self):
        self._row()
        settings = self.settings.with_context(connect_tool_id=self.tool.id)
        self._assert_refused(settings, user=self.other_user.id)
        self._assert_refused(settings, user=self.other_user)

    def test_own_user_passes_with_a_context_tool(self):
        self._row()
        self._assert_passes(
            self.settings.with_context(connect_tool_id=self.tool.id),
            user=self.odoo_user.id)

    def test_another_user_refused_on_a_context_tool_that_is_no_provider(self):
        sip = self.env['connect.tool'].create({'name': 'Desk', 'adapter': 'sip'})
        self._assert_refused(
            self.settings.with_context(connect_tool_id=sip.id), user=self.other_user.id)

    def test_uncontexted_call_is_unchanged(self):
        self._assert_passes(self.settings)
        self._assert_passes(self.settings, user=self.other_user.id)

    def test_sip_context_falls_through_as_before(self):
        sip = self.env['connect.tool'].create({'name': 'Desk', 'adapter': 'sip'})
        self._assert_passes(self.settings.with_context(connect_tool_id=sip.id))

    def test_caller_id_moves_to_context(self):
        self._row()
        self._assert_passes(
            self.settings.with_context(connect_tool_id=self.tool.id),
            caller_id='+15550199', origin_nonce='tab-1')
        self.assertEqual(self.contexts[0]['connect_caller_id'], '+15550199')
        for kwargs in self.reached:
            self.assertNotIn('caller_id', kwargs)
            self.assertEqual(kwargs['origin_nonce'], 'tab-1')

    def test_no_caller_id_leaves_context_alone(self):
        self._assert_passes(self.settings, caller_id='')
        self.assertNotIn('connect_caller_id', self.contexts[0])
        for kwargs in self.reached:
            self.assertNotIn('caller_id', kwargs)

    def test_resolver_and_fence_use_one_tool(self):
        other = self.env['connect.tool'].create({'name': 'Other', 'adapter': 'twilio'})
        campaign = self.env['connect.campaign'].create({'name': 'Theirs', 'tool_id': other.id})
        settings = self.env['connect.settings']
        options = ['twilio', 'other_voice']
        contexted = settings.with_context(
            connect_tool_id=self.tool.id, connect_campaign_id=campaign.id)
        self.assertEqual(contexted._context_tool(options), self.tool)
        self.assertEqual(contexted._context_provider_key(options), 'other_voice')
        campaign_only = settings.with_context(connect_campaign_id=campaign.id)
        self.assertEqual(campaign_only._context_tool(options), other)
        self.assertEqual(campaign_only._context_provider_key(options), 'twilio')
        self.assertFalse(settings._context_tool(options))
        self.assertIs(settings._context_provider_key(options), False)
        self.assertIs(campaign_only._context_provider_key([]), False)
