# -*- coding: utf-8 -*-
"""Connect on the standard settings screen.

The settings view uses priority 1000 so this app is last in the side
panel. Community modules are module_* checkboxes: saving installs them.
Enterprise modules use the same checkbox and stay read-only until the
license lists a Connect EE product or connect_enterprise is installed.
"""
from odoo import api, fields, models

# Names the license treats as Connect EE. A purchase of any one of them,
# or an installed connect_enterprise, unlocks the EE checkboxes.
CONNECT_EE_MODULES = (
    'connect_enterprise',
    'connect_enqueue',
    'connect_callout',
    'connect_portal',
    'connect_ai',
    'connect_stripe_enterprise',
)

# Settings-screen checkboxes backed by connect.settings, the record the call
# code reads. They are not config parameters: the screen must load the live
# value, or saving any app's settings writes False over it.
CONNECT_SETTINGS_FIELDS = {
    'connect_record_all_calls': 'record_all_calls',
    'connect_transcript_calls': 'transcript_calls',
    'connect_proxy_recordings': 'proxy_recordings',
    'connect_debug_mode': 'debug_mode',
}


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    module_connect_twilio = fields.Boolean(string='Twilio')
    module_connect_voicetel = fields.Boolean(string='VoiceTel')
    module_connect_byoc = fields.Boolean(string='Bring your own carrier')
    module_connect_pbx = fields.Boolean(string='Audio, voicemail and parking')
    module_connect_sms = fields.Boolean(string='SMS bridge')
    module_connect_crm = fields.Boolean(string='CRM')
    module_connect_website = fields.Boolean(string='Website')
    module_connect_helpdesk = fields.Boolean(string='Helpdesk')
    module_connect_stripe = fields.Boolean(string='Stripe')
    module_connect_s3 = fields.Boolean(string='S3 recording storage')
    module_connect_elevenlabs = fields.Boolean(string='ElevenLabs')
    module_connect_elevenlabs_knowledge = fields.Boolean(string='ElevenLabs knowledge')
    module_connect_elevenlabs_sale = fields.Boolean(string='ElevenLabs sale')
    module_connect_elevenlabs_helpdesk = fields.Boolean(string='ElevenLabs helpdesk')

    module_connect_enterprise = fields.Boolean(string='Connect EE')
    module_connect_enqueue = fields.Boolean(string='Call queues')
    module_connect_callout = fields.Boolean(string='Callout')
    module_connect_portal = fields.Boolean(string='Portal')
    module_connect_ai = fields.Boolean(string='AI queries')
    module_connect_stripe_enterprise = fields.Boolean(string='Stripe EE')

    connect_ee_entitled = fields.Boolean(
        string='Connect EE subscription',
        compute='_compute_connect_ee_entitled',
        help='Queues, callout, portal, and AI install when a Connect EE '
             'product is on the license.',
    )

    connect_record_all_calls = fields.Boolean(
        string='Record all calls',
        help='Record every call from answer. A stop during the call keeps '
             'the rest of that call silent.',
    )
    connect_transcript_calls = fields.Boolean(
        string='Transcribe calls',
    )
    connect_proxy_recordings = fields.Boolean(
        string='Proxy recordings',
        help='Re-stream recordings using Odoo user authentication.',
    )
    connect_debug_mode = fields.Boolean(
        string='Debug logging',
    )

    @api.depends_context('uid')
    def _compute_connect_ee_entitled(self):
        purchased = self.env['oduist.license'].purchased_module_names()
        installed = bool(self.env['ir.module.module'].sudo().search_count([
            ('name', '=', 'connect_enterprise'),
            ('state', '=', 'installed'),
        ]))
        entitled = installed or bool(purchased.intersection(CONNECT_EE_MODULES))
        for rec in self:
            rec.connect_ee_entitled = entitled

    def set_values(self):
        if not self.connect_ee_entitled:
            installed = set(self.env['ir.module.module'].sudo().search([
                ('name', 'in', list(CONNECT_EE_MODULES)),
                ('state', 'in', ('installed', 'to upgrade', 'to install')),
            ]).mapped('name'))
            for name in CONNECT_EE_MODULES:
                # A locked checkbox must not uninstall a module that is
                # already there, and must not install one that is not.
                self['module_%s' % name] = name in installed
        super().set_values()
        Settings = self.env['connect.settings'].sudo()
        for source, target in CONNECT_SETTINGS_FIELDS.items():
            Settings.set_param(target, self[source])

    @api.model
    def get_values(self):
        res = super().get_values()
        Settings = self.env['connect.settings'].sudo()
        for source, target in CONNECT_SETTINGS_FIELDS.items():
            res[source] = bool(Settings.get_param(target))
        return res
