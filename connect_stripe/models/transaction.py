# -*- coding: utf-8 -*-

from odoo import fields, models


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    connect_stripe_payment_id = fields.Many2one(
        'connect.stripe.payment',
        string='Connect Phone Payment',
        ondelete='set null',
        index=True,
        help='Link back to the live-call phone-payment record this transaction was created for.',
    )

    def _stripe_prepare_payment_intent_payload(self):
        payload = super()._stripe_prepare_payment_intent_payload()
        if self.connect_stripe_payment_id:
            # The card was just collected over DTMF during a live agent call.
            # Stripe's correct classification for this is MOTO (customer-not-present
            # but keypad-collected) — not the off_session/MIT path used for stored
            # recurring tokens. Strip off_session and ask the network to accept MOTO.
            payload.pop('off_session', None)
            payload['payment_method_options[card][moto]'] = 'true'
        return payload
