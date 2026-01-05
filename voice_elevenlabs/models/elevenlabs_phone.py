# -*- coding: utf-8 -*-
"""
ElevenLabs Phone Registration

Manages Twilio phone number registration with ElevenLabs for outbound calling.
Integrates with connect.outgoing_callerid for phone number management.
"""
import logging
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

logger = logging.getLogger(__name__)


class ElevenLabsPhoneRegistration(models.Model):
    """
    Phone number registration with ElevenLabs for Twilio integration.

    This model manages the registration of Twilio phone numbers with
    ElevenLabs to enable outbound calling through ElevenLabs agents.
    """
    _name = 'elevenlabs.phone.registration'
    _description = 'ElevenLabs Phone Registration'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name'

    # === Identity ===
    name = fields.Char(
        string='Name',
        required=True,
        tracking=True,
        help='Display name for this phone registration'
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        tracking=True,
        help='Whether this registration is active'
    )

    # === Provider Link ===
    provider_id = fields.Many2one(
        comodel_name='voice.provider.elevenlabs',
        string='ElevenLabs Provider',
        required=True,
        ondelete='cascade',
        tracking=True,
        help='The ElevenLabs provider for this registration'
    )

    # === Phone Number (requires connect module) ===
    # Note: This field requires connect.outgoing_callerid model
    # If connect module is not installed, this will be a Char field
    outgoing_callerid_id = fields.Many2one(
        comodel_name='connect.outgoing_callerid',
        string='Twilio Phone Number',
        ondelete='cascade',
        tracking=True,
        help='The Twilio phone number to register with ElevenLabs'
    )
    phone_number = fields.Char(
        string='Phone Number',
        required=True,
        tracking=True,
        help='Phone number in E.164 format (e.g., +14155551234)'
    )

    # === ElevenLabs Integration ===
    elevenlabs_phone_id = fields.Char(
        string='ElevenLabs Phone ID',
        readonly=True,
        copy=False,
        help='Unique identifier from ElevenLabs after registration'
    )
    sync_status = fields.Selection(
        selection=[
            ('draft', 'Not Registered'),
            ('syncing', 'Registering'),
            ('synced', 'Registered'),
            ('error', 'Error'),
        ],
        string='Status',
        default='draft',
        tracking=True,
        help='Registration status with ElevenLabs'
    )
    sync_error = fields.Text(
        string='Error Message',
        readonly=True,
        help='Last registration error message'
    )
    last_sync = fields.Datetime(
        string='Last Synced',
        readonly=True,
        help='Last successful sync timestamp'
    )

    # === Configuration ===
    label = fields.Char(
        string='Label',
        help='Optional label for the phone number in ElevenLabs'
    )

    # === Twilio Credentials ===
    # These are typically stored at provider level or in connect settings
    # but can be overridden per phone registration if needed
    twilio_account_sid = fields.Char(
        string='Twilio Account SID',
        groups='base.group_system',
        help='Override Twilio Account SID for this registration (optional)'
    )
    twilio_auth_token = fields.Char(
        string='Twilio Auth Token',
        groups='base.group_system',
        help='Override Twilio Auth Token for this registration (optional)'
    )

    _sql_constraints = [
        ('unique_provider_phone', 'unique(provider_id, phone_number)',
         'This phone number is already registered with this provider!'),
    ]

    @api.onchange('outgoing_callerid_id')
    def _onchange_outgoing_callerid_id(self):
        """Auto-fill phone number from outgoing_callerid_id."""
        if self.outgoing_callerid_id:
            self.phone_number = self.outgoing_callerid_id.number
            if not self.name:
                self.name = f"ElevenLabs Registration - {self.phone_number}"

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-register with ElevenLabs on create."""
        records = super().create(vals_list)
        for record in records:
            if not self.env.context.get('skip_elevenlabs_registration'):
                try:
                    record.action_register()
                except Exception as e:
                    logger.exception("Error auto-registering phone: %s", e)
                    record.write({
                        'sync_status': 'error',
                        'sync_error': str(e),
                    })
        return records

    def unlink(self):
        """Auto-unregister from ElevenLabs on delete."""
        for record in self:
            if record.elevenlabs_phone_id and not self.env.context.get('skip_elevenlabs_registration'):
                try:
                    record.action_unregister()
                except Exception as e:
                    logger.warning("Could not unregister phone from ElevenLabs: %s", e)
        return super().unlink()

    def _get_twilio_credentials(self):
        """
        Get Twilio credentials for registration.

        Returns:
            tuple: (account_sid, auth_token)
        """
        self.ensure_one()

        # Use override credentials if set
        if self.twilio_account_sid and self.twilio_auth_token:
            return (self.twilio_account_sid, self.twilio_auth_token)

        # Try to get from connect settings if available
        if hasattr(self.env, 'ref') and self.env.ref('connect.module_connect', raise_if_not_found=False):
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

    def action_register(self):
        """Register this phone number with ElevenLabs."""
        self.ensure_one()

        if not self.phone_number:
            raise ValidationError(_('Phone number is required for registration.'))

        if not self.provider_id:
            raise ValidationError(_('Provider is required for registration.'))

        # Get Twilio credentials
        try:
            twilio_sid, twilio_token = self._get_twilio_credentials()
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(_('Failed to get Twilio credentials: %s') % str(e))

        # Get ElevenLabs client
        try:
            client = self.provider_id.get_client()
        except Exception as e:
            raise UserError(_('Failed to get ElevenLabs client: %s') % str(e))

        self.write({'sync_status': 'syncing'})

        try:
            # Register phone number with ElevenLabs
            # API: POST /v1/convai/twilio/phone-numbers
            response = client.conversational_ai.twilio.add_phone_number(
                phone_number=self.phone_number,
                twilio_account_sid=twilio_sid,
                twilio_auth_token=twilio_token,
                label=self.label or self.name,
            )

            # Extract phone_number_id from response
            phone_id = getattr(response, 'phone_number_id', None) or str(response)

            self.write({
                'elevenlabs_phone_id': phone_id,
                'sync_status': 'synced',
                'last_sync': fields.Datetime.now(),
                'sync_error': False,
            })

            logger.info(
                "Registered phone %s with ElevenLabs (ID: %s)",
                self.phone_number, self.elevenlabs_phone_id
            )

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Registration Complete'),
                    'message': _('Phone %s registered with ElevenLabs') % self.phone_number,
                    'type': 'success',
                    'sticky': False,
                }
            }

        except Exception as e:
            error_msg = str(e)
            self.write({
                'sync_status': 'error',
                'sync_error': error_msg,
            })
            logger.error("Failed to register phone with ElevenLabs: %s", e)
            raise UserError(_('Registration failed: %s') % error_msg)

    def action_unregister(self):
        """Unregister this phone number from ElevenLabs."""
        self.ensure_one()

        if not self.elevenlabs_phone_id:
            raise ValidationError(_(
                'Phone number is not registered with ElevenLabs. '
                'Cannot unregister.'
            ))

        # Get ElevenLabs client
        try:
            client = self.provider_id.get_client()
        except Exception as e:
            raise UserError(_('Failed to get ElevenLabs client: %s') % str(e))

        try:
            # Delete phone number from ElevenLabs
            # API: DELETE /v1/convai/twilio/phone-numbers/{phone_number_id}
            client.conversational_ai.twilio.delete_phone_number(
                phone_number_id=self.elevenlabs_phone_id
            )

            logger.info(
                "Unregistered phone %s from ElevenLabs",
                self.phone_number
            )

            self.write({
                'elevenlabs_phone_id': False,
                'sync_status': 'draft',
                'sync_error': False,
            })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Unregistration Complete'),
                    'message': _('Phone %s unregistered from ElevenLabs') % self.phone_number,
                    'type': 'warning',
                    'sticky': False,
                }
            }

        except Exception as e:
            logger.error("Failed to unregister phone from ElevenLabs: %s", e)
            raise UserError(_('Unregistration failed: %s') % str(e))

    def action_sync_status(self):
        """Sync registration status from ElevenLabs."""
        self.ensure_one()

        try:
            client = self.provider_id.get_client()

            # Get all registered phone numbers
            response = client.conversational_ai.twilio.get_phone_numbers()
            phone_numbers = getattr(response, 'phone_numbers', [])

            # Find our phone in the list
            found = False
            for el_phone in phone_numbers:
                phone_id = getattr(el_phone, 'phone_number_id', None)
                phone_num = getattr(el_phone, 'phone_number', None)

                if phone_id == self.elevenlabs_phone_id or phone_num == self.phone_number:
                    # Phone is registered
                    self.write({
                        'elevenlabs_phone_id': phone_id,
                        'sync_status': 'synced',
                        'last_sync': fields.Datetime.now(),
                        'sync_error': False,
                    })
                    found = True
                    break

            if not found:
                # Phone not found in ElevenLabs
                self.write({
                    'sync_status': 'error',
                    'sync_error': 'Phone not found in ElevenLabs. May need to re-register.',
                })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sync Complete'),
                    'message': _('Registration status synced'),
                    'type': 'success' if found else 'warning',
                    'sticky': False,
                }
            }

        except Exception as e:
            logger.error("Failed to sync phone status: %s", e)
            raise UserError(_('Status sync failed: %s') % str(e))

    @api.model
    def sync_all_from_elevenlabs(self, provider_id=None):
        """
        Sync all registered phone numbers from ElevenLabs.

        Args:
            provider_id (int, optional): Specific provider to sync

        Returns:
            int: Number of phones synced
        """
        domain = []
        if provider_id:
            domain.append(('id', '=', provider_id))

        providers = self.env['voice.provider.elevenlabs'].search(domain)

        synced_count = 0
        for provider in providers:
            try:
                client = provider.get_client()
                response = client.conversational_ai.twilio.get_phone_numbers()
                phone_numbers = getattr(response, 'phone_numbers', [])

                for el_phone in phone_numbers:
                    phone_id = getattr(el_phone, 'phone_number_id', None)
                    phone_num = getattr(el_phone, 'phone_number', None)

                    if not phone_id or not phone_num:
                        continue

                    # Find existing registration
                    existing = self.search([
                        ('provider_id', '=', provider.id),
                        '|',
                        ('elevenlabs_phone_id', '=', phone_id),
                        ('phone_number', '=', phone_num),
                    ], limit=1)

                    if existing:
                        existing.write({
                            'elevenlabs_phone_id': phone_id,
                            'sync_status': 'synced',
                            'last_sync': fields.Datetime.now(),
                            'sync_error': False,
                        })
                        synced_count += 1

            except Exception as e:
                logger.error("Failed to sync phones from provider %s: %s", provider.name, e)

        return synced_count
