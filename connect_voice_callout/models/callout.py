# -*- coding: utf-8 -*-
"""
Connect Voice Callout extension.

Extends connect.callout with voice AI agent integration, supporting both
traditional TwiML and AI agent call modes.
"""
import logging
from odoo import models, fields, api, release, _
from odoo.exceptions import ValidationError, UserError
from odoo.addons.connect.models.settings import strip_number, debug

logger = logging.getLogger(__name__)


CALL_MODE_SELECTION = [
    ('twiml', 'TwiML (Traditional)'),
    ('agent', 'AI Agent'),
]


class VoiceCallout(models.Model):
    """
    Extend connect.callout with voice AI capabilities.

    Provides provider-agnostic voice AI integration for outbound calling
    campaigns, replacing the legacy connect_elevenlabs_callout module.
    """
    _inherit = 'connect.callout'

    # === Voice Provider Configuration ===
    voice_enabled = fields.Boolean(
        compute='_compute_voice_enabled',
        string='Voice AI Enabled',
        help='Whether voice AI is configured and available'
    )

    # === Call Mode ===
    call_mode = fields.Selection(
        selection=CALL_MODE_SELECTION,
        default='twiml',
        required=True,
        help="TwiML: Traditional automated calling with prompts and DTMF. "
             "Agent: AI-powered conversational calls using voice agents.",
    )

    # === Agent Configuration ===
    voice_agent_id = fields.Many2one(
        comodel_name='connect.voice.agent',
        string='Voice Agent',
        ondelete='restrict',
        help='Voice AI agent to handle outbound calls'
    )
    agent_first_message = fields.Text(
        string='First Message Override',
        help='Override the agent\'s default first message. Use {{contact_name}} for personalization.'
    )
    agent_max_duration = fields.Integer(
        string='Max Duration (seconds)',
        default=300,
        help='Maximum call duration in seconds (default: 5 minutes)'
    )

    # === TTS Files for TwiML Mode ===
    prompt_message_file_id = fields.Many2one(
        comodel_name='voice.tts.file',
        string='Prompt Audio File',
        ondelete='set null',
        help='Pre-generated audio file for the prompt message'
    )
    after_choice_message_file_id = fields.Many2one(
        comodel_name='voice.tts.file',
        string='After Choice Audio File',
        ondelete='set null',
        help='Pre-generated audio file for the after-choice message'
    )
    invalid_input_message_file_id = fields.Many2one(
        comodel_name='voice.tts.file',
        string='Invalid Input Audio File',
        ondelete='set null',
        help='Pre-generated audio file for the invalid input message'
    )

    # === Audio Preview Widgets (Odoo 17+) ===
    if release.version_info[0] >= 17.0:
        prompt_message_widget = fields.Html(
            related='prompt_message_file_id.preview_audio',
            string='Prompt Preview'
        )
        after_choice_message_widget = fields.Html(
            related='after_choice_message_file_id.preview_audio',
            string='After Choice Preview'
        )
        invalid_input_message_widget = fields.Html(
            related='invalid_input_message_file_id.preview_audio',
            string='Invalid Input Preview'
        )
    else:
        prompt_message_widget = fields.Char(
            related='prompt_message_file_id.preview_audio',
            string='Prompt Preview'
        )
        after_choice_message_widget = fields.Char(
            related='after_choice_message_file_id.preview_audio',
            string='After Choice Preview'
        )
        invalid_input_message_widget = fields.Char(
            related='invalid_input_message_file_id.preview_audio',
            string='Invalid Input Preview'
        )

    def _compute_voice_enabled(self):
        """Check if voice AI is available."""
        for rec in self:
            # Check if any voice provider is configured
            provider_count = self.env['voice.provider'].search_count([
                ('active', '=', True)
            ])
            rec.voice_enabled = provider_count > 0

    @api.constrains('prompt_message')
    def _generate_prompt_message_file(self):
        """Generate TTS file when prompt message changes."""
        for rec in self:
            if rec.voice_enabled and rec.prompt_message:
                rec._generate_tts_file('prompt_message', 'prompt_message_file_id')

    @api.constrains('after_choice_message')
    def _generate_after_choice_message_file(self):
        """Generate TTS file when after-choice message changes."""
        for rec in self:
            if rec.voice_enabled and rec.after_choice_message:
                rec._generate_tts_file('after_choice_message', 'after_choice_message_file_id')

    @api.constrains('invalid_input_message')
    def _generate_invalid_input_message_file(self):
        """Generate TTS file when invalid input message changes."""
        for rec in self:
            if rec.voice_enabled and rec.invalid_input_message:
                rec._generate_tts_file('invalid_input_message', 'invalid_input_message_file_id')

    def _generate_tts_file(self, text_field, file_field):
        """
        Generate or update TTS audio file for a message field.

        Args:
            text_field (str): Name of the text field
            file_field (str): Name of the file Many2one field
        """
        self = self.sudo()
        self.ensure_one()

        text = getattr(self, text_field, '')
        if not text:
            # Remove file if text is empty
            file_rec = getattr(self, file_field)
            if file_rec:
                file_rec.unlink()
            return

        # Format text with placeholders
        if '{gather_timeout}' in text:
            text = text.format(gather_timeout=self.gather_timeout)

        # Get the default voice provider
        provider = self.env['voice.provider'].search([
            ('is_default', '=', True),
            ('active', '=', True),
        ], limit=1)

        if not provider:
            logger.warning('No default voice provider configured')
            return

        file_rec = getattr(self, file_field)
        if file_rec:
            # Update existing file
            file_rec.text = text
        else:
            # Create new file
            new_file = self.env['voice.tts.file'].create({
                'text': text,
                'voice_provider_id': provider.id,
            })
            setattr(self, file_field, new_file.id)

    def get_prompt_message(self, gather):
        """Override to use TTS audio file when available."""
        try:
            self = self.sudo()
            if self.voice_enabled and self.prompt_message_file_id and self.prompt_message_file_id.audio_file:
                gather.play(self.prompt_message_file_id.get_file_url())
                return
        except Exception as e:
            logger.error('Voice TTS error for prompt_message: %s', e)
        return super().get_prompt_message(gather)

    def get_after_choice_message(self, response):
        """Override to use TTS audio file when available."""
        try:
            self = self.sudo()
            if self.voice_enabled and self.after_choice_message_file_id and self.after_choice_message_file_id.audio_file:
                response.play(self.after_choice_message_file_id.get_file_url())
                return
        except Exception as e:
            logger.error('Voice TTS error for after_choice_message: %s', e)
        return super().get_after_choice_message(response)

    def get_invalid_input_message(self, response):
        """Override to use TTS audio file when available."""
        try:
            self = self.sudo()
            if self.voice_enabled and self.invalid_input_message_file_id and self.invalid_input_message_file_id.audio_file:
                response.play(self.invalid_input_message_file_id.get_file_url())
                return
        except Exception as e:
            logger.error('Voice TTS error for invalid_input_message: %s', e)
        return super().get_invalid_input_message(response)

    # === Agent Mode Methods ===

    @api.constrains('call_mode', 'voice_agent_id')
    def _check_agent_mode_requirements(self):
        """Validate agent mode configuration."""
        for rec in self:
            if rec.call_mode == 'agent':
                if not rec.voice_agent_id:
                    raise ValidationError(_(
                        'Voice Agent is required when using Agent call mode.'
                    ))
                if not rec.voice_agent_id.voice_provider_id:
                    raise ValidationError(_(
                        'The selected Voice Agent must have a provider configured.'
                    ))
                if not rec.voice_agent_id.external_agent_id:
                    raise ValidationError(_(
                        'The selected Voice Agent must be synced with the provider.'
                    ))

    def originate_call(self, contact):
        """Override to route agent mode calls to voice provider."""
        self.ensure_one()
        if self.call_mode == 'agent':
            return self._originate_agent_call(contact)
        return super().originate_call(contact)

    def _originate_agent_call(self, contact):
        """
        Initiate an outbound call using voice AI agent.

        Uses the provider's outbound calling API to initiate a call
        where an AI agent handles the conversation.
        """
        self.ensure_one()

        if not self.voice_agent_id:
            raise UserError(_('No Voice Agent configured for this callout.'))

        provider = self.voice_agent_id.voice_provider_id
        if not provider:
            raise UserError(_('Voice Agent has no provider configured.'))

        # Get phone number to call
        number = strip_number(contact.phone_number)
        if hasattr(self, 'test_to') and self.test_to:
            number = self.test_to

        # Build dynamic variables for personalization
        dynamic_variables = self._build_agent_dynamic_variables(contact)

        try:
            # Prepare first message override if set
            first_message = None
            if self.agent_first_message:
                first_message = self.agent_first_message
                # Replace placeholders
                for key, value in dynamic_variables.items():
                    first_message = first_message.replace('{{' + key + '}}', str(value))

            # Delegate to provider's outbound call method
            # Each provider implements this differently
            if hasattr(provider, 'initiate_outbound_call'):
                result = provider.initiate_outbound_call(
                    agent_id=self.voice_agent_id.external_agent_id,
                    to_number=number,
                    dynamic_variables=dynamic_variables,
                    first_message=first_message,
                    max_duration=self.agent_max_duration,
                )

                call_sid = result.get('call_sid')
                contact.write({
                    'call_sid': call_sid,
                    'current_attempt': contact.current_attempt + 1,
                })

                self.sudo().create_log_message(
                    f"Agent call initiated to {contact.phone_number} using agent '{self.voice_agent_id.name}'"
                )

                debug(self, f"Voice agent outbound call initiated: {call_sid}")

            else:
                raise UserError(_(
                    'Provider %s does not support outbound calling.'
                ) % provider.name)

        except Exception as e:
            logger.exception('Error initiating voice agent outbound call: %s', e)
            contact.write({
                'status': 'failed',
                'error_message': str(e),
                'current_attempt': contact.current_attempt + 1,
            })
            self.sudo().create_log_message(
                f"Failed to initiate agent call to {contact.phone_number}: {e}"
            )
            # Continue to next contact if available
            if self.status == 'running':
                next_contact = self.get_next_contact()
                if next_contact:
                    self.originate_call(next_contact)

    def _build_agent_dynamic_variables(self, contact):
        """
        Build dynamic variables for agent personalization.

        These variables can be used in the agent's prompt and first message.

        Args:
            contact: callout contact record

        Returns:
            dict: Variable name -> value mapping
        """
        variables = {
            'contact_name': contact.partner.name if contact.partner else '',
            'contact_phone': contact.phone_number,
            'callout_name': self.name,
        }

        # Add partner fields if available
        if contact.partner:
            variables.update({
                'contact_email': contact.partner.email or '',
                'contact_company': (
                    contact.partner.parent_id.name
                    if contact.partner.parent_id
                    else (contact.partner.company_name or '')
                ),
            })

        # Add meeting fields if they exist
        if hasattr(contact, 'meeting_id') and contact.meeting_id:
            variables['meeting_id'] = contact.meeting_id
        if hasattr(contact, 'meeting_password') and contact.meeting_password:
            variables['meeting_password'] = contact.meeting_password
        if hasattr(contact, 'meeting_url') and contact.meeting_url:
            variables['meeting_url'] = contact.meeting_url

        return variables
