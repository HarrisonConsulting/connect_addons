# -*- coding: utf-8 -*-
import json
import logging
import time
import uuid
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from markupsafe import escape

from odoo import models, api, release
from odoo.exceptions import UserError

from twilio.base.exceptions import TwilioRestException
from twilio.twiml.voice_response import VoiceResponse

from odoo.addons.connect.models.channel import CALL_END_STATUSES
from odoo.addons.connect.models.settings import debug
from .settings import MAX_EXTEN_LEN

logger = logging.getLogger(__name__)

# Recording statuses that mean "audio is still being captured". Twilio's
# terminal states are 'completed', 'absent', 'deleted' and 'stopped'; a
# running recording shows up as 'processing' (or 'in-progress'/'paused'
# depending on how it was started), so all three count as live.
TWILIO_LIVE_RECORDING_STATUSES = ('in-progress', 'processing', 'paused')

HOLD_MUSIC_URL = 'http://twimlets.com/holdmusic?Bucket=com.twilio.music.classical'

# A promoted call's room exists only once the moved leg has joined it, and
# the parent leg joins a beat later through its <Dial action>. Conference and
# participant lookups retry over this schedule (seconds) before giving up.
CONFERENCE_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0, 3.0)

# Twilio error codes that mean "that leg is not live any more".
TWILIO_LEG_GONE_CODES = (21220, 20404)


class Channel(models.Model):
    _inherit = 'connect.channel'

    @api.model
    def on_call_status(self, params):
        """Twilio webhook adapter: map Twilio params and delegate to core."""
        debug(self, 'On channel status: %s' % json.dumps(params, indent=2))
        generic = self._map_twilio_params(params)
        return self.process_channel_event(generic)

    def _strip_exten_plus(self, number):
        """Undo the E.164 prefix Twilio puts on an extension used as caller ID.

        Internal calls hand Twilio a bare extension (``100``) as the caller
        ID, and Twilio echoes it back in its webhooks as ``+100``. Map it back
        to the extension so the ledger shows ``100`` instead of a bogus
        phone number.
        """
        if not isinstance(number, str) or not number.startswith('+'):
            return number
        candidate = number[1:]
        if not candidate.isdigit() or len(candidate) > MAX_EXTEN_LEN:
            return number
        exten = self.env['connect.twilio.exten'].sudo().search(
            [('number', '=', candidate)], limit=1
        )
        return candidate if exten else number

    def _map_twilio_params(self, params):
        """Map Twilio webhook params to generic channel event dict."""
        def strip_whatsapp(v):
            return (
                v.split(':', 1)[1]
                if isinstance(v, str) and v.startswith('whatsapp:')
                else v
            )

        caller_raw = params.get('Caller')
        called_raw = params.get('Called')
        to_raw = params.get('To')
        call_type = (
            'whatsapp'
            if any(
                isinstance(x, str) and x.startswith('whatsapp:')
                for x in [caller_raw, called_raw, to_raw]
            )
            else 'phone'
        )

        return {
            'sid': params['CallSid'],
            'caller': self._strip_exten_plus(strip_whatsapp(caller_raw)),
            'called': self._strip_exten_plus(strip_whatsapp(called_raw)),
            'to': strip_whatsapp(to_raw),
            'technical_direction': params['Direction'],
            'status': params['CallStatus'],
            'duration': int(params.get('CallDuration', 0)),
            'call_type': call_type,
            'parent_sid': params.get('ParentCallSid'),
        }

    def _twilio_recording_call_sids(self):
        """Call SIDs that may carry a recording for this channel.

        A callflow records on the leg that runs ``<Dial record=...>`` — the
        parent — while manual softphone recording is created on this leg, so
        both have to be considered before reporting a state.
        """
        self.ensure_one()
        sids = []
        channel = self
        while channel:
            if channel.sid and channel.sid not in sids:
                sids.append(channel.sid)
            parent = channel.parent_channel
            if not parent and channel.parent_sid:
                parent = self.sudo().search(
                    [('sid', '=', channel.parent_sid)], limit=1)
            if not parent or parent.sid in sids:
                break
            channel = parent
        return sids

    def _twilio_active_recording(self):
        """Return ``(call_sid, recording_sid)`` of a recording running now.

        Twilio is the only authority on this: recording may have been started
        by a callflow, by the per-user Record Calls option or manually from
        the softphone, and only the API knows which of them is live.
        """
        self.ensure_one()
        try:
            client = self.env['connect.settings'].sudo().get_client()
        except Exception:
            logger.exception('Twilio client unavailable for %s', self.sid)
            return '', ''
        for call_sid in self._twilio_recording_call_sids():
            try:
                # CallRecordingList.list() filters by date only. Asking it for
                # status='in-progress' raises TypeError, and the except below
                # swallowed it -- so a recording running on the call was never
                # seen and the softphone button stayed idle through a call the
                # Record Calls option was recording.
                # ...and matching only 'in-progress' was still wrong: Twilio
                # reports a recording that is still running as 'processing'
                # (duration -1). Measured on a live call — recording
                # RE4df4b06d on an in-progress call came back 'processing',
                # and across 50 recordings on the account only 'processing'
                # (live) and 'completed' (finished) ever appeared, so the
                # 'in-progress' test never matched and the button stayed on
                # "Start Recording" for the whole call.
                recordings = [
                    recording
                    for recording in client.calls(call_sid).recordings.list(
                        limit=20)
                    if getattr(recording, 'status', '')
                    in TWILIO_LIVE_RECORDING_STATUSES
                ]
            except Exception:
                logger.exception(
                    'Twilio recording lookup failed for %s', call_sid)
                continue
            if recordings:
                return call_sid, getattr(recordings[0], 'sid', '') or ''
        return '', ''

    def _twilio_set_recording_status(self, channel, status):
        """Ask Twilio to pause, resume or stop. Return (applied, note)."""
        call_sid, recording_ref = channel._twilio_active_recording()
        if not call_sid:
            call_sid = channel.sid
            recording_ref = channel.recording_control_ref or ''
        if not recording_ref or recording_ref in ('manual-off', 'stopped'):
            recording_ref = 'Twilio.CURRENT'
        try:
            self.env['connect.settings'].sudo().get_client().calls(
                call_sid).recordings(recording_ref).update(status=status)
            return True, ''
        except Exception as error:
            logger.exception(
                'Twilio recording %s failed for %s', status, channel.sid)
            return False, str(error)

    @api.model
    def _softphone_recording_state_twilio(self, payload):
        channel = self._softphone_recording_channel(payload)
        if channel._recording_was_stopped():
            Mark = self.env['connect.recording.mark'].sudo()
            stop = Mark.search(
                [('channel', '=', channel.id), ('kind', '=', 'stop')],
                order='id desc', limit=1)
            if stop and not stop.provider_applied:
                applied, note = self._twilio_set_recording_status(
                    channel, 'stopped')
                if applied:
                    stop.write({
                        'provider_applied': True,
                        'provider_note': note or stop.provider_note,
                    })
            result = channel._softphone_recording_payload()
            result['state'] = 'stopped'
            return result
        Mark = self.env['connect.recording.mark'].sudo()
        last = Mark.search(
            [('channel', '=', channel.id)],
            order='occurred_at desc, id desc', limit=1)
        if last and last.kind == 'pause':
            result = channel._softphone_recording_payload()
            result['state'] = 'paused'
            return result
        result = channel._softphone_recording_payload()
        if result['state'] in ('off', '') and not result['recording_ref']:
            call_sid, recording_sid = channel._twilio_active_recording()
            if call_sid:
                result['state'] = 'on'
                result['recording_ref'] = recording_sid
                if not Mark.search_count([
                    ('channel', '=', channel.id),
                    ('kind', '=', 'start'),
                ]):
                    channel._append_recording_mark(
                        'start', True, 'observed on the provider')
        return result

    @api.model
    def _softphone_recording_start_twilio(self, payload):
        channel = self._softphone_recording_channel(payload)
        channel._check_softphone_recording_active()
        if channel._recording_was_stopped():
            raise UserError(
                'Recording stays off for the rest of this call.')
        channel.sudo().write({
            'recording_state': 'starting',
            'recording_control_error': False,
        })
        try:
            settings = self.env['connect.settings'].sudo()
            api_url = settings.get_param('api_url')
            edge = settings.get_param('twilio_edge')
            callback_url = urljoin(
                api_url or '',
                'twilio/webhook/recordingstatus#e={}'.format(edge or ''),
            )
            kwargs = {
                'recording_channels': 'dual',
            }
            if api_url:
                kwargs.update({
                    'recording_status_callback': callback_url,
                    'recording_status_callback_event': ['completed'],
                })
            recording = settings.get_client().calls(
                channel.sid).recordings.create(**kwargs)
            channel.sudo().write({
                'recording_control_ref': getattr(recording, 'sid', '') or '',
            })
            channel._append_recording_mark('start', True)
        except Exception as e:
            logger.exception('Twilio recording start failed for %s', channel.sid)
            channel.sudo().write({
                'recording_state': 'error',
                'recording_control_error': str(e),
            })
            raise UserError('Could not start recording: {}'.format(e))
        return channel._softphone_recording_payload()

    @api.model
    def _softphone_recording_pause_twilio(self, payload):
        channel = self._softphone_recording_channel(payload)
        channel._check_softphone_recording_active()
        if channel._recording_was_stopped():
            raise UserError('Recording stays off for the rest of this call.')
        applied, note = self._twilio_set_recording_status(channel, 'paused')
        channel._append_recording_mark('pause', applied, note)
        return channel._softphone_recording_payload()

    @api.model
    def _softphone_recording_resume_twilio(self, payload):
        channel = self._softphone_recording_channel(payload)
        channel._check_softphone_recording_active()
        if channel._recording_was_stopped():
            raise UserError('Recording stays off for the rest of this call.')
        applied, note = self._twilio_set_recording_status(channel, 'in-progress')
        channel._append_recording_mark('resume', applied, note)
        return channel._softphone_recording_payload()

    @api.model
    def _softphone_recording_stop_twilio(self, payload):
        channel = self._softphone_recording_channel(payload)
        channel._check_softphone_recording_active()
        applied, note = self._twilio_set_recording_status(channel, 'stopped')
        channel._append_recording_mark('stop', applied, note)
        return channel._softphone_recording_payload()

    def _softphone_forward_twilio(self, payload):
        """Blind-transfer the call this softphone leg belongs to.

        The other party's leg is pointed back at the same TwiML application
        that routes every outgoing call, with the destination carried in
        `forward_to`. Routing is therefore not reimplemented here: an
        extension resolves to the colleague's client or SIP phone and an
        external number is dialled with the usual caller ID, because
        `connect.twilio.domain.route_call()` does the deciding either way.

        Redirecting the *other* leg is what makes this work in both
        directions. On an inbound call that leg is the customer; on an
        outbound one it is the person we called. Either way it is the party
        who should end up talking to the forward target, and our own leg drops
        as soon as the bridge goes away.
        """
        channel = self._softphone_recording_channel(payload)
        # Same resolution and ownership checks the recording controls use:
        # the helper answers "which live leg is this softphone on, and may
        # this user touch it?", which is not specific to recording.
        channel._check_softphone_recording_active()
        number = (payload.get('number') or '').strip()
        if not number:
            raise UserError('No number to forward the call to.')

        client = self.env['connect.settings'].get_client()
        other_sid = channel._forward_target_sid(client)

        pbx_user = (
            channel.caller_pbx_user
            or channel.called_pbx_user
            or self.env.user.connect_user
        )
        application = pbx_user.domain.application or pbx_user.application
        if not application or not application.voice_url:
            raise UserError(
                'No Twilio application is configured to route the forwarded '
                'call. Set a SIP domain application in Connect settings.')

        # Announce before moving them. Being redirected mid-sentence with no
        # warning reads as a dropped call; a short line and a beat of silence
        # is what the other Connect generation plays here too.
        response = VoiceResponse()
        response.say('Transferring your call now.')
        response.pause(length=1)
        response.redirect(
            self._url_with_query(application.voice_url, {'forward_to': number}),
            method='POST',
        )

        try:
            client.calls(other_sid).update(twiml=str(response))
        except Exception as e:
            logger.exception('Could not forward call %s to %s', other_sid, number)
            raise UserError('Could not forward the call: {}'.format(e))

        debug(self, 'Forwarded channel %s to %s' % (other_sid, number))
        return {'forwarded_to': number, 'channel_sid': other_sid}

    # ------------------------------------------------------------------
    # Hold, attended transfer, add participant (child-leg-first promotion)
    # ------------------------------------------------------------------

    def _twilio_bridge_legs(self, client):
        """Return `(our_sid, other_sid, child_sid)` for this softphone leg.

        A `<Dial>` bridge has a parent (the leg running the `<Dial>`) and a
        child (the dialled leg). Only the child may be redirected: moving it
        completes the parent's `<Dial>`, whose action then rejoins the parent
        to the same room. Redirecting the parent would hang the child up.

        The ledger answers from `technical_direction` -- `inbound` is the
        parent, `outbound-dial` the child, our leg being the agent's in both
        directions. Twilio is asked only when the ledger cannot say.
        """
        self.ensure_one()
        other_sid = self._forward_target_sid(client)
        other = self.sudo().search([('sid', '=', other_sid)], limit=1)
        if self.technical_direction == 'outbound-dial' or (
                other and other.technical_direction == 'inbound'):
            child_sid = self.sid
        elif self.technical_direction == 'inbound' or (
                other and other.technical_direction == 'outbound-dial'):
            child_sid = other_sid
        else:
            try:
                parent = getattr(
                    client.calls(self.sid).fetch(), 'parent_call_sid', None)
            except Exception as e:
                logger.exception('Could not ask Twilio about leg %s', self.sid)
                raise UserError(
                    'Could not work out how this call is connected: '
                    '{}'.format(e))
            child_sid = self.sid if parent else other_sid
        return self.sid, other_sid, child_sid

    @api.model
    def _twilio_call_control_error(self, error, doing):
        """A sentence for the user; the Twilio text stays in the log."""
        code = getattr(error, 'code', None)
        if code in TWILIO_LEG_GONE_CODES:
            return 'The call ended before it could be {}.'.format(doing)
        return 'The call could not be {}: {}'.format(
            doing, getattr(error, 'msg', None) or error)

    def _promote_to_conference_twilio(self, client):
        """Move this call into a conference room, child leg first.

        Returns `(call, conference_name, customer_sid)`. The room name and the
        `conference_pending` marker are committed before Twilio is touched,
        because the parent leg's `<Dial action>` webhook -- a separate
        request -- must find them while this RPC is still running.
        """
        self.ensure_one()
        call = self.call.sudo()
        if not call:
            raise UserError('This call is not in the call log yet.')
        our_sid, other_sid, child_sid = self._twilio_bridge_legs(client)
        if call.conference_name:
            return call, call.conference_name, other_sid

        conference_name = 'callcontrol-{}-{}'.format(call.id, uuid.uuid4().hex)
        call._conference_state_commit({
            'conference_name': conference_name,
            'conference_sid': False,
            'conference_pending': True,
            'is_on_hold': False,
        })
        twiml = call._conference_join_twiml(
            conference_name, agent=(child_sid == our_sid))
        try:
            client.calls(child_sid).update(twiml=twiml)
        except Exception as e:
            logger.exception(
                'Could not move leg %s into conference %s',
                child_sid, conference_name)
            # Never leave a stored room that only one leg could reach.
            call._conference_state_commit({
                'conference_name': False,
                'conference_pending': False,
            })
            raise UserError(self._twilio_call_control_error(e, 'held'))
        logger.info(
            'Call %s promoted to conference %s (moved child leg %s)',
            call.id, conference_name, child_sid)
        return call, conference_name, other_sid

    @api.model
    def _twilio_conference_sid(self, client, call, conference_name):
        """Resolve the room's SID by name, waiting for Twilio to create it."""
        if call.conference_sid:
            return call.conference_sid
        for attempt, delay in enumerate(CONFERENCE_RETRY_DELAYS, 1):
            try:
                rooms = client.conferences.list(
                    friendly_name=conference_name, status='in-progress',
                    limit=1)
            except Exception:
                logger.warning(
                    'Conference lookup %s failed (attempt %s)',
                    conference_name, attempt, exc_info=True)
                rooms = []
            if rooms:
                sid = rooms[0].sid
                call._conference_state_commit({'conference_sid': sid})
                return sid
            if attempt < len(CONFERENCE_RETRY_DELAYS):
                time.sleep(delay)
        return None

    @api.model
    def _twilio_update_participant(self, client, conference_sid, call_sid, **kwargs):
        """`participants(call_sid).update(...)`, waiting for the leg to join.

        On an inbound call the customer is the parent and reaches the room
        only after its `<Dial action>` has run, so a 404 straight after the
        promotion means "not yet", not "never".
        """
        for attempt, delay in enumerate(CONFERENCE_RETRY_DELAYS, 1):
            try:
                return client.conferences(conference_sid).participants(
                    call_sid).update(**kwargs)
            except TwilioRestException as e:
                if e.status != 404 or attempt == len(CONFERENCE_RETRY_DELAYS):
                    raise
            time.sleep(delay)

    def _twilio_set_hold(self, hold):
        """Hold or resume the other party. Shared by hold/resume/attended."""
        self.ensure_one()
        client = self.env['connect.settings'].get_client()
        if hold:
            call, conference_name, customer_sid = (
                self._promote_to_conference_twilio(client))
        else:
            call = self.call.sudo()
            conference_name = call.conference_name
            if not conference_name:
                raise UserError('This call is not on hold.')
            _ours, customer_sid, _child = self._twilio_bridge_legs(client)
        conference_sid = self._twilio_conference_sid(
            client, call, conference_name)
        if not conference_sid:
            raise UserError(
                'The caller is waiting in conference {} but it could not be '
                'reached to {} them.'.format(
                    conference_name, 'hold' if hold else 'resume'))
        kwargs = {'hold': bool(hold)}
        if hold:
            kwargs['hold_url'] = HOLD_MUSIC_URL
        try:
            self._twilio_update_participant(
                client, conference_sid, customer_sid, **kwargs)
        except Exception as e:
            logger.exception(
                'Could not %s %s in conference %s',
                'hold' if hold else 'resume', customer_sid, conference_name)
            if hold:
                raise UserError(
                    'Customer is waiting in conference {} but could not be '
                    'put on hold: {}'.format(
                        conference_name,
                        self._twilio_call_control_error(e, 'held')))
            raise UserError(self._twilio_call_control_error(e, 'resumed'))
        call._conference_state_commit({'is_on_hold': bool(hold)})
        return {
            'success': True,
            'is_on_hold': bool(hold),
            'conference_name': conference_name,
            'channel_sid': self.sid,
        }

    def _twilio_participant_target(self, number):
        """`to` and `from_` for adding `number` to a conference."""
        self.ensure_one()
        number = (number or '').strip()
        if not number:
            raise UserError('No number to add to the call.')
        pbx_user = (
            self.caller_pbx_user or self.called_pbx_user
            or self.env.user.connect_user)
        caller_id = pbx_user.twilio_outgoing_callerid.number if pbx_user else False
        if not caller_id:
            caller_id = self.env['connect.twilio.outgoing_callerid'].sudo().search(
                [('is_default', '=', True)], limit=1).number
        if not caller_id:
            raise UserError('Select a default number for caller ID first.')
        if number.startswith('+'):
            return number, caller_id
        exten = self.env['connect.twilio.exten'].sudo().search(
            [('number', '=', number)], limit=1)
        if exten and exten.dst and exten.dst._name == 'connect.user':
            return 'client:{}'.format(exten.dst.get_client_identity()), caller_id
        if number.isdigit() and len(number) > MAX_EXTEN_LEN:
            return '+{}'.format(number), caller_id
        raise UserError(
            'Extension {} is not a user and cannot join a call.'.format(number))

    def _twilio_add_to_conference(self, number, end_on_exit):
        self.ensure_one()
        to, caller_id = self._twilio_participant_target(number)
        client = self.env['connect.settings'].get_client()
        call, conference_name, customer_sid = (
            self._promote_to_conference_twilio(client))
        conference_sid = self._twilio_conference_sid(
            client, call, conference_name)
        if not conference_sid:
            raise UserError(
                'The caller is waiting in conference {} but it could not be '
                'reached to add {}.'.format(conference_name, number))
        settings = self.env['connect.settings'].sudo()
        status_url = urljoin(
            settings.get_param('api_url') or '',
            'twilio/webhook/callstatus#e={}'.format(
                settings.get_param('twilio_edge') or ''))
        try:
            client.conferences(conference_sid).participants.create(
                from_=caller_id,
                to=to,
                end_conference_on_exit=end_on_exit,
                beep=False,
                status_callback=status_url,
                status_callback_event=['initiated', 'ringing', 'answered',
                                       'completed'],
            )
        except Exception as e:
            logger.exception(
                'Could not add %s to conference %s', to, conference_name)
            raise UserError('Could not add {} to the call: {}'.format(
                number, getattr(e, 'msg', None) or e))
        return call, conference_sid, customer_sid

    @api.model
    def _softphone_hold_twilio(self, payload):
        channel = self._softphone_recording_channel(payload)
        channel._check_softphone_recording_active()
        return channel._twilio_set_hold(True)

    @api.model
    def _softphone_resume_twilio(self, payload):
        channel = self._softphone_recording_channel(payload)
        channel._check_softphone_recording_active()
        return channel._twilio_set_hold(False)

    @api.model
    def _softphone_attended_start_twilio(self, payload):
        """Hold the caller so the user can consult the transfer target."""
        return self._softphone_hold_twilio(payload)

    @api.model
    def _softphone_attended_cancel_twilio(self, payload):
        """The consult went nowhere: back to the caller."""
        return self._softphone_resume_twilio(payload)

    @api.model
    def _softphone_attended_complete_twilio(self, payload):
        """Bring the target into the room, release the caller, drop out."""
        channel = self._softphone_recording_channel(payload)
        channel._check_softphone_recording_active()
        number = (payload.get('number') or '').strip()
        call, conference_sid, customer_sid = channel._twilio_add_to_conference(
            number, end_on_exit=True)
        client = self.env['connect.settings'].get_client()
        if call.is_on_hold:
            try:
                self._twilio_update_participant(
                    client, conference_sid, customer_sid, hold=False)
                call._conference_state_commit({'is_on_hold': False})
            except Exception:
                logger.exception(
                    'Could not take %s off hold in %s',
                    customer_sid, conference_sid)
        # Leave the room. Our leg joined with endConferenceOnExit, so drop
        # that first or leaving would end the call we just handed over. And
        # staying would let the user listen in, so fall back to hanging up.
        try:
            participant = client.conferences(conference_sid).participants(
                channel.sid)
            participant.update(end_conference_on_exit=False)
            participant.delete()
        except Exception:
            logger.warning(
                'Could not remove %s from %s; hanging the leg up',
                channel.sid, conference_sid, exc_info=True)
            try:
                client.calls(channel.sid).update(status='completed')
            except Exception:
                logger.exception('Could not hang up %s', channel.sid)
        return {
            'success': True,
            'transferred_to': number,
            'channel_sid': channel.sid,
        }

    @api.model
    def _softphone_add_participant_twilio(self, payload):
        channel = self._softphone_recording_channel(payload)
        channel._check_softphone_recording_active()
        number = (payload.get('number') or '').strip()
        channel._twilio_add_to_conference(number, end_on_exit=False)
        return {
            'success': True,
            'added': number,
            'channel_sid': channel.sid,
        }

    def _forward_target_sid(self, client):
        """The SID of the leg to move: the other party on this call.

        The ledger answers this without a round-trip, and answers it the same
        way in both directions -- the sibling of the softphone's leg is the
        customer on an inbound call and the callee on an outbound one, and is
        never our own leg, so the `<Dial>` we are running cannot be torn down
        underneath us.

        It only answers when the ledger is complete, though. A channel row
        still in flight, or a third leg left up by a ring group, and there is
        no single sibling to pick. Twilio knows regardless, so fall back to
        asking it rather than refusing a transfer the user can see is
        possible.
        """
        self.ensure_one()
        siblings = (self.call.channels - self).filtered(
            lambda c: c.status not in CALL_END_STATUSES)
        if len(siblings) == 1:
            return siblings.sid

        logger.info(
            'Ledger shows %s other live legs on call %s; asking Twilio which '
            'leg to forward', len(siblings), self.call.id)
        try:
            live = self._twilio_sibling_sid(client)
        except Exception as e:
            logger.exception('Could not ask Twilio for the other leg of %s', self.sid)
            raise UserError('Could not work out which party to forward: {}'.format(e))
        if not live:
            raise UserError(
                'This call cannot be forwarded: there is no other party on '
                'the line.')
        return live

    def _twilio_sibling_sid(self, client):
        """Ask Twilio for the other leg: our parent, or failing that our child.

        A softphone leg is the child on an inbound call (the customer dialled
        in and the platform dialled us) and the parent on an outbound one (we
        dialled out), so the other party is whichever of the two we are not.
        """
        self.ensure_one()
        ours = client.calls(self.sid).fetch()
        parent_sid = getattr(ours, 'parent_call_sid', None)
        if parent_sid:
            return parent_sid
        for child in client.calls.list(parent_call_sid=self.sid, limit=20):
            if child.status not in CALL_END_STATUSES:
                return child.sid
        return None

    @api.model
    def _url_with_query(self, url, params):
        """Add query parameters to a URL that may already carry a fragment.

        TwiML voice URLs end in `#e=<edge>`, so appending `?x=y` naively would
        bury the query inside the fragment and Twilio would never send it.
        """
        parts = urlsplit(url)
        query = '&'.join(filter(None, [parts.query, urlencode(params)]))
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    def connect_notify(
        self, title='Connect', sticky=False, warning=False
    ):
        """Notify user about incoming call."""
        # The message is rendered as trusted HTML (markup()) on the
        # client, and self.caller / self.partner.name are attacker-
        # controlled (inbound caller-id, partner name), so every dynamic
        # value is HTML-escaped before interpolation to prevent XSS.
        caller = escape(self.caller or '')
        caller_avatar = '/web/static/img/placeholder.png'
        if self.partner:
            caller = """
                <p class="text-center"><strong>Partner:</strong>
                <a href='/web#id={}&model={}&view_type=form'>
                    {}
                </a>
                </p>
            """.format(self.partner.id, 'res.partner', escape(self.partner.name or ''))
            caller_avatar = '/web/image/res.partner/{}/image_1024'.format(
                self.partner.id
            )
        elif self.caller_user:
            caller_avatar = '/web/image/res.users/{}/image_1024'.format(
                self.caller_user.id
            )

        message = """
        <div class="d-flex align-items-center justify-content-center">
            <div>
                <img style="max-height: 100px; max-width: 100px;"
                        class="rounded-circle"
                        src="{}"/>
            </div>
            <div>
                <p class="text-center">Incoming call</p>
                {}
            </div>
        </div>
        """.format(caller_avatar, caller)

        if release.version_info[0] < 15:
            self.env['bus.bus'].sendone(
                'connect_actions_{}'.format(self.called_user.id),
                {
                    'action': 'notify',
                    'message': message,
                    'title': title,
                    'sticky': sticky,
                    'warning': warning,
                },
            )
        else:
            self.env['bus.bus']._sendone(
                'connect_actions_{}'.format(self.called_user.id),
                'connect_notify',
                {
                    'message': message,
                    'title': title,
                    'sticky': sticky,
                    'warning': warning,
                },
            )
        return True
