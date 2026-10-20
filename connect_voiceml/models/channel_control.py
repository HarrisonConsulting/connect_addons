# -*- coding: utf-8 -*-
"""VoiceML implementations of Connect's provider-dispatched call controls.

Core owns the entry points (connect.channel hold_softphone_call,
resume_softphone_call, attended_transfer_softphone_call,
forward_softphone_call) and dispatches to `_softphone_<action>_voiceml`.

The VoiceML browser phone is a SIP-over-WebRTC client, so media hold is the
phone's own re-INVITE (JsSIP session.hold()/unhold()). These methods are the
server half: they check the caller may touch the call, and the transfer
actions move the *other* leg with a REST call update. Add participant is
deliberately not implemented: it needs Conferences, whose VoiceML semantics
are unverified (LQ11), so core refuses it in words.
"""
import logging
from urllib.parse import urljoin

from odoo import models, api
from odoo.exceptions import UserError

from odoo.addons.connect.models.channel import CALL_END_STATUSES
from .settings import MAX_EXTEN_LEN, strip_number

logger = logging.getLogger(__name__)


class Channel(models.Model):
    _inherit = 'connect.channel'

    @api.model
    def _voiceml_control_channel(self, payload):
        """The live leg the browser phone is on, after core's access checks.

        The phone sends `channel_sid` when the SIP INVITE carried it. When it
        did not, fall back to the user's newest live leg.
        """
        payload = dict(payload or {})
        if not (payload.get('channel_sid') or payload.get('call_sid')):
            pbx_user = self.env.user.connect_user
            leg = self.sudo().search([
                '|', ('caller_pbx_user', '=', pbx_user.id),
                ('called_pbx_user', '=', pbx_user.id),
                ('status', 'not in', list(CALL_END_STATUSES)),
            ], order='id desc', limit=1) if pbx_user else self.browse()
            if not leg:
                raise UserError('Missing active call identifier.')
            payload['channel_sid'] = leg.sid
        channel = self._softphone_recording_channel(payload)
        channel._check_softphone_recording_active()
        return channel

    def _voiceml_other_leg_sid(self):
        """The one live party on the other side of this softphone leg."""
        self.ensure_one()
        siblings = (self.call.channels - self).filtered(
            lambda c: c.status not in CALL_END_STATUSES)
        if len(siblings) != 1:
            raise UserError(
                'This call cannot be transferred: there is no single other '
                'party on the line.')
        return siblings.sid

    def _voiceml_transfer_twiml(self, number):
        self.ensure_one()
        raw = (number or '').strip()
        if not raw:
            raise UserError('No number to transfer the call to.')
        settings = self.env['connect.settings'].sudo()
        digits = strip_number(raw)
        exten = self.env['connect.voiceml.exten'].sudo().search(
            [('number', '=', digits)], limit=1)
        if exten:
            return exten.render()
        pbx_user = (self.caller_pbx_user or self.called_pbx_user
                    or self.env.user.connect_user)
        caller_id = pbx_user.voiceml_pstn_caller_id() if pbx_user else ''
        if not caller_id:
            raise UserError(
                'No caller ID: set an outgoing caller ID on the user or a '
                'default VoiceML outgoing caller ID before transferring.')
        dial = settings._voiceml_dial_number(raw) if (
            len(digits) > MAX_EXTEN_LEN) else digits
        status_url = urljoin(
            settings.get_param('api_url') or '', 'voiceml/webhook/callstatus')
        return settings.get_voiceml_external_call_route(
            dial, caller_id, status_url)

    def _voiceml_redirect_other_leg(self, number):
        self.ensure_one()
        other_sid = self._voiceml_other_leg_sid()
        twiml = self._voiceml_transfer_twiml(number)
        try:
            client = self.env['connect.settings']._voiceml_client()
            client.calls(other_sid).update(twiml=twiml)
        except Exception as e:
            logger.exception('Could not transfer leg %s to %s', other_sid, number)
            raise UserError('Could not transfer the call: {}'.format(e))
        return {'success': True, 'transferred_to': number,
                'channel_sid': other_sid}

    @api.model
    def _softphone_hold_voiceml(self, payload):
        channel = self._voiceml_control_channel(payload)
        return {'success': True, 'is_on_hold': True, 'channel_sid': channel.sid}

    @api.model
    def _softphone_resume_voiceml(self, payload):
        channel = self._voiceml_control_channel(payload)
        return {'success': True, 'is_on_hold': False, 'channel_sid': channel.sid}

    @api.model
    def _softphone_attended_start_voiceml(self, payload):
        return self._softphone_hold_voiceml(payload)

    @api.model
    def _softphone_attended_cancel_voiceml(self, payload):
        return self._softphone_resume_voiceml(payload)

    @api.model
    def _softphone_attended_complete_voiceml(self, payload):
        channel = self._voiceml_control_channel(payload)
        return channel._voiceml_redirect_other_leg(payload.get('number'))

    @api.model
    def _softphone_forward_voiceml(self, payload):
        channel = self._voiceml_control_channel(payload)
        return channel._voiceml_redirect_other_leg(payload.get('number'))
