# -*- coding: utf-8 -*-
"""
Connect Voice Recording extension.

Extends connect.recording with voice agent recording support,
allowing AI agent conversations to be stored and played back
the same way as regular Twilio recordings.
"""
import logging

from odoo import models, fields, api, release

logger = logging.getLogger(__name__)


class ConnectRecordingVoice(models.Model):
    """
    Extend connect.recording with voice agent recording support.

    This adds a binary field to store audio from voice AI providers
    (like ElevenLabs) and a widget to play them back in the UI.
    """
    _inherit = 'connect.recording'

    # === Voice Agent Recording Fields ===
    voice_media_file = fields.Binary(
        string='Voice Agent Recording',
        attachment=True,
        help='Audio recording from voice AI provider'
    )
    voice_transcript = fields.Text(
        string='Voice Transcript',
        readonly=True,
        help='Transcript from voice AI provider'
    )
    voice_summary = fields.Text(
        string='Voice Summary',
        readonly=True,
        help='Summary from voice AI provider'
    )
    voice_conversation_id = fields.Char(
        string='Voice Conversation ID',
        readonly=True,
        index=True,
        help='External conversation ID from voice provider'
    )

    # === Voice Recording Widget ===
    if release.version_info[0] >= 17.0:
        voice_recording_widget = fields.Html(
            compute='_compute_voice_recording_widget',
            sanitize=False,
            string='Voice Recording Player',
            help='Audio player widget for voice agent recording'
        )
    else:
        voice_recording_widget = fields.Char(
            compute='_compute_voice_recording_widget',
            string='Voice Recording Player',
            help='Audio player widget for voice agent recording'
        )

    def _compute_voice_recording_widget(self):
        """Compute HTML audio widget for voice agent recordings."""
        for rec in self:
            if rec.voice_media_file:
                rec.voice_recording_widget = (
                    '<audio id="sound_file" preload="auto" controls="controls">'
                    '<source src="/web/content?model=connect.recording'
                    f'&id={rec.id}&filename=voice_recording.mp3'
                    '&field=voice_media_file&download=True" />'
                    '</audio>'
                )
            else:
                rec.voice_recording_widget = ''

    def _get_recording_widget(self):
        """Override to support voice agent recordings without media_url."""
        super()._get_recording_widget()
        for rec in self:
            # If no media_url but has voice_media_file, use the voice widget
            if not rec.media_url and rec.voice_media_file:
                rec.recording_widget = rec.voice_recording_widget

    @api.depends('summary', 'voice_summary')
    def _get_list_view_summary(self):
        """Override to prioritize voice summary over regular summary."""
        for rec in self:
            if rec.voice_summary:
                rec.list_view_summary = rec.voice_summary
            else:
                rec.list_view_summary = rec.summary or ''

    @api.model
    def create_from_voice_conversation(self, call, conversation_id, audio_data,
                                        transcript=None, summary=None, duration=None):
        """
        Create a recording from a voice AI conversation.

        This method is called by the voice webhook after a conversation ends
        to create a recording that will appear in the UI like a regular
        Twilio recording.

        Args:
            call: connect.call record
            conversation_id (str): External conversation ID from provider
            audio_data (bytes): Audio file data (MP3)
            transcript (str): Conversation transcript
            summary (str): Conversation summary
            duration (int): Duration in seconds

        Returns:
            connect.recording: Created recording record
        """
        import base64

        if not call:
            logger.error('Cannot create voice recording: no call provided')
            return self.browse()

        # Get channel from call
        channel = call.channels[0] if call.channels else None
        channel_sid = channel.sid if channel else conversation_id

        vals = {
            'call': call.id,
            'channel': channel.id if channel else False,
            'partner': call.partner.id if call.partner else False,
            'sid': conversation_id,
            'call_sid': channel_sid,
            'caller_number': call.caller,
            'called_number': call.called,
            'caller_user': call.caller_user.id if call.caller_user else False,
            'status': 'completed',
            'start_time': call.create_date,
            'duration': duration or 0,
            'voice_media_file': base64.b64encode(audio_data) if audio_data else False,
            'voice_transcript': transcript,
            'voice_summary': summary,
            'voice_conversation_id': conversation_id,
        }

        logger.info('Creating voice recording for call %s, conversation %s',
                   call.id, conversation_id)

        return self.with_context(skip_transcription=True).create(vals)
