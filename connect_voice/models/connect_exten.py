# -*- coding: utf-8 -*-
"""
Connect Extension integration for Voice Agents.

Extends connect.exten to allow routing calls to voice agents,
enabling inbound call handling by AI agents.
"""
from odoo import models, fields, api
import logging

logger = logging.getLogger(__name__)


class ConnectExtenVoice(models.Model):
    """
    Extend connect.exten to support voice agent destinations.

    Adds 'connect.voice.agent' as a valid destination type for extensions,
    allowing inbound calls to be routed to AI voice agents.
    """
    _inherit = 'connect.exten'

    # Extend the destination selection to include voice agents
    dst = fields.Reference(
        selection_add=[('connect.voice.agent', 'Voice Agent')],
        ondelete={'connect.voice.agent': 'set null'},
    )

    def render(self, request, call):
        """
        Override render to handle voice agent destinations.

        When the destination is a voice agent, delegates to the provider's
        render method to generate appropriate TwiML for WebSocket streaming.

        Args:
            request: HTTP request object
            call: connect.call record

        Returns:
            TwiML response string
        """
        self.ensure_one()

        # Check if destination is a voice agent
        if self.dst and self.dst._name == 'connect.voice.agent':
            return self._render_voice_agent(request, call, self.dst)

        # Fall back to default rendering
        return super().render(request, call)

    def _render_voice_agent(self, request, call, voice_agent):
        """
        Render TwiML for routing call to voice agent.

        Args:
            request: HTTP request object
            call: connect.call record
            voice_agent: connect.voice.agent record

        Returns:
            str: TwiML response
        """
        # Link call to voice agent
        call.voice_agent_id = voice_agent.id

        # Get the provider
        provider = voice_agent.voice_provider_id
        if not provider:
            logger.error('Voice agent %s has no provider', voice_agent.id)
            return self._render_error_response('Voice agent not configured')

        # Get the external agent ID
        agent_id = voice_agent.external_agent_id
        if not agent_id:
            logger.error('Voice agent %s not synced to provider', voice_agent.id)
            return self._render_error_response('Voice agent not synced')

        # Get the agent stream URL from settings or provider
        stream_url = self._get_agent_stream_url()
        if not stream_url:
            logger.error('Agent stream URL not configured')
            return self._render_error_response('Agent service not configured')

        # Render the agent response
        if hasattr(provider, 'render_agent_response'):
            return provider.render_agent_response(
                agent_id=agent_id,
                call_id=call.id,
                stream_url=stream_url,
            )

        # Fallback: generate basic TwiML with WebSocket stream
        return self._render_basic_stream_response(agent_id, call.id, stream_url)

    def _get_agent_stream_url(self):
        """
        Get the WebSocket URL for agent streaming.

        Returns:
            str: WebSocket URL or None
        """
        try:
            Settings = self.env['connect.settings'].sudo()
            # Try provider-agnostic setting first
            stream_url = Settings.get_param('voice_agent_stream_url')
            if stream_url:
                return stream_url

            # Fall back to ElevenLabs-specific setting
            stream_url = Settings.get_param('elevenlabs_agent_url')
            if stream_url:
                return stream_url.replace('https://', 'wss://').replace('http://', 'ws://')

            # Use default based on API URL
            api_url = Settings.get_param('api_url')
            if api_url:
                base = api_url.replace('https://', 'wss://').replace('http://', 'ws://')
                return f"{base}/twilio/stream"

        except Exception as e:
            logger.error('Error getting agent stream URL: %s', e)

        return None

    def _render_error_response(self, message):
        """
        Render TwiML error response.

        Args:
            message (str): Error message to speak

        Returns:
            str: TwiML response
        """
        try:
            from twilio.twiml.voice_response import VoiceResponse
            response = VoiceResponse()
            response.say(f'Sorry, an error occurred: {message}')
            response.hangup()
            return str(response)
        except ImportError:
            return f'<?xml version="1.0" encoding="UTF-8"?><Response><Say>{message}</Say><Hangup/></Response>'

    def _render_basic_stream_response(self, agent_id, call_id, stream_url):
        """
        Render basic TwiML with WebSocket stream.

        Fallback when provider doesn't have custom render method.

        Args:
            agent_id (str): External agent ID
            call_id (int): Call ID
            stream_url (str): WebSocket base URL

        Returns:
            str: TwiML response
        """
        try:
            from twilio.twiml.voice_response import VoiceResponse, Connect

            response = VoiceResponse()
            connect = Connect()
            full_url = f"{stream_url}/{agent_id}/{call_id}"
            connect.stream(url=full_url)
            response.append(connect)
            return str(response)

        except ImportError:
            full_url = f"{stream_url}/{agent_id}/{call_id}"
            return f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{full_url}"/>
    </Connect>
</Response>'''
