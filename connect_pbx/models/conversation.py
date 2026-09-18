import logging

from odoo import models, fields, api
from odoo.exceptions import ValidationError
from odoo.models import Constraint

logger = logging.getLogger(__name__)


class ConnectConversation(models.Model):
    _name = 'connect.conversation'
    _description = 'Message Conversation'
    _order = 'last_message_date DESC'
    _rec_name = 'name'

    name = fields.Char(compute='_compute_name', store=True, help="Display name: partner name or phone number")
    conversation_key = fields.Char(
        string='Conversation Key', compute='_compute_conversation_key',
        store=True, index=True, help="Canonical key for deduplication: channel:phone1|phone2 (sorted)")
    channel_type = fields.Selection([
        ('sms', 'SMS'),
        ('whatsapp', 'WhatsApp'),
    ], required=True, default='sms', help="Communication channel for this conversation")
    phone_a = fields.Char(
        string='Our Number', required=True,
        help="The number belonging to our organization")
    phone_b = fields.Char(
        string='Their Number', required=True,
        help="The external party phone number")
    partner_id = fields.Many2one(
        'res.partner', string='Contact', ondelete='set null', index=True,
        help="Resolved contact for the external party")
    partner_image = fields.Binary(related='partner_id.image_128')
    message_ids = fields.One2many(
        'connect.message', 'conversation_id', string='Messages')
    last_message_date = fields.Datetime(
        compute='_compute_last_message', store=True,
        help="Date of the most recent message")
    last_message_body = fields.Text(
        compute='_compute_last_message', store=True,
        help="Preview of the most recent message")
    last_message_direction = fields.Selection([
        ('incoming', 'Incoming'),
        ('outgoing', 'Outgoing'),
    ], compute='_compute_last_message', store=True)
    message_count = fields.Integer(
        compute='_compute_last_message', store=True,
        help="Total number of messages in conversation")
    has_error = fields.Boolean(
        compute='_compute_has_error', store=True,
        help="Whether any message in this conversation has an error")
    active = fields.Boolean(default=True)

    _conversation_key_unique = Constraint(
        'UNIQUE(conversation_key)',
        'A conversation with this phone pair and channel already exists!')

    @api.depends('partner_id', 'partner_id.name', 'phone_b')
    def _compute_name(self):
        for rec in self:
            if rec.partner_id and rec.partner_id.name:
                rec.name = rec.partner_id.name
            elif rec.phone_b:
                rec.name = rec.phone_b
            else:
                rec.name = 'New Conversation'

    @api.depends('channel_type', 'phone_a', 'phone_b')
    def _compute_conversation_key(self):
        for rec in self:
            phones = sorted([rec.phone_a or '', rec.phone_b or ''])
            rec.conversation_key = f"{rec.channel_type}:{phones[0]}|{phones[1]}"

    @api.depends('message_ids.create_date', 'message_ids.body', 'message_ids.direction')
    def _compute_last_message(self):
        for rec in self:
            messages = rec.message_ids.sorted('create_date', reverse=True)
            if messages:
                last = messages[0]
                rec.last_message_date = last.create_date
                rec.last_message_body = (last.body or '')[:200]
                rec.last_message_direction = last.direction
            else:
                rec.last_message_date = False
                rec.last_message_body = False
                rec.last_message_direction = False
            rec.message_count = len(rec.message_ids)

    @api.depends('message_ids.has_error')
    def _compute_has_error(self):
        for rec in self:
            rec.has_error = any(m.has_error for m in rec.message_ids)

    @api.model
    def get_or_create(self, channel_type, phone_a, phone_b, partner=None):
        """Find or create a conversation for the given phone pair and channel.

        Returns the conversation record.
        """
        phones = sorted([phone_a or '', phone_b or ''])
        key = f"{channel_type}:{phones[0]}|{phones[1]}"
        conv = self.search([('conversation_key', '=', key)], limit=1)
        if conv:
            # Update partner if we have one now and didn't before
            if partner and not conv.partner_id:
                conv.partner_id = partner
            return conv
        vals = {
            'channel_type': channel_type,
            'phone_a': phone_a,
            'phone_b': phone_b,
            'partner_id': partner.id if partner else False,
        }
        return self.create(vals)

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        # Default phone_a from user's outgoing callerid
        try:
            user = self.env.user
            if user.connect_user and user.connect_user.outgoing_callerid:
                vals['phone_a'] = user.connect_user.outgoing_callerid.number
        except Exception:
            pass
        return vals

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            phone = self.partner_id.phone_sanitized or self.partner_id.mobile or self.partner_id.phone
            if phone:
                self.phone_b = phone

    @api.onchange('channel_type')
    def _onchange_channel_type(self):
        if self.channel_type == 'whatsapp':
            try:
                sender = self.env['connect.whatsapp_sender'].get_default_sender()
                if sender:
                    self.phone_a = sender.number
            except Exception:
                pass
        elif self.channel_type == 'sms':
            try:
                user = self.env.user
                if user.connect_user and user.connect_user.outgoing_callerid:
                    self.phone_a = user.connect_user.outgoing_callerid.number
            except Exception:
                pass

    def send_message(self, body):
        """Send a message in this conversation. Routes to SMS or WhatsApp."""
        self.ensure_one()
        if not body:
            raise ValidationError('Message body cannot be empty.')
        if self.channel_type == 'sms':
            self.env['connect.message'].send(
                recipient=self.phone_b,
                body=body,
                outgoing_callerid=self.phone_a,
            )
        elif self.channel_type == 'whatsapp':
            sender = self.env['connect.whatsapp_sender'].search(
                [('number', '=', self.phone_a)], limit=1)
            if not sender:
                sender = self.env['connect.whatsapp_sender'].get_default_sender()
            if not sender:
                raise ValidationError('No WhatsApp sender available for this number.')
            sender.send_whatsapp(
                recipient=self.phone_b,
                body=body,
            )

    def get_messages(self, limit=100, offset=0):
        """Return messages for the chat thread widget, ordered oldest first."""
        self.ensure_one()
        messages = self.env['connect.message'].search_read(
            [('conversation_id', '=', self.id)],
            fields=[
                'body', 'direction', 'create_date', 'sender_user',
                'partner', 'media_widget', 'status', 'status_display',
                'message_type', 'has_error', 'from_number', 'to_number',
            ],
            order='create_date ASC',
            limit=limit,
            offset=offset,
        )
        return messages
