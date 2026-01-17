# -*- coding: utf-8 -*-

import logging
from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError

logger = logging.getLogger(__name__)


class ElevenlabsPhoneRegistration(models.Model):
    _name = 'connect.elevenlabs_phone_registration'
    _description = 'ElevenLabs Registered Phone Number'
    _order = 'name'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(required=True, tracking=True, help="Display name for this phone registration")
    active = fields.Boolean(default=True)
    outgoing_callerid_id = fields.Many2one(
        'connect.outgoing_callerid',
        string="Twilio Phone Number",
        required=True,
        ondelete='cascade',
        tracking=True,
        help="The Twilio phone number to register with ElevenLabs for outbound calling",
    )
    phone_number = fields.Char(
        related='outgoing_callerid_id.number',
        store=True,
        help="Phone number in E.164 format",
    )

    # ElevenLabs sync fields
    elevenlabs_phone_id = fields.Char(
        string="ElevenLabs Phone ID",
        readonly=True,
        copy=False,
        help="Unique identifier from ElevenLabs after registration",
    )
    sync_status = fields.Selection([
        ('draft', 'Not Registered'),
        ('synced', 'Registered'),
        ('error', 'Error'),
    ], default='draft', tracking=True, help="Registration status with ElevenLabs")
    sync_error = fields.Text(readonly=True, help="Last registration error message")
    last_sync = fields.Datetime(readonly=True, help="Last successful sync timestamp")

    # Configuration
    label = fields.Char(help="Optional label for the phone number in ElevenLabs")

    _outgoing_callerid_uniq = models.Constraint(
        "UNIQUE(outgoing_callerid_id)",
        "This phone number is already registered with ElevenLabs!"
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if not self.env.context.get('skip_elevenlabs'):
                try:
                    rec.register_with_elevenlabs()
                except Exception as e:
                    logger.exception("Error registering phone with ElevenLabs: %s", e)
                    rec.with_context(skip_elevenlabs=True).write({
                        'sync_status': 'error',
                        'sync_error': str(e),
                    })
        return records

    def unlink(self):
        for rec in self:
            if rec.elevenlabs_phone_id and not self.env.context.get('skip_elevenlabs'):
                try:
                    rec.unregister_from_elevenlabs()
                except Exception as e:
                    logger.warning("Could not unregister phone from ElevenLabs: %s", e)
        return super().unlink()

    def register_with_elevenlabs(self):
        """Register this phone number with ElevenLabs for outbound calling."""
        self.ensure_one()
        if not self.outgoing_callerid_id:
            raise ValidationError("Please select a Twilio phone number first.")

        # Get Twilio credentials
        settings = self.env['connect.settings'].sudo()
        twilio_account_sid = settings.get_param('twilio_sid')
        twilio_auth_token = settings.get_param('twilio_token')

        if not twilio_account_sid or not twilio_auth_token:
            raise ValidationError("Twilio credentials not configured in Connect settings.")

        # Get ElevenLabs client
        client = settings.get_elevenlabs_client()

        try:
            # Register phone number with ElevenLabs
            # API: POST /v1/convai/twilio/phone-numbers
            response = client.conversational_ai.twilio.add_phone_number(
                phone_number=self.phone_number,
                twilio_account_sid=twilio_account_sid,
                twilio_auth_token=twilio_auth_token,
                label=self.label or self.name,
            )

            self.with_context(skip_elevenlabs=True).write({
                'elevenlabs_phone_id': response.phone_number_id if hasattr(response, 'phone_number_id') else str(response),
                'sync_status': 'synced',
                'last_sync': fields.Datetime.now(),
                'sync_error': False,
            })

            logger.info("Registered phone %s with ElevenLabs: %s",
                       self.phone_number, self.elevenlabs_phone_id)

        except Exception as e:
            self.with_context(skip_elevenlabs=True).write({
                'sync_status': 'error',
                'sync_error': str(e),
            })
            raise UserError(f"Failed to register phone with ElevenLabs: {e}")

    def unregister_from_elevenlabs(self):
        """Remove this phone number registration from ElevenLabs."""
        self.ensure_one()
        if not self.elevenlabs_phone_id:
            return

        settings = self.env['connect.settings'].sudo()
        client = settings.get_elevenlabs_client()

        try:
            # Delete phone number from ElevenLabs
            client.conversational_ai.twilio.delete_phone_number(
                phone_number_id=self.elevenlabs_phone_id
            )
            logger.info("Unregistered phone %s from ElevenLabs", self.phone_number)
        except Exception as e:
            logger.warning("Error unregistering phone from ElevenLabs: %s", e)
            raise

    def action_register(self):
        """Manual action to register/re-register with ElevenLabs."""
        self.ensure_one()
        self.register_with_elevenlabs()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Registration Complete',
                'message': f'Phone {self.phone_number} registered with ElevenLabs',
                'type': 'success',
            },
        }

    def action_unregister(self):
        """Manual action to unregister from ElevenLabs."""
        self.ensure_one()
        self.unregister_from_elevenlabs()
        self.with_context(skip_elevenlabs=True).write({
            'elevenlabs_phone_id': False,
            'sync_status': 'draft',
            'sync_error': False,
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Unregistration Complete',
                'message': f'Phone {self.phone_number} unregistered from ElevenLabs',
                'type': 'warning',
            },
        }

    @api.model
    def sync_from_elevenlabs(self):
        """Sync registered phone numbers from ElevenLabs."""
        settings = self.env['connect.settings'].sudo()
        client = settings.get_elevenlabs_client()

        try:
            response = client.conversational_ai.twilio.get_phone_numbers()
            phone_numbers = response.phone_numbers if hasattr(response, 'phone_numbers') else []
        except Exception as e:
            raise UserError(f"Failed to fetch phone numbers from ElevenLabs: {e}")

        synced = 0
        for el_phone in phone_numbers:
            phone_id = el_phone.phone_number_id if hasattr(el_phone, 'phone_number_id') else None
            phone_number = el_phone.phone_number if hasattr(el_phone, 'phone_number') else None

            if not phone_id or not phone_number:
                continue

            # Find existing registration by phone_id or phone_number
            existing = self.search([
                '|',
                ('elevenlabs_phone_id', '=', phone_id),
                ('phone_number', '=', phone_number),
            ], limit=1)

            if existing:
                existing.with_context(skip_elevenlabs=True).write({
                    'elevenlabs_phone_id': phone_id,
                    'sync_status': 'synced',
                    'last_sync': fields.Datetime.now(),
                    'sync_error': False,
                })
                synced += 1

        settings.connect_notify(
            f"Synced {synced} phone registration(s)",
            title='ElevenLabs Phone Sync',
            notify_uid=self.env.user.id,
        )

        return synced
