# -*- coding: utf-8 -*-
"""
Voice AI Webhook Controllers.

Handles incoming webhooks from voice providers for:
- Post-call callbacks with transcripts and summaries
- Real-time events during conversations
- Phone number verification
"""
import json
import logging
import hmac
import hashlib
from odoo import http
from odoo.http import request, Response

logger = logging.getLogger(__name__)


def _get_webhook_user():
    """Get the webhook user for authenticated operations."""
    return request.env.ref("connect.user_connect_webhook")


class VoiceWebhookController(http.Controller):
    """
    Controller for voice AI webhooks.

    Provides endpoints for voice providers to send post-call data,
    conversation events, and other notifications.
    """

    @http.route('/connect/voice/webhook/<int:agent_id>/post_call',
                type='json', auth='public', methods=['POST'], csrf=False)
    def post_call_webhook(self, agent_id, **kwargs):
        """
        Handle post-call webhook from voice provider.

        Receives conversation summary, transcript, and metadata after
        a voice call ends.

        Args:
            agent_id (int): Voice agent ID

        Returns:
            dict: Acknowledgment response
        """
        try:
            webhook_user = _get_webhook_user()
            agent = request.env['connect.voice.agent'].with_user(webhook_user).browse(agent_id)
            if not agent.exists():
                logger.error('Voice agent %s not found', agent_id)
                return {'status': 'error', 'message': 'Agent not found'}

            # Validate webhook signature if configured
            if agent.post_call_webhook_secret:
                if not self._validate_webhook_signature(agent.post_call_webhook_secret):
                    logger.warning('Invalid webhook signature for agent %s', agent_id)
                    return {'status': 'error', 'message': 'Invalid signature'}

            # Parse webhook data
            data = request.jsonrequest
            logger.info('Post-call webhook for agent %s: %s',
                       agent_id, json.dumps(data, default=str)[:500])

            # Extract conversation data
            conversation_id = data.get('conversation_id')
            call_id = data.get('call_id') or data.get('metadata', {}).get('call_id')
            summary = data.get('summary') or data.get('analysis', {}).get('summary')
            transcript = data.get('transcript')

            # Handle transcript as structured data
            if isinstance(transcript, list):
                # Convert transcript array to text
                transcript_text = self._format_transcript(transcript)
            else:
                transcript_text = transcript

            # Update the call record if we have a call_id
            if call_id:
                request.env['connect.call'].with_user(webhook_user).voice_agent_end_call_event(
                    call_id=call_id,
                    conversation_id=conversation_id,
                    summary=summary,
                    transcript=transcript_text,
                )

            # Create or update conversation record
            self._save_conversation(agent, data)

            return {'status': 'ok', 'message': 'Webhook processed'}

        except Exception as e:
            logger.exception('Post-call webhook error: %s', e)
            return {'status': 'error', 'message': str(e)}

    @http.route('/connect/voice/webhook/<int:agent_id>/event',
                type='json', auth='public', methods=['POST'], csrf=False)
    def conversation_event_webhook(self, agent_id, **kwargs):
        """
        Handle real-time conversation events.

        Receives events like tool calls, interruptions, and status changes
        during an active conversation.

        Args:
            agent_id (int): Voice agent ID

        Returns:
            dict: Acknowledgment response
        """
        try:
            data = request.jsonrequest
            event_type = data.get('type', 'unknown')

            logger.debug('Conversation event for agent %s: %s', agent_id, event_type)

            # Handle different event types
            if event_type == 'tool_call':
                return self._handle_tool_call_event(agent_id, data)
            elif event_type == 'conversation_started':
                return self._handle_conversation_started(agent_id, data)
            elif event_type == 'conversation_ended':
                return self._handle_conversation_ended(agent_id, data)
            else:
                logger.debug('Unhandled event type: %s', event_type)
                return {'status': 'ok', 'message': 'Event logged'}

        except Exception as e:
            logger.exception('Conversation event webhook error: %s', e)
            return {'status': 'error', 'message': str(e)}

    @http.route('/connect/voice/health', type='http', auth='none', methods=['GET'])
    def health_check(self, **kwargs):
        """
        Health check endpoint for monitoring.

        Returns:
            Response: JSON health status
        """
        return Response(
            json.dumps({'status': 'ok', 'service': 'connect_voice'}),
            content_type='application/json'
        )

    def _validate_webhook_signature(self, secret):
        """
        Validate webhook request signature.

        Checks the X-Signature header against the request body using
        HMAC-SHA256.

        Args:
            secret (str): Webhook secret for validation

        Returns:
            bool: True if signature is valid
        """
        signature = request.httprequest.headers.get('X-Signature', '')
        if not signature:
            # Allow unsigned requests if no signature header present
            # (for backwards compatibility)
            return True

        # Get raw request body
        body = request.httprequest.get_data(as_text=True)

        # Compute expected signature
        expected = hmac.new(
            secret.encode('utf-8'),
            body.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(signature, expected)

    def _format_transcript(self, transcript_data):
        """
        Format structured transcript data as readable text.

        Args:
            transcript_data (list): List of transcript entries

        Returns:
            str: Formatted transcript text
        """
        lines = []
        for entry in transcript_data:
            speaker = entry.get('role', entry.get('speaker', 'Unknown'))
            text = entry.get('text', entry.get('message', ''))
            timestamp = entry.get('timestamp', '')

            if timestamp:
                lines.append(f'[{timestamp}] {speaker}: {text}')
            else:
                lines.append(f'{speaker}: {text}')

        return '\n'.join(lines)

    def _save_conversation(self, agent, data):
        """
        Save or update conversation record and create connect.recording.

        Args:
            agent: connect.voice.agent record
            data (dict): Webhook data
        """
        conversation_id = data.get('conversation_id')
        if not conversation_id:
            return

        webhook_user = _get_webhook_user()
        VoiceConversation = request.env['voice.conversation'].with_user(webhook_user)

        # Check if conversation already exists
        existing = VoiceConversation.search([
            ('external_conversation_id', '=', conversation_id),
        ], limit=1)

        # Extract metadata
        metadata = data.get('metadata', {})
        call_id = data.get('call_id') or metadata.get('call_id')
        duration = data.get('duration') or data.get('call_duration_secs')
        summary = data.get('summary') or data.get('analysis', {}).get('summary')
        transcript = data.get('transcript')

        # Handle transcript as structured data
        if isinstance(transcript, list):
            transcript_text = self._format_transcript(transcript)
        else:
            transcript_text = transcript

        values = {
            'external_conversation_id': conversation_id,
            'res_model': 'connect.voice.agent',
            'res_id': agent.id,
            'voice_provider_id': agent.voice_provider_id.id if agent.voice_provider_id else False,
            'summary': summary,
            'transcript': transcript_text,
            'state': 'completed',
        }

        if duration:
            values['duration_seconds'] = int(duration)

        if existing:
            existing.write(values)
        else:
            VoiceConversation.create(values)

        # Create connect.recording for unified recording experience
        self._create_voice_recording(
            agent, call_id, conversation_id,
            transcript_text, summary, duration
        )

    def _create_voice_recording(self, agent, call_id, conversation_id,
                                 transcript, summary, duration):
        """
        Create a connect.recording from voice conversation audio.

        Fetches audio from the voice provider and creates a recording
        record so it appears in the UI like a regular Twilio recording.

        Args:
            agent: connect.voice.agent record
            call_id: Connect call ID
            conversation_id: External conversation ID from provider
            transcript: Conversation transcript
            summary: Conversation summary
            duration: Duration in seconds
        """
        if not call_id or not conversation_id:
            logger.warning('Cannot create voice recording: missing call_id or conversation_id')
            return

        # Get the call record
        webhook_user = _get_webhook_user()
        call = request.env['connect.call'].with_user(webhook_user).browse(int(call_id))
        if not call.exists():
            logger.warning('Cannot create voice recording: call %s not found', call_id)
            return

        # Check if recording already exists for this conversation
        existing = request.env['connect.recording'].with_user(webhook_user).search([
            ('voice_conversation_id', '=', conversation_id),
        ], limit=1)
        if existing:
            logger.info('Voice recording already exists for conversation %s', conversation_id)
            return

        # Fetch audio from provider
        audio_data = None
        provider = agent.voice_provider_id if agent else None

        if provider and hasattr(provider, 'fetch_conversation_audio'):
            try:
                audio_data = provider.fetch_conversation_audio(conversation_id)
                if audio_data:
                    logger.info('Fetched %d bytes of audio for conversation %s',
                               len(audio_data), conversation_id)
            except Exception as e:
                logger.error('Error fetching voice recording audio: %s', e)

        if not audio_data:
            logger.warning('No audio data available for conversation %s', conversation_id)
            # Still create record without audio so transcript/summary are available
            # The recording icon will still show due to the call having a voice_agent_id

        # Create the recording
        try:
            request.env['connect.recording'].with_user(webhook_user).create_from_voice_conversation(
                call=call,
                conversation_id=conversation_id,
                audio_data=audio_data,
                transcript=transcript,
                summary=summary,
                duration=int(duration) if duration else 0,
            )
            logger.info('Created voice recording for call %s', call_id)
        except Exception as e:
            logger.error('Error creating voice recording: %s', e)

    def _handle_tool_call_event(self, agent_id, data):
        """Handle tool call events during conversation."""
        tool_name = data.get('tool_name', 'unknown')
        logger.info('Tool call event: agent=%s, tool=%s', agent_id, tool_name)

        # Log tool call to conversation if we have a conversation_id
        conversation_id = data.get('conversation_id')
        if conversation_id:
            webhook_user = _get_webhook_user()
            VoiceToolCall = request.env['voice.conversation.tool_call'].with_user(webhook_user)
            VoiceToolCall.create({
                'conversation_id': conversation_id,
                'tool_name': tool_name,
                'parameters': json.dumps(data.get('parameters', {})),
                'result': json.dumps(data.get('result', {})),
            })

        return {'status': 'ok'}

    def _handle_conversation_started(self, agent_id, data):
        """Handle conversation start event."""
        conversation_id = data.get('conversation_id')
        logger.info('Conversation started: agent=%s, conversation=%s',
                   agent_id, conversation_id)
        return {'status': 'ok'}

    def _handle_conversation_ended(self, agent_id, data):
        """Handle conversation end event."""
        conversation_id = data.get('conversation_id')
        logger.info('Conversation ended: agent=%s, conversation=%s',
                   agent_id, conversation_id)
        return {'status': 'ok'}
