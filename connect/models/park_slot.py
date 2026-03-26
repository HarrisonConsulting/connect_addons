# -*- coding: utf-8 -*-

import logging

from odoo import fields, models, api
from odoo.exceptions import UserError
from twilio.twiml.voice_response import VoiceResponse, Dial
from .settings import debug

logger = logging.getLogger(__name__)

PARK_SLOTS = [(str(i), f'Slot {i}') for i in range(1, 10)]


class ParkSlot(models.Model):
    _name = 'connect.park_slot'
    _description = 'Call Park Slot'

    name = fields.Selection(PARK_SLOTS, string='Slot', required=True, index=True,
                            help='Park slot number (1-9)')
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

    _sql_constraints = [
        ('slot_unique', 'unique(name, is_occupied)', 'Park slot already in use'),
    ]

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

    @api.model
    def park_call(self, call_sid, slot_number):
        """Park the current call on a numbered slot.

        Moves the remote party into a Twilio conference with hold music,
        then hangs up the user's leg. Any user can later pick up the call
        from this slot.
        """
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

        try:
            # Put caller into park conference with hold music
            response = VoiceResponse()
            dial = Dial()
            dial.conference(
                conf_name,
                startConferenceOnEnter=True,
                endConferenceOnExit=False,
                beep=False,
                waitUrl='http://twimlets.com/holdmusic?Bucket=com.twilio.music.classical',
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

    def action_unpark(self):
        """Button action to pick up a parked call from the UI."""
        self.ensure_one()
        result = self.unpark_call(self.name)
        if not result.get('success'):
            raise UserError(result.get('error', 'Failed to pick up call'))

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
        } for s in slots]
