# -*- coding: utf-8 -*-
"""
Voice TTS File models for provider-agnostic text-to-speech file management.

These models provide the base infrastructure for TTS file generation,
storage, and playback. Provider-specific implementations extend these
to use their respective TTS APIs.
"""

import base64
import logging
import uuid
from odoo import fields, models, api, release, _
from odoo.exceptions import ValidationError


logger = logging.getLogger(__name__)


class VoiceTTSFile(models.Model):
    """
    Base TTS file storage model.

    Stores generated audio files with their source text. Provider implementations
    should override the generate_audio() method to use their specific TTS API.

    Migration from connect.elevenlabs_file:
    - text -> text
    - file -> audio_file
    - filename -> audio_filename
    - preview_audio -> preview_audio (computed)
    """
    _name = 'voice.tts.file'
    _description = 'Voice TTS Audio File'
    _order = 'create_date desc'

    # === Source Text ===
    text = fields.Char(
        string='Text',
        required=True,
        help='Text that was converted to speech'
    )

    # === Audio File ===
    audio_file = fields.Binary(
        string='Audio File',
        attachment=True,
        help='Generated audio file'
    )
    audio_filename = fields.Char(
        string='Filename',
        help='Audio file name'
    )

    # === Provider Link ===
    voice_provider_id = fields.Many2one(
        comodel_name='voice.provider',
        string='Provider',
        ondelete='cascade',
        help='Provider that generated this audio'
    )
    voice_id = fields.Many2one(
        comodel_name='voice.voice',
        string='Voice',
        ondelete='set null',
        help='Voice used for generation'
    )

    # === Audio Preview (Odoo 17+ uses Html field) ===
    if release.version_info[0] >= 17.0:
        preview_audio = fields.Html(
            compute='_compute_preview_audio',
            string='Preview Audio',
            sanitize=False,
            help='Audio player widget for preview'
        )
    else:
        preview_audio = fields.Char(
            compute='_compute_preview_audio',
            string='Preview Audio',
            help='Audio player widget for preview'
        )

    # === Metadata ===
    model_id = fields.Char(
        string='TTS Model',
        help='TTS model used for generation'
    )
    audio_format = fields.Selection(
        selection=[
            ('mp3', 'MP3'),
            ('wav', 'WAV'),
            ('pcm_16000', 'PCM 16kHz'),
            ('pcm_22050', 'PCM 22.05kHz'),
            ('pcm_24000', 'PCM 24kHz'),
            ('pcm_44100', 'PCM 44.1kHz'),
            ('ulaw_8000', 'uLaw 8kHz'),
        ],
        string='Audio Format',
        default='mp3',
        help='Format of the audio file'
    )

    # === Migration Support ===
    legacy_model = fields.Char(
        string='Legacy Model',
        readonly=True,
        help='Original model name if migrated from legacy system'
    )
    legacy_id = fields.Integer(
        string='Legacy ID',
        readonly=True,
        index=True,
        help='Original record ID if migrated from legacy system'
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to ensure proper MIME type for attachments."""
        res = super().create(vals_list)
        for rec in res:
            if rec.audio_file:
                rec._fix_attachment_mimetype()
        return res

    def _fix_attachment_mimetype(self):
        """Fix MIME type for audio attachments."""
        self.ensure_one()
        attachment = self.env['ir.attachment'].search([
            ('res_model', '=', self._name),
            ('res_field', '=', 'audio_file'),
            ('res_id', '=', self.id),
        ], limit=1)
        if attachment:
            mime_types = {
                'mp3': 'audio/mpeg',
                'wav': 'audio/wav',
                'pcm_16000': 'audio/wav',
                'pcm_22050': 'audio/wav',
                'pcm_24000': 'audio/wav',
                'pcm_44100': 'audio/wav',
                'ulaw_8000': 'audio/basic',
            }
            expected_mime = mime_types.get(self.audio_format, 'audio/mpeg')
            if attachment.mimetype != expected_mime:
                attachment.mimetype = expected_mime

    @api.constrains('text')
    def _onchange_text_regenerate(self):
        """Regenerate audio when text changes."""
        for rec in self:
            if rec.text and rec.voice_provider_id:
                try:
                    rec._generate_audio()
                except Exception as e:
                    logger.warning('TTS generation failed: %s', e)

    def _generate_audio(self):
        """
        Generate audio from text using the provider.

        This method should be overridden by provider-specific implementations
        or call the provider's TTS method.
        """
        self.ensure_one()
        if not self.voice_provider_id:
            return

        try:
            audio_data = self.voice_provider_id.generate_tts(
                text=self.text,
                voice_id=self.voice_id.external_voice_id if self.voice_id else None,
                model_id=self.model_id,
            )
            if audio_data:
                self.write({
                    'audio_file': audio_data,
                    'audio_filename': f'{uuid.uuid4().hex}.mp3',
                })
                self._fix_attachment_mimetype()
        except NotImplementedError:
            logger.debug('TTS generation not implemented for provider %s',
                        self.voice_provider_id.name)
        except Exception as e:
            logger.error('TTS generation error: %s', e)
            raise ValidationError(_('TTS generation failed: %s') % e)

    def get_file_path(self):
        """Get relative URL path for the audio file."""
        self.ensure_one()
        return f'/web/content/{self._name}/{self.id}/audio_file/{self.audio_filename}'

    def get_file_url(self, base_url=None):
        """
        Get full URL for the audio file.

        Args:
            base_url: Optional base URL. If not provided, uses system base URL.

        Returns:
            str: Full URL to the audio file
        """
        self.ensure_one()
        if not base_url:
            base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        file_path = self.get_file_path()
        return f'{base_url.rstrip("/")}{file_path}'

    def _compute_preview_audio(self):
        """Compute HTML audio player for preview."""
        for rec in self:
            if rec.audio_file:
                rec.preview_audio = (
                    f'<audio id="sound_file" preload="auto" controls="controls">'
                    f'<source src="{rec.get_file_path()}"/>'
                    f'</audio>'
                )
            else:
                rec.preview_audio = ''

    def action_regenerate(self):
        """Action button to regenerate audio."""
        for rec in self:
            rec._generate_audio()
        return True

    def action_play_audio(self):
        """Action to play audio in browser."""
        self.ensure_one()
        if self.audio_file:
            return {
                'type': 'ir.actions.act_url',
                'url': self.get_file_path(),
                'target': 'new',
            }
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('No Audio'),
                'message': _('No audio file available'),
                'type': 'warning',
                'sticky': False,
            }
        }


class VoiceSystemMessage(models.Model):
    """
    Pre-generated system message audio files.

    Extends VoiceTTSFile to add message_key for looking up common system
    messages that should be pre-generated for faster playback.

    Migration from connect.elevenlabs_system_message:
    - message_key -> message_key
    - Inherits file fields from VoiceTTSFile (was connect.elevenlabs_file)
    """
    _name = 'voice.system.message'
    _inherit = 'voice.tts.file'
    _description = 'Pre-generated System Message Audio'

    # === Message Key ===
    message_key = fields.Char(
        string='Message Key',
        required=True,
        index=True,
        help='Unique key for looking up this system message'
    )

    _sql_constraints = [
        ('unique_message_key_provider',
         'UNIQUE(message_key, voice_provider_id)',
         'Message key must be unique per provider')
    ]

    @api.model
    def get_or_create_message(self, message_key, text, provider_id=None, voice_id=None):
        """
        Get existing system message or create a new one.

        Args:
            message_key: Unique message identifier
            text: Message text for TTS generation
            provider_id: Optional provider ID
            voice_id: Optional voice ID

        Returns:
            voice.system.message record
        """
        domain = [('message_key', '=', message_key)]
        if provider_id:
            domain.append(('voice_provider_id', '=', provider_id))

        existing = self.search(domain, limit=1)
        if existing:
            # Update text if different (will trigger regeneration)
            if existing.text != text:
                existing.text = text
            return existing

        # Create new message
        vals = {
            'message_key': message_key,
            'text': text,
        }
        if provider_id:
            vals['voice_provider_id'] = provider_id
        if voice_id:
            vals['voice_id'] = voice_id
        return self.create(vals)

    @api.model
    def get_message_url(self, message_key, provider_id=None):
        """
        Get URL for a system message audio file.

        Args:
            message_key: Message identifier
            provider_id: Optional provider filter

        Returns:
            str: Audio URL or None if not found
        """
        domain = [('message_key', '=', message_key)]
        if provider_id:
            domain.append(('voice_provider_id', '=', provider_id))

        message = self.search(domain, limit=1)
        if message and message.audio_file:
            return message.get_file_url()
        return None


# Default system messages - these are common across providers
DEFAULT_SYSTEM_MESSAGES = {
    'system.transfer': 'Transfer',
    'system.connecting': 'Connecting...',
    'error.no_callerid': 'You must configure a default number for caller ID!',
    'error.callflow_empty': 'This callflow has no actions! Goodbye!',
    'error.call_failed': 'Sorry, I could not connect your call. Goodbye!',
    'error.no_extension': 'Extension not configured!',
    'error.choice_error': 'Choice application error, please contact technical support!',
}
