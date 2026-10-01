# -*- coding: utf-8 -*-
"""A campaign names the voice system a call uses."""

from uuid import uuid4

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCampaignTool(TransactionCase):

    def test_campaign_context_names_the_system(self):
        sip_tool = self.env['connect.tool'].create({
            'name': 'sip-%s' % uuid4().hex,
            'adapter': 'sip',
        })
        sip_campaign = self.env['connect.campaign'].create({
            'name': 'Desk campaign %s' % uuid4().hex,
            'tool_id': sip_tool.id,
        })
        self.assertEqual(sip_campaign.originate_context(), {
            'connect_tool_id': sip_tool.id,
            'connect_campaign_id': sip_campaign.id,
        })
        # sip is not a click-to-call key, so the stored fallback still applies.
        options = self.env['connect.user']._fields['originate_provider'].get_values(
            self.env)
        try:
            provider = self.env['connect.settings'].with_context(
                **sip_campaign.originate_context(),
            )._get_originate_provider()
        except UserError:
            provider = False
        self.assertNotEqual(provider, 'sip')

        if 'twilio' in options:
            login = 'campaign-%s' % uuid4().hex
            odoo_user = self.env['res.users'].with_context(
                no_reset_password=True,
                tracking_disable=True,
            ).create({
                'name': login,
                'login': login,
                'share': False,
                'group_ids': [(4, self.env.ref('base.group_user').id)],
            })
            connect_user = self.env['connect.user'].with_context(
                no_clear_cache=True,
            ).create({'user': odoo_user.id})
            self.assertFalse(connect_user.originate_provider)
            twilio_tool = self.env['connect.tool'].create({
                'name': 'twilio-%s' % uuid4().hex,
                'adapter': 'twilio',
            })
            campaign = self.env['connect.campaign'].create({
                'name': 'Twilio campaign %s' % uuid4().hex,
                'tool_id': twilio_tool.id,
                'user_id': connect_user.id,
            })
            provider = self.env['connect.settings'].with_context(
                **campaign.originate_context(),
            )._get_originate_provider(user=odoo_user)
            self.assertEqual(provider, 'twilio')
            call = self.env['connect.call'].with_context(
                **campaign.originate_context(),
            ).create({})
            self.assertEqual(call.tool_id, twilio_tool)
