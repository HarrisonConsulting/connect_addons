# -*- coding: utf-8 -*-
"""Twilio webhook handlers for Connect Stripe phone payments.

Three endpoints:
    /connect/stripe/twiml         — TwiML the live call is redirected to;
                                    returns <Pay>.
    /connect/stripe/pay_webhook   — Twilio's status_callback during/after Pay.
    /connect/stripe/resume        — Twilio's action= target after Pay finishes;
                                    drives the customer back into the call.

Signature verification: Twilio signs the full URL (query string included) and
the POST body params, sorted alphabetically. The query-string `session_id`
must NOT be added to the params dict — it would be double-counted.
Reference: https://www.twilio.com/docs/usage/webhooks/webhooks-security
"""

import logging

from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse, Pay, Dial

from odoo.http import Controller, route, request, Response
from odoo.addons.connect.models.settings import get_env_credential

logger = logging.getLogger(__name__)

# Per Twilio's recommendation, every "reject" response should be a 200 with
# TwiML body — returning 4xx makes Twilio treat the failure as a transport
# error and retry, which is exactly what we don't want for a deliberate reject.
_REJECT_TWIML = '<Response><Hangup/></Response>'
_ERROR_TWIML = '<Response><Say>An error occurred.</Say><Hangup/></Response>'
_EMPTY_TWIML = '<Response/>'


class ConnectStripeController(Controller):

    # ----- helpers -------------------------------------------------------- #

    @staticmethod
    def _reject():
        return Response(_REJECT_TWIML, status=200, content_type='text/xml')

    @staticmethod
    def check_signature():
        """Verify Twilio request signature.

        Twilio's algorithm: HMAC-SHA1(auth_token, full_url + concat(sorted(POST body))).
        The full URL already contains the query string — so we pass only the POST
        body (request.params strips routing args automatically).
        """
        settings = request.env['connect.settings'].sudo()
        if not settings.get_param('twilio_verify_requests'):
            if get_env_credential('account_sid') is not None:
                logger.critical('SECURITY: Twilio webhook signature verification is DISABLED')
                return True
            logger.critical(
                'SECURITY: twilio_verify_requests is disabled but no CONNECT_* '
                'sandbox override is active (production context); refusing to '
                'skip Twilio webhook signature verification.'
            )
        auth_token = settings.get_param('auth_token')
        validator = RequestValidator(auth_token)
        url = request.httprequest.url.replace('http:', 'https:')
        signature = request.httprequest.headers.get('X-Twilio-Signature', '')
        # POST form body only — Twilio docs are explicit that query-string params
        # must not be in the params dict (they're already in the URL).
        body = dict(request.httprequest.form) if request.httprequest.method == 'POST' else {}
        if not validator.validate(url, body, signature):
            if request.httprequest.url.startswith('http:'):
                logger.error('Twilio requires HTTPS for signature verification.')
            else:
                logger.error('Twilio signature invalid for %s', request.httprequest.path)
            return False
        return True

    def _find_payment(self, session_id=None, call_sid=None, states=None):
        env = request.env
        try:
            domain = []
            if session_id:
                domain.append(('twilio_session_id', '=', session_id))
            if call_sid:
                domain.append(('twilio_call_sid', '=', call_sid))
            if states:
                domain.append(('state', 'in', tuple(states)))
            if not domain:
                return env['connect.stripe.payment'].browse()
            return env['connect.stripe.payment'].with_user(
                env.ref('connect.user_connect_webhook')
            ).search(domain, limit=1)
        except Exception:
            logger.exception(
                'connect_stripe: payment lookup failed (session_id=%s, call_sid=%s)',
                session_id, call_sid,
            )
            return env['connect.stripe.payment'].browse()

    # ----- /connect/stripe/twiml ----------------------------------------- #

    @route('/connect/stripe/twiml', methods=['GET', 'POST'], type='http', auth='public', csrf=False)
    def stripe_twiml(self, session_id=None, **kw):
        if not self.check_signature():
            return self._reject()

        payment = self._find_payment(session_id=session_id)
        if not payment:
            logger.warning('stripe_twiml: no payment for session_id=%s', session_id)
            return Response(_ERROR_TWIML, content_type='text/xml')

        if payment.state not in ('draft', 'capturing'):
            logger.warning('stripe_twiml: payment %s in state %s (not draft/capturing)',
                           payment.id, payment.state)
            return Response(_ERROR_TWIML, content_type='text/xml')

        try:
            settings = request.env['connect.settings'].sudo().search([], limit=1)
            base_url = request.httprequest.host_url.rstrip('/')
            connector = settings.stripe_pay_connector_name or 'StripeConnector'
            currency_code = (
                payment.currency_id.name.lower()
                if payment.currency_id else 'usd'
            )

            response = VoiceResponse()
            # Brief friendly prompt before the connector takes over (Twilio's
            # own prompts are robotic). Keeps the customer oriented.
            response.say(
                'Please have your card ready. '
                'Enter your card number on your keypad when prompted, '
                'then the expiration date, then the security code.',
                voice='Polly.Joanna',
            )
            # token_type='payment-method' alone signals "tokenise only, return
            # a Stripe pm_xxx" — no charge_amount needed (and including
            # charge_amount='0' would risk Twilio interpreting it as a literal
            # zero charge, which Stripe rejects below the $0.50 minimum).
            pay = Pay(
                payment_connector=connector,
                token_type='payment-method',
                currency=currency_code,
                language='en-US',
                timeout='5',
                max_attempts='3',
                description=f'Phone payment ref Connect call {payment.call_id.id}',
                action=f'{base_url}/connect/stripe/resume?session_id={payment.twilio_session_id}',
                status_callback=f'{base_url}/connect/stripe/pay_webhook?session_id={payment.twilio_session_id}',
                status_callback_method='POST',
                postal_code='false',
                security_code='true',
                valid_card_types='visa mastercard amex discover',
            )
            # Metadata Twilio forwards to Stripe (attached to the resulting
            # PaymentMethod). Lets you trace any Stripe charge back to the
            # Odoo partner without going through our DB, and gives Stripe's
            # fraud tools a phone-number signal.
            # sudo() because the webhook user lacks res.partner read.
            partner = payment.partner_id.sudo() if payment.partner_id else False
            if partner:
                pay.parameter(name='OdooPartnerId', value=str(partner.id))
                customer_phone = partner.phone or partner.mobile or ''
                if customer_phone:
                    pay.parameter(name='CustomerPhone', value=customer_phone)
            response.append(pay)
            payment.state = 'capturing'
            return Response(str(response), content_type='text/xml')
        except Exception:
            logger.exception('stripe_twiml: failed to build TwiML for session_id=%s', session_id)
            return Response(_ERROR_TWIML, content_type='text/xml')

    # ----- /connect/stripe/pay_webhook ------------------------------------ #

    @route('/connect/stripe/pay_webhook', methods=['POST'], type='http', auth='public', csrf=False)
    def stripe_pay_webhook(self, session_id=None, **kw):
        if not self.check_signature():
            return self._reject()

        call_sid = kw.get('CallSid')
        payment = self._find_payment(
            session_id=session_id,
            call_sid=None if session_id else call_sid,
            states=('capturing', 'captured'),
        )
        if not payment:
            logger.warning(
                'stripe_pay_webhook: no payment for session_id=%s call_sid=%s',
                session_id, call_sid,
            )
            return Response(_EMPTY_TWIML, content_type='text/xml')

        try:
            # PaymentCardNumber: masked PAN e.g. "xxxxxxxxxxxx4242"
            masked_pan = kw.get('PaymentCardNumber') or ''
            last4 = masked_pan[-4:] if len(masked_pan) >= 4 else masked_pan
            card_type = (kw.get('PaymentCardType') or '').lower()

            vals = {}
            if last4 and last4 != payment.masked_card:
                vals['masked_card'] = last4
            if card_type and card_type != payment.card_type:
                vals['card_type'] = card_type

            status_type = kw.get('StatusCallbackType') or ''
            result = kw.get('Result')

            # Terminal handling switches on Result, not on the absence of a
            # StatusCallbackType. This avoids burning the record on malformed
            # retries that arrive without StatusCallbackType.
            if status_type == 'payment-completed':
                if result == 'success':
                    vals['stripe_payment_method_id'] = kw.get('PaymentConfirmationCode')
                    vals['state'] = 'captured'
                    if vals:
                        payment.write(vals)
                    try:
                        # System action (needs cross-model read); sudo it.
                        payment.sudo()._process_payment()
                    except Exception as e:
                        logger.exception(
                            'stripe_pay_webhook: _process_payment failed for payment %s',
                            payment.id,
                        )
                        payment.write({'state': 'failed', 'error_message': str(e)})
                else:
                    # Terminal failure reported via status_callback.
                    vals['state'] = 'failed'
                    vals['error_message'] = (
                        kw.get('ErrorType') or kw.get('PaymentError') or 'Payment failed'
                    )
                    payment.write(vals)
            else:
                # Intermediate progress event (card-number-collected,
                # payment-card-expiration-date-collected, etc). Just persist any
                # new card info we learned. Don't write state.
                if vals:
                    payment.write(vals)
        except Exception:
            logger.exception('stripe_pay_webhook: processing failed for CallSid=%s', call_sid)

        return Response(_EMPTY_TWIML, content_type='text/xml')

    # ----- /connect/stripe/resume ---------------------------------------- #

    # Twilio's documented Result values on the action= callback.
    _RESULT_SUCCESS = 'success'
    _RESULT_TERMINAL = frozenset({
        'validation-error',
        'payment-connector-error',
        'caller-interrupted-with-star',
        'caller-hung-up',
        'max-failed-attempts',
        'too-many-failed-attempts',
        'internal-error',
    })

    @route('/connect/stripe/resume', methods=['GET', 'POST'], type='http', auth='public', csrf=False)
    def stripe_resume(self, session_id=None, **kw):
        """Target of <Pay action=…>.

        Twilio is allowed to hit this before the final status_callback lands,
        so we treat /resume as authoritative for the call-leg routing decision
        while letting /pay_webhook own the payment record state. We do, however,
        defensively mark the payment failed/cancelled here if it's still in
        flight and Twilio reports a terminal non-success Result.
        """
        if not self.check_signature():
            return self._reject()

        payment = self._find_payment(session_id=session_id)
        result = (kw.get('Result') or '').strip()

        # If the action= callback arrives before pay_webhook with a terminal
        # non-success outcome, mark the payment so the agent UI reflects it.
        if payment and payment.state in ('capturing', 'captured'):
            if result in self._RESULT_TERMINAL:
                payment.sudo().write({
                    'state': 'failed' if result not in ('caller-hung-up',) else 'cancelled',
                    'error_message': result,
                })

        response = VoiceResponse()

        # Customer-facing message, calibrated to outcome.
        if payment and payment.state == 'complete':
            if payment.masked_card:
                response.say(
                    f'Thank you. Your payment on the card ending {payment.masked_card} '
                    f'was successful. Returning you to the agent.',
                    voice='Polly.Joanna',
                )
            else:
                response.say('Thank you. Your payment was successful. Returning you to the agent.',
                             voice='Polly.Joanna')
        elif payment and payment.state == 'failed':
            response.say(
                'We were unable to process your payment. Returning you to the agent.',
                voice='Polly.Joanna',
            )
        elif payment and payment.state == 'cancelled':
            response.say('Payment cancelled. Returning you to the agent.', voice='Polly.Joanna')
        else:
            response.say('Returning you to the agent.', voice='Polly.Joanna')

        # Try to rejoin the original conference, else fall back gracefully.
        conference_name = (
            payment.call_id.conference_name
            if payment and payment.call_id and payment.call_id.conference_name
            else None
        )
        if conference_name:
            dial = Dial()
            # If the agent dropped, end the empty conference quickly rather
            # than holding the customer in silence; start_conference_on_enter
            # keeps the room idle until both parties present.
            dial.conference(
                conference_name,
                start_conference_on_enter='false',
                end_conference_on_exit='false',
                wait_url='',
                max_participants='2',
            )
            response.append(dial)
        else:
            response.hangup()

        return Response(str(response), content_type='text/xml')
