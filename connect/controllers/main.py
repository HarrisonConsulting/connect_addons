# -*- coding: utf-8 -*

import json
import logging
from datetime import timedelta

import requests
from werkzeug.exceptions import NotFound

from odoo import fields, http, release
from odoo.api import SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.addons.connect.models.settings import HTTP_API_TIMEOUT
from .twilio_webhooks import ConnectController as TwilioWebhooksController

logger = logging.getLogger(__name__)

route_type = "json" if release.version_info[0] < 19.0 else 'jsonrpc'

class ConnectController(http.Controller):

    # preflight-ignore-next-line: idor-sudo-write -- capability token (uuid4), not the URL id; generator removed in refactor so the field is now always False
    @http.route('/connect/transcript/<int:rec_id>', methods=['POST'], type=route_type,
                auth='public', csrf=False)
    def upload_transcript(self, rec_id):
        # Public method protected by the one-time transcription token.
        data = json.loads(http.request.httprequest.get_data(as_text=True))
        rec = http.request.env['connect.recording'].sudo().search([
            ('id', '=', rec_id), ('transcription_token', '!=', False),
            ('transcription_token', '=', data['transcription_token'])
        ])
        if not rec:
            logger.warning('Transcription token %s not found for recording %s',
                data['transcription_token'], rec_id)
            raise NotFound()
        rec.with_user(SUPERUSER_ID).update_transcript(data)
        logger.info('Transcript for recording %s saved.', rec_id)
        return True

    @http.route('/connect/recording/<int:record_id>', type='http', auth='user')
    def serve_recording(self, record_id):
        recording = http.request.env['connect.recording'].browse(record_id)
        if not recording.exists() or not recording.media_url:
            return http.Response(status=404)
        # ACL: user must be a connect user or admin
        if not http.request.env.user.has_group('connect.group_connect_user') and \
                not http.request.env.user.has_group('connect.group_connect_admin'):
            return http.Response(status=403)
        return self._serve_media(recording.media_url)

    @http.route('/connect/voicemail/<int:record_id>', type='http', auth='user')
    def serve_voicemail(self, record_id):
        call = http.request.env['connect.call'].browse(record_id)
        if not call.exists() or not call.voicemail_url:
            return http.Response(status=404)
        # ACL: user must be a connect user or admin
        if not http.request.env.user.has_group('connect.group_connect_user') and \
                not http.request.env.user.has_group('connect.group_connect_admin'):
            return http.Response(status=403)
        return self._serve_media(call.voicemail_url)

    def _serve_media(self, media_url):
        media_name = '{}.wav'.format(media_url.split('/')[-1])
        account_sid, auth_token = http.request.env['connect.settings'].sudo()._get_client_credentials()
        response = requests.get(media_url, auth=(account_sid, auth_token), timeout=HTTP_API_TIMEOUT)
        if response.status_code == 200:
            # Create the response
            res = http.Response(response.content, content_type='audio/wav')
            res.headers['Content-Disposition'] = http.content_disposition(media_name)
            return res
        else:
            raise UserError("Failed to download the media. Status code: %s" % response.status_code)

    @http.route('/connect/<string:extension_number>', methods=['GET', 'POST'], type='http', auth='public', csrf=False)
    def extension_handler(self, extension_number, **kw):
        """Handle extension calls via direct URL"""
        if not TwilioWebhooksController.check_signature(kw):
            return TwilioWebhooksController._reject_invalid_request()
        try:
            exten = http.request.env['connect.exten'].sudo().search([('number', '=', extension_number)])
            if not exten:
                return http.Response(
                    '<Response><Say>Extension not found. Goodbye!</Say><Hangup/></Response>',
                    content_type='text/xml',
                )
            result = exten.render(request=kw, params=kw)
            return http.Response(f'{result}', content_type='text/xml')
        except Exception:
            logger.exception('extension_handler failed for extension=%s', extension_number)
            return http.Response(
                '<Response><Say>A system error occurred. Please try again later.</Say><Hangup/></Response>',
                content_type='text/xml',
            )

    @http.route('/connect/dial_complete', methods=['GET', 'POST'], type='http', auth='public', csrf=False)
    def dial_complete_handler(self, **kw):
        """Handle Dial action completion for transfer redirects and update call completion fields"""
        if not TwilioWebhooksController.check_signature(kw):
            return TwilioWebhooksController._reject_invalid_request()
        try:
            from twilio.twiml.voice_response import VoiceResponse

            dial_status = kw.get('DialCallStatus')
            dial_call_sid = kw.get('DialCallSid')
            original_call_sid = kw.get('CallSid')

            try:
                self._process_extension_redirect_completion(kw)
            except Exception as e:
                logger.error(f'Failed to process transfer completion: {e}', exc_info=True)

            response = VoiceResponse()

            if dial_status == 'completed':
                response.hangup()
            else:
                try:
                    original_call = self._find_original_call_for_redirect_completion(original_call_sid, dial_call_sid)
                    if original_call:
                        transfer_recipient = None

                        if original_call_sid:
                            transfer_recipient = original_call.get_transfer_target(original_call_sid)

                        if not transfer_recipient and dial_call_sid:
                            transfer_recipient = original_call.get_transfer_target(dial_call_sid)

                        if not transfer_recipient:
                            parent_call_sid = kw.get('ParentCallSid')
                            if parent_call_sid:
                                transfer_recipient = original_call.get_transfer_target(parent_call_sid)

                        if not transfer_recipient and original_call.transferred_users:
                            transfer_recipient = original_call.transferred_users[-1]
                            logger.warning(
                                'Using last transferred_users entry as fallback for voicemail lookup on call %s',
                                original_call.id,
                            )

                        if transfer_recipient:
                            pbx_user = http.request.env['connect.user'].sudo().search([
                                ('user', '=', transfer_recipient.id)
                            ], limit=1)

                            if pbx_user and pbx_user.voicemail_enabled:
                                # get_voicemail_prompt handles the audio-missing
                                # case with a pronunciation-processed <Say>.
                                pbx_user.sudo().get_voicemail_prompt(response)
                            else:
                                system_voice = http.request.env['connect.settings'].get_system_voice()
                                processed_text = http.request.env['connect.settings'].process_pronunciation('Please leave a message after the tone.')
                                response.say(processed_text, voice=system_voice)
                        else:
                            logger.warning(f'Could not find transfer recipient for personalized voicemail')
                            system_voice = http.request.env['connect.settings'].get_system_voice()
                            processed_text = http.request.env['connect.settings'].process_pronunciation('Please leave a message after the tone.')
                            response.say(processed_text, voice=system_voice)
                    else:
                        logger.warning(f'Could not find original call for personalized voicemail')
                        system_voice = http.request.env['connect.settings'].get_system_voice()
                        processed_text = http.request.env['connect.settings'].process_pronunciation('Please leave a message after the tone.')
                        response.say(processed_text, voice=system_voice)
                except Exception as e:
                    logger.error(f'Error setting up personalized voicemail: {e}')
                    system_voice = http.request.env['connect.settings'].get_system_voice()
                    processed_text = http.request.env['connect.settings'].process_pronunciation('Please leave a message after the tone.')
                    response.say(processed_text, voice=system_voice)

                vm_max_length = http.request.env['connect.settings'].sudo().get_param('voicemail_max_length') or 120
                vm_finish_key = http.request.env['connect.settings'].sudo().get_param('voicemail_finish_key') or '#'
                response.record(maxLength=vm_max_length, finishOnKey=vm_finish_key, playBeep=True)

            return http.Response(response.to_xml(), content_type='text/xml')
        except Exception:
            logger.exception('dial_complete_handler failed for CallSid=%s', kw.get('CallSid'))
            return http.Response(
                '<Response><Say>A system error occurred. Please try again later.</Say><Hangup/></Response>',
                content_type='text/xml',
            )

    def _process_extension_redirect_completion(self, webhook_params):
        """
        Process completion of extension redirect transfers.
        Updates the original call's completion fields based on transfer outcome.
        """
        dial_call_status = webhook_params.get('DialCallStatus')
        dial_call_sid = webhook_params.get('DialCallSid')
        original_call_sid = webhook_params.get('CallSid')

        original_call = self._find_original_call_for_redirect_completion(original_call_sid, dial_call_sid)
        if not original_call:
            logger.warning(f'Could not find original call for redirect completion')
            return

        transfer_recipient = None

        if original_call_sid:
            transfer_recipient = original_call.get_transfer_target(original_call_sid)

        if not transfer_recipient and dial_call_sid:
            transfer_recipient = original_call.get_transfer_target(dial_call_sid)

        if not transfer_recipient:
            parent_call_sid = webhook_params.get('ParentCallSid')
            if parent_call_sid:
                transfer_recipient = original_call.get_transfer_target(parent_call_sid)

        if not transfer_recipient and original_call.transferred_users:
            transfer_recipient = original_call.transferred_users[-1]
            logger.warning(
                'Using last transferred_users entry as fallback for call %s - this is a heuristic and may be wrong',
                original_call.id,
            )

        if not transfer_recipient:
            logger.warning('Could not find transfer recipient for completion processing - no transferred_users found')
            return

        if dial_call_status == 'completed':
            original_call.completed_by_user = transfer_recipient
            self._create_or_update_transfer_channel(original_call, dial_call_sid, transfer_recipient, 'completed', webhook_params)
            self._terminate_external_call_after_transfer_completion(original_call, dial_call_sid, transfer_recipient)
        else:
            self._create_or_update_transfer_channel(original_call, dial_call_sid, transfer_recipient, dial_call_status, webhook_params)

    def _find_original_call_for_redirect_completion(self, original_call_sid, dial_call_sid):
        """Find the original call that initiated this transfer redirect"""
        Call = http.request.env['connect.call'].sudo()
        cutoff = fields.Datetime.now() - timedelta(minutes=5)

        logger.debug(
            'Looking up original call for redirect completion: original_call_sid=%s dial_call_sid=%s',
            original_call_sid, dial_call_sid,
        )

        recent_calls = Call.search([
            ('transfer_context', '!=', False),
            ('create_date', '>=', cutoff),
        ])

        for call in recent_calls:
            if call.transfer_context:
                context_str = str(call.transfer_context)
                if ((original_call_sid and original_call_sid in context_str) or
                    (dial_call_sid and dial_call_sid in context_str)):
                    logger.info(
                        'Found original call %s via transfer_context match (original_sid=%s, dial_sid=%s)',
                        call.id, original_call_sid, dial_call_sid,
                    )
                    return call

        logger.info(
            'No transfer_context match found among %d recent calls, falling back to active transfer search',
            len(recent_calls),
        )

        # Fallback: find recent calls with transfers still in progress
        recent_transfers = Call.search([
            ('transferred_users', '!=', False),
            ('create_date', '>=', cutoff),
            ('status', 'not in', ['completed', 'failed', 'busy', 'no-answer']),
        ], limit=5)

        if recent_transfers:
            logger.warning(
                'Using heuristic fallback: returning call %s (first of %d active transfers). '
                'This may be incorrect if multiple transfers are in progress.',
                recent_transfers[0].id, len(recent_transfers),
            )
            return recent_transfers[0]

        logger.warning(
            'No original call found for redirect completion (original_sid=%s, dial_sid=%s)',
            original_call_sid, dial_call_sid,
        )
        return None

    def _create_or_update_transfer_channel(self, call, dial_call_sid, transfer_recipient, status, webhook_params):
        """Create or update a channel record for the transfer recipient to ensure proper field population"""
        try:
            existing_channel = http.request.env['connect.channel'].sudo().search([
                ('sid', '=', dial_call_sid),
                ('call', '=', call.id)
            ], limit=1)

            if existing_channel:
                logger.info(f'Updating existing transfer channel {existing_channel.id}')
                existing_channel.write({
                    'status': status,
                    'duration': int(webhook_params.get('DialCallDuration', 0))
                })
                return existing_channel
            else:
                logger.info(f'Creating new transfer channel for {transfer_recipient.login}')

                parent_channel = call.channels.filtered(lambda c: not c.parent_channel)
                if not parent_channel:
                    logger.warning(f'No parent channel found for call {call.id}')
                    return None
                parent_channel = parent_channel[0]

                pbx_user = http.request.env['connect.user'].sudo().search([
                    ('user', '=', transfer_recipient.id)
                ], limit=1)

                if not pbx_user:
                    logger.warning(f'No PBX user found for {transfer_recipient.login}')
                    return None

                channel_data = {
                    'sid': dial_call_sid,
                    'call': call.id,
                    'parent_channel': parent_channel.id,
                    'technical_direction': 'outbound-dial',
                    'status': status,
                    'duration': int(webhook_params.get('DialCallDuration', 0)),
                    'called_pbx_user': pbx_user.id,
                    'called_user': transfer_recipient.id,
                    'call_source': 'transfer',
                    'caller': parent_channel.caller,
                    'called': pbx_user.uri
                }

                new_channel = http.request.env['connect.channel'].sudo().create(channel_data)
                logger.info(f'Created transfer channel {new_channel.id} for {transfer_recipient.login}')
                return new_channel

        except Exception as e:
            logger.error(f'Failed to create/update transfer channel: {e}', exc_info=True)
            return None

    def _terminate_external_call_after_transfer_completion(self, call, transfer_recipient_sid, transfer_recipient):
        """
        Terminate external call legs after successful transfer completion to prevent voicemail fall-through.
        This addresses the issue where external callers go to voicemail when internal users hang up completed calls.
        """
        try:
            if call.direction == 'outgoing':
                external_call_sid = call.get_external_call_leg()
                if external_call_sid:
                    client = http.request.env['connect.settings'].sudo().get_client()
                    try:
                        external_call = client.calls(external_call_sid).fetch()
                        if external_call.status in ['in-progress', 'ringing']:
                            self._store_external_call_termination_context(call, external_call_sid, transfer_recipient_sid)
                        else:
                            logger.info(f'External call {external_call_sid} already ended ({external_call.status})')
                    except Exception as e:
                        logger.warning(f'Could not check external call status: {e}')
                else:
                    logger.warning(f'No external call leg found for outgoing call {call.id}')
            else:
                external_channels = call.channels.filtered(lambda c: not c.parent_channel and not c.caller_pbx_user)
                if external_channels:
                    external_channel = external_channels[0]
                    self._store_external_call_termination_context(call, external_channel.sid, transfer_recipient_sid)
                else:
                    logger.info(f'No external caller channel found for incoming call {call.id}')

        except Exception as e:
            logger.error(f'Failed to set up external call termination: {e}', exc_info=True)

    def _store_external_call_termination_context(self, call, external_call_sid, transfer_recipient_sid):
        """Store context for terminating external calls when transfer recipients hang up"""
        try:
            current_context = call.transfer_context or {}
            current_context['_external_termination'] = {
                'external_call_sid': external_call_sid,
                'transfer_recipient_sid': transfer_recipient_sid,
                'setup_time': http.request.env.cr.now()
            }
            call.transfer_context = current_context
        except Exception as e:
            logger.error(f'Failed to store external termination context: {e}')

    @http.route('/connect/health/status', methods=['GET'], type='http', auth='user')
    def health_status(self, **kw):
        """Health check endpoint for monitoring Twilio connectivity."""
        settings = http.request.env['connect.settings'].sudo()
        checks = {}

        # Check 1: Credentials configured
        account_sid, auth_token = settings._get_client_credentials()
        checks['credentials_configured'] = bool(account_sid and auth_token)

        # Check 2: Twilio API reachable
        checks['twilio_api_reachable'] = False
        if checks['credentials_configured']:
            try:
                client = settings.get_client(region=False)
                account = client.api.accounts(account_sid).fetch()
                checks['twilio_api_reachable'] = account.status == 'active'
                checks['twilio_account_status'] = account.status
            except Exception as e:
                checks['twilio_api_error'] = str(e)

        # Check 3: Phone numbers configured
        number_count = http.request.env['connect.number'].sudo().search_count([])
        checks['phone_numbers_configured'] = number_count > 0
        checks['phone_number_count'] = number_count

        # Check 4: Webhook base URL
        api_url = settings.get_param('api_url')
        checks['webhook_url_configured'] = bool(api_url)
        if api_url:
            checks['webhook_base_url'] = api_url

        # Check 5: Active PBX users
        user_count = http.request.env['connect.user'].sudo().search_count([])
        checks['pbx_users_configured'] = user_count > 0
        checks['pbx_user_count'] = user_count

        # Overall status
        critical_checks = [
            checks['credentials_configured'],
            checks['twilio_api_reachable'],
            checks['phone_numbers_configured'],
            checks['webhook_url_configured'],
        ]
        checks['status'] = 'healthy' if all(critical_checks) else 'unhealthy'

        status_code = 200 if checks['status'] == 'healthy' else 503
        return http.Response(
            json.dumps(checks, indent=2),
            status=status_code,
            content_type='application/json',
        )

    # preflight-ignore-next-line: idor-sudo-write -- health check compares a config UID and returns text only; handler contains no write despite POST+sudo
    @http.route('/connect/health/<string:uid>/', methods=['GET', 'POST'], type='http', auth='public', csrf=False)
    def health_check(self, uid):
        instance_uid = http.request.env['connect.settings'].sudo().get_param('instance_uid')
        if uid == instance_uid:
            return "True"
        else:
            return "False"
