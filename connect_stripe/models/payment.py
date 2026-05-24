# -*- coding: utf-8 -*-
"""Connect Stripe Payment — live-call DTMF card capture.

Architecture (tokenize-then-charge):
    1. Twilio's <Pay> verb captures the card via DTMF and tokenises it through
       the Stripe Pay Connector. Twilio returns a Stripe pm_xxx PaymentMethod
       and we never see the PAN — PCI scope stays out of Odoo.
    2. We attach that pm to a Stripe Customer for the partner (created on
       demand) and save a payment.token record so subsequent calls to the same
       partner can reuse it.
    3. We create a payment.transaction (operation='offline', token_id=token,
       linked invoice if any) and call _charge_with_token(). Odoo's pipeline
       handles the PaymentIntent, posts the account.payment, reconciles the
       invoice, and surfaces refunds/disputes via the standard flow.
    4. Our _stripe_prepare_payment_intent_payload override adds MOTO so the
       network correctly classifies the auth as keypad-captured rather than
       off-session MIT — keeps issuers happy in SCA regions.
"""

import logging
import uuid

from odoo import fields, models, api
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)


class ConnectStripePayment(models.Model):
    _name = 'connect.stripe.payment'
    _description = 'Connect Stripe Payment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(
        compute='_compute_name',
        store=True,
        help='Human-readable label derived from the masked card and the call.',
    )
    call_id = fields.Many2one(
        'connect.call',
        string='Call',
        required=True,
        ondelete='restrict',
        index=True,
        help='The live call during which this payment was initiated.',
    )
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('capturing', 'Capturing'),
            ('captured', 'Captured'),
            ('processing', 'Processing'),
            ('complete', 'Complete'),
            ('failed', 'Failed'),
            ('cancelled', 'Cancelled'),
        ],
        default='draft',
        required=True,
        tracking=True,
        help=(
            'Lifecycle: draft → capturing (DTMF in progress) → captured (Twilio '
            'returned a pm_xxx) → processing (Stripe charge in flight) → '
            'complete / failed / cancelled.'
        ),
    )
    amount = fields.Monetary(
        required=True,
        currency_field='currency_id',
        help='Amount to charge. Set by the agent before initiating the flow.',
    )
    currency_id = fields.Many2one(
        'res.currency',
        required=True,
        help='Currency for this payment. Defaults to the Stripe default currency in Connect settings.',
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        index=True,
        help='Customer making the payment. Used for the Stripe Customer and account.payment partner.',
    )
    invoice_id = fields.Many2one(
        'account.move',
        string='Invoice',
        domain=[('move_type', 'in', ['out_invoice', 'out_receipt'])],
        help='Optional invoice to reconcile after the payment is posted.',
    )
    masked_card = fields.Char(
        string='Card (Last 4)',
        readonly=True,
        help='Last 4 digits as reported by Twilio.',
    )
    card_type = fields.Char(
        string='Card Type',
        readonly=True,
        help='Card brand (visa, mastercard, amex…) reported by Twilio.',
    )
    stripe_payment_method_id = fields.Char(
        string='Stripe Payment Method',
        readonly=True,
        help='pm_xxx token returned by Twilio after the Stripe Pay Connector tokenises the card.',
    )
    stripe_payment_intent_id = fields.Char(
        string='Stripe PaymentIntent',
        readonly=True,
        help='pi_xxx of the PaymentIntent created by Odoo when charging the token.',
    )
    transaction_id = fields.Many2one(
        'payment.transaction',
        string='Payment Transaction',
        readonly=True,
        help='The payment.transaction Odoo created to charge the token.',
    )
    payment_id = fields.Many2one(
        related='transaction_id.payment_id',
        string='Odoo Payment',
        store=True,
        readonly=True,
        help='The account.payment record built by Odoo from the payment.transaction.',
    )
    token_id = fields.Many2one(
        'payment.token',
        string='Stripe Token',
        readonly=True,
        help='The payment.token (reusable across calls for the same partner).',
    )
    error_message = fields.Text(
        readonly=True,
        help='Error detail when the payment enters the failed state.',
    )
    twilio_call_sid = fields.Char(
        string='Twilio Call SID',
        readonly=True,
        index=True,
        help='Twilio CallSid of the leg that executed the <Pay> verb.',
    )
    twilio_session_id = fields.Char(
        string='Twilio Session ID',
        index=True,
        help='Opaque token passed to TwiML and echoed back in webhooks.',
    )

    # ----- defaults & create ------------------------------------------------ #

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('partner_id') and vals.get('call_id'):
                call = self.env['connect.call'].browse(vals['call_id'])
                if call.exists() and call.partner:
                    vals['partner_id'] = call.partner.id
        return super().create(vals_list)

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        settings = self.env['connect.settings'].sudo().search([], limit=1)
        if 'currency_id' in fields_list and not defaults.get('currency_id'):
            currency = (
                getattr(settings, 'stripe_default_currency_id', False)
                or self.env.company.currency_id
            )
            if currency:
                defaults['currency_id'] = currency.id
        return defaults

    @api.depends('masked_card', 'call_id')
    def _compute_name(self):
        for rec in self:
            card = rec.masked_card or 'pending'
            call_label = rec.call_id.name if rec.call_id else str(rec.call_id.id or '')
            rec.name = f'Payment {card} on {call_label}'

    # ----- settings / provider --------------------------------------------- #

    def _get_settings(self):
        return self.env['connect.settings'].sudo().search([], limit=1)

    def _get_stripe_provider(self):
        provider = self._get_settings()._get_odoo_stripe_provider()
        if not provider:
            raise UserError(
                'The Odoo Stripe payment provider must be installed and enabled '
                '(or in test mode) for Connect Stripe to charge cards. '
                'Configure it under Accounting → Configuration → Payment Providers.'
            )
        return provider

    # ----- Twilio redirect (capture) --------------------------------------- #

    def action_initiate(self):
        """Flip to capturing state and redirect the live call leg into <Pay>."""
        self.ensure_one()
        if not self.partner_id and self.call_id.partner:
            self.partner_id = self.call_id.partner
        if not self.twilio_session_id:
            self.twilio_session_id = uuid.uuid4().hex
        if not self.twilio_call_sid and self.call_id.call_sid:
            self.twilio_call_sid = self.call_id.call_sid
        self.state = 'capturing'
        self._redirect_call_to_pay()

    def _build_redirect_twiml(self, path):
        from twilio.twiml.voice_response import VoiceResponse  # noqa: PLC0415

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        response = VoiceResponse()
        response.redirect(f'{base_url}{path}?session_id={self.twilio_session_id}')
        return str(response)

    def _redirect_call_to_pay(self):
        client = self.env['connect.settings'].sudo().get_client()
        if not client:
            raise UserError('Twilio client is not configured. Check Connect settings.')
        call_sid = self.twilio_call_sid or self.call_id.call_sid
        if not call_sid:
            raise UserError('No Twilio call SID found on this call.')
        client.calls(call_sid).update(twiml=self._build_redirect_twiml('/connect/stripe/twiml'))

    def action_cancel(self):
        """Cancel an in-flight payment.

        Set the terminal state FIRST so a late payment-completed webhook can't
        race past us, then redirect the live Twilio leg to /resume.
        """
        self.ensure_one()
        if self.state in ('complete', 'failed', 'cancelled'):
            return
        prior_state = self.state
        self.write({'state': 'cancelled'})
        if prior_state in ('capturing', 'captured') and self.twilio_call_sid:
            try:
                client = self.env['connect.settings'].sudo().get_client()
                if client:
                    client.calls(self.twilio_call_sid).update(
                        twiml=self._build_redirect_twiml('/connect/stripe/resume')
                    )
            except Exception:
                logger.exception('action_cancel: failed to redirect Twilio call leg')

    # ----- Stripe charge (via payment.transaction) ------------------------- #

    def _process_payment(self):
        """Charge the captured Twilio pm_xxx via Odoo's payment.transaction pipeline.

        Builds (or reuses) a payment.token, creates a payment.transaction with
        operation='offline', and calls _charge_with_token(). Odoo's account_payment
        extension handles posting account.payment and reconciling the invoice.
        """
        self.ensure_one()
        if not self.stripe_payment_method_id:
            raise UserError('No Stripe PaymentMethod captured; cannot charge.')
        if not self.partner_id:
            raise UserError(
                'Cannot charge a phone payment without a customer. '
                'Set the call partner before initiating.'
            )

        self.state = 'processing'
        provider = self._get_stripe_provider()
        token = self._upsert_token(provider)
        self.token_id = token.id
        tx = self._create_transaction(provider, token)
        self.transaction_id = tx.id

        # Drive Odoo's pipeline. _charge_with_token → payment_stripe's
        # _send_payment_request → _stripe_create_intent → _apply_updates →
        # _set_done → account_payment._post_process → account.payment + reconcile.
        tx.sudo()._charge_with_token()
        tx.invalidate_recordset(['state', 'state_message', 'provider_reference', 'payment_id'])

        if tx.state == 'done':
            self.write({
                'state': 'complete',
                'stripe_payment_intent_id': tx.provider_reference,
            })
            self.call_id.message_post(
                body=(
                    f'Stripe payment complete: {self.currency_id.name} {self.amount:.2f} '
                    f'({self.card_type or "card"} ****{self.masked_card or "????"}) — '
                    f'PaymentIntent {tx.provider_reference or "n/a"}'
                )
            )
        elif tx.state in ('cancel', 'error'):
            self.write({
                'state': 'failed',
                'error_message': tx.state_message or 'Stripe declined the charge.',
            })
        else:
            # 'draft' or 'pending' — async (3DS, etc.). Leave at 'processing' and
            # log; Stripe webhook will eventually drive the tx to done/error.
            logger.info(
                'connect_stripe payment %s left in async tx state %s — '
                'awaiting Stripe webhook to finalise.',
                self.id, tx.state,
            )

    # ----- Token + transaction helpers ------------------------------------- #

    def _upsert_token(self, provider):
        """Find or create a payment.token for (partner, provider, stripe_pm).

        Twilio's Stripe Pay Connector returns a pm_xxx already attached to a
        Stripe Customer it created on the fly. We rebind the pm to a Customer
        scoped to this Odoo partner so subsequent charges through Odoo's
        pipeline find a stable customer/PM pair.
        """
        Token = self.env['payment.token'].sudo()
        existing = Token.search([
            ('provider_id', '=', provider.id),
            ('partner_id', '=', self.partner_id.id),
            ('stripe_payment_method', '=', self.stripe_payment_method_id),
            ('active', '=', True),
        ], limit=1)
        if existing:
            return existing

        customer_id = self._ensure_stripe_customer(provider)
        self._attach_pm_to_customer(provider, customer_id)

        card_method = self.env.ref('payment.payment_method_card', raise_if_not_found=False)
        return Token.create({
            'provider_id': provider.id,
            'payment_method_id': card_method.id if card_method else False,
            'payment_details': self.masked_card or '••••',
            'partner_id': self.partner_id.id,
            'provider_ref': customer_id,
            'stripe_payment_method': self.stripe_payment_method_id,
        })

    def _ensure_stripe_customer(self, provider):
        """Return a Stripe customer id for this payment's partner.

        Reuses any prior connect_stripe token's provider_ref; otherwise creates
        a new Stripe Customer via the provider's API.
        """
        Token = self.env['payment.token'].sudo()
        prior = Token.search([
            ('provider_id', '=', provider.id),
            ('partner_id', '=', self.partner_id.id),
            ('provider_ref', '!=', False),
        ], limit=1)
        if prior:
            return prior.provider_ref

        partner = self.partner_id
        response = provider._send_api_request('POST', 'customers', data={
            'name': partner.name or '',
            'email': partner.email or None,
            'phone': (partner.phone or partner.mobile or '')[:20] or None,
            'description': f'Odoo partner {partner.id} (Connect phone payment)',
        })
        return response['id']

    def _attach_pm_to_customer(self, provider, customer_id):
        try:
            provider._send_api_request(
                'POST',
                f'payment_methods/{self.stripe_payment_method_id}/attach',
                data={'customer': customer_id},
            )
        except Exception:
            # If already attached to this customer, Stripe returns 200 anyway;
            # only a different-customer attach raises. Log and move on — the
            # subsequent PaymentIntent will surface the real problem.
            logger.warning(
                'Could not attach pm %s to customer %s; continuing with charge.',
                self.stripe_payment_method_id, customer_id,
            )

    def _create_transaction(self, provider, token):
        Transaction = self.env['payment.transaction'].sudo()
        reference = f'CONNECT-{self.id}-{uuid.uuid4().hex[:8]}'
        vals = {
            'provider_id': provider.id,
            'payment_method_id': token.payment_method_id.id,
            'token_id': token.id,
            'reference': reference,
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'partner_id': self.partner_id.id,
            'operation': 'offline',
            'connect_stripe_payment_id': self.id,
        }
        if self.invoice_id:
            vals['invoice_ids'] = [(6, 0, [self.invoice_id.id])]
        return Transaction.create(vals)

    # ----- Refunds --------------------------------------------------------- #

    def action_refund(self, amount_to_refund=None):
        """Issue a Stripe refund for a complete payment.

        Delegates to payment.transaction.action_refund which creates a child
        refund transaction, calls Stripe's refund API, reverses the account.payment
        on success, and un-reconciles the invoice.
        """
        self.ensure_one()
        if self.state != 'complete' or not self.transaction_id:
            raise UserError('Only completed payments can be refunded.')
        return self.transaction_id.sudo().action_refund(amount_to_refund=amount_to_refund)

    # ----- JS polling ------------------------------------------------------- #

    def action_get_state(self):
        self.ensure_one()
        return {
            'state': self.state,
            'masked_card': self.masked_card,
            'card_type': self.card_type,
            'error_message': self.error_message,
        }
