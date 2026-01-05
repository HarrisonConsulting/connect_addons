# -*- coding: utf-8 -*-
"""
ElevenLabs Phone Registration - Connect Integration.

Extends elevenlabs.phone.registration with Connect telephony integration,
providing linkage to connect.outgoing_callerid and Twilio credentials.
"""
import logging
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

logger = logging.getLogger(__name__)


class ElevenLabsPhoneRegistrationConnect(models.Model):
    """
    Extend elevenlabs.phone.registration with Connect integration.

    Links ElevenLabs phone registration to Connect's outgoing_callerid
    and provides Twilio credentials from Connect settings.
    """
    _inherit = 'elevenlabs.phone.registration'

    # === Connect Integration ===
    outgoing_callerid_id = fields.Many2one(
        comodel_name='connect.outgoing_callerid',
        string='Twilio Phone Number',
        ondelete='cascade',
        tracking=True,
        help='The Twilio phone number from Connect to register with ElevenLabs'
    )

    @api.onchange('outgoing_callerid_id')
    def _onchange_outgoing_callerid_id(self):
        """Auto-fill phone number from outgoing_callerid_id."""
        if self.outgoing_callerid_id:
            self.phone_number = self.outgoing_callerid_id.number
            if not self.name:
                self.name = f"ElevenLabs Registration - {self.phone_number}"

    def _get_twilio_credentials(self):
        """
        Get Twilio credentials for registration.

        First checks for override credentials on this record,
        then falls back to Connect settings.

        Returns:
            tuple: (account_sid, auth_token)
        """
        self.ensure_one()

        # Use override credentials if set
        if self.twilio_account_sid and self.twilio_auth_token:
            return (self.twilio_account_sid, self.twilio_auth_token)

        # Get from Connect settings
        try:
            settings = self.env['connect.settings'].sudo()
            account_sid = settings.get_param('twilio_sid')
            auth_token = settings.get_param('twilio_token')
            if account_sid and auth_token:
                return (account_sid, auth_token)
        except Exception as e:
            logger.debug("Could not get Twilio credentials from connect settings: %s", e)

        # No credentials found
        raise ValidationError(_(
            'Twilio credentials not configured. '
            'Please configure them in this registration or in Connect settings.'
        ))
