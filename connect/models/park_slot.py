# -*- coding: utf-8 -*-

import logging
from datetime import timedelta

from odoo import fields, models, api
from odoo.exceptions import UserError, ValidationError
from twilio.twiml.voice_response import VoiceResponse, Dial
from .settings import debug

logger = logging.getLogger(__name__)

DEFAULT_HOLD_MUSIC = 'http://twimlets.com/holdmusic?Bucket=com.twilio.music.classical'


class ParkSlot(models.Model):
    _name = 'connect.park_slot'
    _description = 'Call Park Slot'
    _order = 'name'

    name = fields.Integer(string='Slot', required=True, index=True,
                          help='Park slot number')
    call = fields.Many2one('connect.call', string='Parked Call', ondelete='set null',
                           help='The call currently parked in this slot')
    caller_channel_sid = fields.Char(string='Caller Channel SID',
                                     help='Twilio Call SID of the parked caller leg')
    conference_name = fields.Char(string='Conference Name',
                                  help='Twilio conference friendly name for this park slot')
    conference_sid = fields.Char(string='Conference SID',
                                 help='Twilio Conference SID')
    parked_by = fields.Many2one('res.users', string='Parked By',
                                help='User who parked the call')
    parked_at = fields.Datetime(string='Parked At', default=fields.Datetime.now,
                                help='When the call was parked')
    is_occupied = fields.Boolean(string='Occupied', compute='_compute_is_occupied', store=True,
                                 help='Whether this slot currently has a parked call')
    caller_display = fields.Char(string='Caller', compute='_compute_caller_display',
                                 help='Display name of the parked caller')
    park_duration = fields.Integer(string='Duration (s)', compute='_compute_park_duration',
                                   help='How long the call has been parked in seconds')

    _name_unique = models.Constraint(
        'unique(name)',
        'Park slot number must be unique',
    )

    @api.constrains('name')
    def _check_slot_number(self):
        for rec in self:
            max_slots = self.env['connect.settings'].sudo().get_param('park_slot_count') or 9
            if rec.name < 1 or rec.name > max_slots:
                raise ValidationError(
                    f'Slot number must be between 1 and {max_slots}'
                )

    @api.depends('call')
    def _compute_is_occupied(self):
        for rec in self:
            rec.is_occupied = bool(rec.call)

    def _compute_caller_display(self):
        for rec in self:
            if rec.call:
                rec.caller_display = rec.call.partner.name if rec.call.partner else rec.call.caller or 'Unknown'
            else:
                rec.caller_display = ''

    def _compute_park_duration(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.is_occupied and rec.parked_at:
                delta = now - rec.parked_at
                rec.park_duration = int(delta.total_seconds())
            else:
                rec.park_duration = 0

    @api.model
    def sync_slots(self):
        """Create or remove slots to match the configured park_slot_count."""
        count = self.env['connect.settings'].sudo().get_param('park_slot_count') or 9
        count = max(1, min(99, count))
        existing = self.search([])
        existing_numbers = {s.name for s in existing}

        # Create missing slots
        to_create = []
        for i in range(1, count + 1):
            if i not in existing_numbers:
                to_create.append({'name': i})
        if to_create:
            self.create(to_create)

        # Remove excess empty slots (only those beyond the new count)
        excess = existing.filtered(lambda s: s.name > count and not s.call)
        if excess:
            excess.unlink()

        return True

    @api.model
    def init(self):
        """Ensure default park slots exist on module install/update."""
        super().init()
        # Use raw SQL check to avoid issues during module install
        self.env.cr.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'connect_park_slot'"
        )
        if self.env.cr.fetchone()[0]:
            try:
                self.sync_slots()
            except Exception:
                logger.warning('park_slot init: Could not sync slots (may be first install)')
                pass

    @api.model
    def park_call(self, call_sid, slot_number):
        """Park the current call on a numbered slot.

        Moves the remote party into a Twilio conference with hold music,
        then hangs up the user's leg. Any user can later pick up the call
        from this slot.
        """
        slot_number = int(slot_number)
        call, user_channel, other_channel = self.env['connect.call']._find_call_channels(call_sid)
        if not call:
            return {'success': False, 'error': 'Call not found'}
        if not user_channel or not other_channel:
            return {'success': False, 'error': 'Cannot identify call parties'}

        # Check if slot is available
        existing = self.search([('name', '=', slot_number), ('call', '!=', False)], limit=1)
        if existing:
            return {'success': False, 'error': f'Slot {slot_number} is occupied'}

        client = self.env['connect.settings'].get_client()
        conf_name = 'park-{}'.format(slot_number)

        # Get settings for hold music and announcements
        settings = self.env['connect.settings'].sudo()
        hold_audio = settings.park_hold_music_audio_id
        # get_play_url() returns None for twilio_tts (no media URL exists —
        # live <Say> can't be used as a conference waitUrl). Fall back to
        # the default Twimlet in that case so parked callers never land on
        # silent hold.
        hold_music_url = (hold_audio and hold_audio.get_play_url()) or settings.get_default_hold_music_url()
        announcement_enabled = settings.get_param('park_announcement_enabled')

        try:
            # Build TwiML for the parked caller
            response = VoiceResponse()

            # Optionally announce slot number before hold music
            if announcement_enabled:
                response.say(
                    f'Your call has been parked at slot {slot_number}.',
                    voice='Polly.Ruth-Generative',
                )

            dial = Dial()
            dial.conference(
                conf_name,
                startConferenceOnEnter=True,
                endConferenceOnExit=False,
                beep=False,
                waitUrl=hold_music_url,
            )
            response.append(dial)
            client.calls(other_channel.sid).update(twiml=str(response))

            # Hang up user's leg
            client.calls(user_channel.sid).update(status='completed')

            # Create or update slot record
            slot = self.search([('name', '=', slot_number)], limit=1)
            if not slot:
                slot = self.create({'name': slot_number})
            slot.write({
                'call': call.id,
                'caller_channel_sid': other_channel.sid,
                'conference_name': conf_name,
                'parked_by': self.env.user.id,
                'parked_at': fields.Datetime.now(),
            })

            logger.info('park_call: Call %s parked on slot %s by %s',
                        call.id, slot_number, self.env.user.login)

            # Notify all connect users
            self.env['connect.settings'].connect_reload_view('connect.park_slot')

            return {'success': True, 'slot': slot_number}

        except Exception as e:
            logger.exception('park_call: Failed to park call %s', call.id)
            return {'success': False, 'error': str(e)}

    @api.model
    def unpark_call(self, slot_number):
        """Pick up a parked call from a slot.

        Creates an outbound call to the current user that joins the park
        conference, reconnecting them with the parked caller.
        """
        slot_number = int(slot_number)
        slot = self.search([('name', '=', slot_number), ('call', '!=', False)], limit=1)
        if not slot:
            return {'success': False, 'error': f'Slot {slot_number} is empty'}

        current_user = self.env.user.connect_user
        if not current_user:
            return {'success': False, 'error': 'User not configured for telephony'}

        client = self.env['connect.settings'].get_client()
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
        status_url = '{}/twilio/webhook/callstatus#e={}'.format(api_url.rstrip('/'), edge)

        caller_id = current_user.outgoing_callerid.number if current_user.outgoing_callerid else None
        if not caller_id:
            default_number = self.env['connect.outgoing_callerid'].search(
                [('is_default', '=', True)], limit=1)
            caller_id = default_number.number if default_number else None
        if not caller_id:
            return {'success': False, 'error': 'No caller ID available'}

        try:
            # Create outbound call to current user that joins the park conference
            target_identity = current_user.get_client_identity()
            response = VoiceResponse()
            dial = Dial()
            dial.conference(
                slot.conference_name,
                startConferenceOnEnter=True,
                endConferenceOnExit=True,  # When agent hangs up, end conference
                beep=False,
            )
            response.append(dial)

            client.calls.create(
                to='client:{}'.format(target_identity),
                from_=caller_id,
                twiml=str(response),
                status_callback=status_url,
                status_callback_event=['initiated', 'answered', 'completed'],
            )

            # Clear the slot
            call_id = slot.call.id
            slot.write({
                'call': False,
                'caller_channel_sid': False,
                'conference_name': False,
                'conference_sid': False,
            })

            logger.info('unpark_call: Slot %s picked up by %s (call %s)',
                        slot_number, self.env.user.login, call_id)

            # Notify all connect users
            self.env['connect.settings'].connect_reload_view('connect.park_slot')

            return {'success': True}

        except Exception as e:
            logger.exception('unpark_call: Failed to unpark slot %s', slot_number)
            return {'success': False, 'error': str(e)}

    def _clear_slot(self):
        """Clear a park slot, resetting all call-related fields."""
        self.ensure_one()
        self.write({
            'call': False,
            'caller_channel_sid': False,
            'conference_name': False,
            'conference_sid': False,
            'parked_by': False,
            'parked_at': False,
        })

    def action_unpark(self):
        """Button action to pick up a parked call from the UI."""
        self.ensure_one()
        result = self.unpark_call(self.name)
        if not result.get('success'):
            raise UserError(result.get('error', 'Failed to pick up call'))

    @api.model
    def check_park_timeouts(self):
        """Cron job: check for parked calls that have exceeded the timeout.

        For each timed-out slot:
        1. Attempt to ring back the user who parked the call
        2. If that fails, clear the slot (caller remains in conference)
        """
        timeout = self.env['connect.settings'].sudo().get_param('park_timeout') or 0
        if not timeout:
            return

        cutoff = fields.Datetime.now() - timedelta(seconds=timeout)
        timed_out = self.search([
            ('call', '!=', False),
            ('parked_at', '<=', cutoff),
        ])

        if not timed_out:
            return

        client = self.env['connect.settings'].get_client()
        api_url = self.env['connect.settings'].sudo().get_param('api_url')
        edge = self.env['connect.settings'].sudo().get_param('twilio_edge')
        status_url = '{}/twilio/webhook/callstatus#e={}'.format(api_url.rstrip('/'), edge)

        for slot in timed_out:
            logger.info('check_park_timeouts: Slot %s timed out (parked by %s, call %s)',
                        slot.name, slot.parked_by.login if slot.parked_by else 'unknown',
                        slot.call.id)

            ring_back_success = False
            parker = slot.parked_by
            if parker and parker.connect_user:
                connect_user = parker.connect_user
                caller_id = (
                    connect_user.outgoing_callerid.number
                    if connect_user.outgoing_callerid else None
                )
                if not caller_id:
                    default_number = self.env['connect.outgoing_callerid'].search(
                        [('is_default', '=', True)], limit=1)
                    caller_id = default_number.number if default_number else None

                if caller_id:
                    try:
                        target_identity = connect_user.get_client_identity()
                        response = VoiceResponse()
                        response.say(
                            f'Parked call on slot {slot.name} has timed out.',
                            voice='Polly.Ruth-Generative',
                        )
                        dial = Dial()
                        dial.conference(
                            slot.conference_name,
                            startConferenceOnEnter=True,
                            endConferenceOnExit=True,
                            beep=False,
                        )
                        response.append(dial)

                        client.calls.create(
                            to='client:{}'.format(target_identity),
                            from_=caller_id,
                            twiml=str(response),
                            status_callback=status_url,
                            status_callback_event=['initiated', 'answered', 'completed'],
                        )
                        ring_back_success = True
                        logger.info('check_park_timeouts: Ring-back sent to %s for slot %s',
                                    parker.login, slot.name)
                    except Exception:
                        logger.exception(
                            'check_park_timeouts: Failed to ring back %s for slot %s',
                            parker.login, slot.name)

            if not ring_back_success:
                # Could not ring back parker - attempt voicemail redirect
                logger.warning(
                    'check_park_timeouts: Could not ring back parker for slot %s, '
                    'clearing slot', slot.name)

            # Clear the slot regardless - the ring-back creates a new call leg
            slot._clear_slot()

        # Notify all connect users of the change
        self.env['connect.settings'].connect_reload_view('connect.park_slot')

    @api.model
    def get_parked_calls(self):
        """Get list of all parked calls for UI display."""
        slots = self.search([('call', '!=', False)])
        return [{
            'slot': s.name,
            'caller': s.caller_display,
            'parked_by': s.parked_by.name if s.parked_by else '',
            'parked_at': s.parked_at.isoformat() if s.parked_at else '',
            'call_id': s.call.id,
            'park_duration': s.park_duration,
        } for s in slots]
