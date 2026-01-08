# -*- coding: utf-8 -*-
"""
Connect Voice Call extension.

Extends connect.call with voice AI agent integration, tracking which calls
are handled by voice agents and storing conversation metadata.
"""
from odoo import models, fields, api, release
import logging

logger = logging.getLogger(__name__)


class ConnectCallVoice(models.Model):
    """
    Extend connect.call with voice agent integration.

    This provides provider-agnostic voice agent tracking for calls,
    replacing the ElevenLabs-specific fields from connect_elevenlabs.
    """
    _inherit = 'connect.call'

    # === Voice Agent Link ===
    voice_agent_id = fields.Many2one(
        comodel_name='connect.voice.agent',
        string='Voice Agent',
        readonly=True,
        tracking=True,
        help='Voice agent that handled this call'
    )

    # === Conversation Tracking ===
    voice_conversation_id = fields.Char(
        string='Conversation ID',
        readonly=True,
        index=True,
        help='External conversation ID from the voice provider'
    )
    voice_summary = fields.Html(
        string='AI Summary',
        readonly=True,
        help='AI-generated summary of the conversation'
    )
    voice_transcript = fields.Text(
        string='Voice Transcript',
        readonly=True,
        help='Full transcript of the voice AI conversation'
    )

    # === Recording Widget (Odoo 17+ uses Html) ===
    if release.version_info[0] >= 17.0:
        voice_recording_widget = fields.Html(
            compute='_compute_voice_recording_data',
            sanitize=False,
            string='Voice Recording',
            help='Audio player widget for the recording'
        )
    else:
        voice_recording_widget = fields.Char(
            compute='_compute_voice_recording_data',
            string='Voice Recording',
            help='Audio player widget for the recording'
        )

    def _compute_voice_recording_data(self):
        """Compute recording widget for voice calls."""
        for rec in self:
            if rec.voice_agent_id and rec.voice_conversation_id:
                # Try to get recording URL from the conversation
                conversation = self.env['voice.conversation'].search([
                    ('external_conversation_id', '=', rec.voice_conversation_id),
                ], limit=1)
                if conversation and conversation.audio_url:
                    rec.voice_recording_widget = (
                        f'<audio controls="controls" preload="none">'
                        f'<source src="{conversation.audio_url}"/>'
                        f'</audio>'
                    )
                else:
                    rec.voice_recording_widget = ''
            else:
                rec.voice_recording_widget = ''

    def _get_recording_data(self):
        """Override to add voice agent recording icon."""
        super()._get_recording_data()
        for rec in self:
            if rec.voice_agent_id:
                rec.recording_icon = '<span class="fa fa-file-sound-o"/>'

    def get_voice_call_data(self):
        """
        Get call data for voice agent processing.

        Returns call context data that can be used by voice agents
        for personalizing conversations.

        Returns:
            dict: Call context data
        """
        self.ensure_one()
        users = self.env['connect.user'].search([])
        partner = self.partner

        # Fallback for outgoing/internal calls
        if not partner and self.direction in ['outgoing', 'internal']:
            partner = self.caller_user.partner_id if self.caller_user else None

        data = {
            'id': self.id,
            'call_id': self.id,
            'caller_number': self.caller,
            'called_number': self.called,
            'direction': self.direction,
            'partner_id': partner.id if partner else False,
            'partner_name': partner.name if partner else 'Not registered',
            'existing_partner': 'Yes' if partner else 'No',
            'partner_phone': partner.phone if partner else '',
            'greeting': partner.name if partner else 'Dear customer',
            'users_directory': ', '.join([
                f'{u.user.name} <{u.exten.number}>'
                for u in users if u.exten
            ]),
            'previous_conversation_id': '',
            'previous_topics': '',
        }

        # Add language from partner
        if partner and partner.lang:
            # Handle pt_BR special case
            if partner.lang == 'pt_BR':
                data['partner_language'] = 'pt-br'
            else:
                data['partner_language'] = partner.lang.split('_')[0]
        else:
            data['partner_language'] = 'en'

        # Look for previous conversations
        if partner:
            previous_calls = self.sudo().search([
                ('partner', '=', partner.id),
                ('voice_conversation_id', '!=', False),
                ('id', '!=', self.id),
            ], order='create_date desc', limit=1)
            if previous_calls:
                data['previous_conversation_id'] = previous_calls.voice_conversation_id
                data['previous_topics'] = previous_calls.voice_summary or ''

        return data

    @api.model
    def voice_agent_start_call_event(self, call_id, agent_id):
        """
        Handle voice agent call start event.

        Called by the WebSocket service when a voice agent conversation starts.

        Args:
            call_id (int): Connect call ID
            agent_id (int): Voice agent ID

        Returns:
            dict: Call context data for the voice agent
        """
        call = self.sudo().browse(int(call_id))
        if not call.exists():
            logger.error('Call %s not found', call_id)
            return {'error': 'Call not found'}

        agent = self.env['connect.voice.agent'].sudo().browse(int(agent_id))
        if not agent.exists():
            logger.error('Voice agent %s not found', agent_id)
            return {'error': 'Agent not found'}

        # Link call to voice agent
        call.voice_agent_id = agent.id

        logger.info('Voice agent %s started handling call %s', agent.name, call_id)
        return call.get_voice_call_data()

    @api.model
    def voice_agent_end_call_event(self, call_id, conversation_id, summary=None, transcript=None):
        """
        Handle voice agent call end event.

        Called when a voice agent conversation ends to record the summary
        and transcript.

        Args:
            call_id (int): Connect call ID
            conversation_id (str): External conversation ID from provider
            summary (str): AI-generated summary
            transcript (str): Full conversation transcript
        """
        call = self.sudo().browse(int(call_id))
        if not call.exists():
            logger.error('Call %s not found for end event', call_id)
            return False

        call.write({
            'voice_conversation_id': conversation_id,
            'voice_summary': summary,
            'voice_transcript': transcript,
        })

        logger.info('Voice agent ended call %s, conversation_id=%s', call_id, conversation_id)
        return True
