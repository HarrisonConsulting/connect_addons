# -*- coding: utf-8 -*-

from odoo import fields, models, api


class ConnectCallStripe(models.Model):
    _inherit = 'connect.call'

    stripe_payment_ids = fields.One2many(
        'connect.stripe.payment',
        'call_id',
        string='Stripe Payments',
        help='Phone payments initiated during this call via Twilio <Pay> and Stripe.',
    )
    stripe_payment_count = fields.Integer(
        compute='_compute_stripe_payment_count',
        string='Payments',
        help='Number of Stripe phone payments associated with this call.',
    )

    @api.depends('stripe_payment_ids')
    def _compute_stripe_payment_count(self):
        for rec in self:
            rec.stripe_payment_count = len(rec.stripe_payment_ids)

    def get_widget_fields(self):
        widget_fields = super().get_widget_fields()
        widget_fields.append('stripe_payment_count')
        return widget_fields

    @api.model
    def _conference_rejoin_twiml(self, request):
        """Inbound <Pay>: the customer is the parent leg.

        The agent child was moved into the room first; when the customer's
        `<Dial action>` fires, the parent goes to <Pay> instead of joining.
        """
        twiml = super()._conference_rejoin_twiml(request)
        if not twiml:
            return twiml
        payment = self.env['connect.stripe.payment'].sudo().search([
            ('twilio_call_sid', '=', (request or {}).get('CallSid')),
            ('state', '=', 'capturing'),
        ], order='id desc', limit=1)
        if payment:
            return payment._build_redirect_twiml('/connect/stripe/twiml')
        return twiml
