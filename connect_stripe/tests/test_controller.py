# -*- coding: utf-8 -*-

import uuid
from unittest.mock import patch

from odoo.tests import HttpCase, tagged
from odoo.addons.connect_stripe.controllers.main import ConnectStripeController

from .common import StripeTestCase


@tagged('post_install', '-at_install')
class TestStripeControllerHttp(StripeTestCase, HttpCase):
    """End-to-end-ish: hit the live controller routes over HTTP."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Controller behavior is exercised independently of Twilio's HMAC. The
        # shared validator has dedicated fail-closed tests in connect.
        cls.startClassPatcher(patch.object(
            ConnectStripeController,
            'check_signature',
            return_value=True,
        ))

    def _new_payment(self, **kw):
        vals = {
            'call_id': self.call.id,
            'amount': 10.0,
            'twilio_session_id': uuid.uuid4().hex,
            'twilio_call_sid': self.call.call_sid,
            'state': 'capturing',
        }
        vals.update(kw)
        return self.env['connect.stripe.payment'].create(vals)

    # ----- /twiml --------------------------------------------------------- #

    def test_twiml_endpoint_returns_pay_verb(self):
        payment = self._new_payment()
        res = self.url_open(
            f'/connect/stripe/twiml?session_id={payment.twilio_session_id}'
        )
        self.assertEqual(res.status_code, 200)
        body = res.text
        self.assertIn('<Pay', body)
        self.assertIn('TestConnector', body)
        self.assertIn('action=', body)
        self.assertIn('/connect/stripe/resume', body)
        self.assertIn(payment.twilio_session_id, body)
        self.assertIn('payment-method', body)
        # Branded prompt before <Pay>.
        self.assertIn('<Say', body)
        self.assertIn('keypad', body.lower())
        # chargeAmount must NOT appear — token_type=payment-method alone is
        # how we signal "tokenise only" to the Stripe Pay Connector. Including
        # chargeAmount="0" risks Twilio treating it as a literal zero charge.
        self.assertNotIn('chargeAmount', body)
        # Stripe-bound metadata via <Parameter> children.
        self.assertIn('OdooPartnerId', body)
        self.assertIn(f'value="{self.partner_1.id}"', body)
        self.assertIn('CustomerPhone', body)
        self.assertIn(self.partner_1.phone, body)

    def test_twiml_endpoint_unknown_session_returns_error_twiml(self):
        res = self.url_open('/connect/stripe/twiml?session_id=does-not-exist')
        self.assertEqual(res.status_code, 200)
        self.assertIn('error', res.text.lower())

    def test_twiml_endpoint_wrong_state_returns_error(self):
        payment = self._new_payment(state='complete')
        res = self.url_open(
            f'/connect/stripe/twiml?session_id={payment.twilio_session_id}'
        )
        self.assertIn('error', res.text.lower())

    # ----- /pay_webhook --------------------------------------------------- #

    def test_pay_webhook_success_processes_payment_via_transaction(self):
        payment = self._new_payment()
        with self.mockStripeApi():
            res = self.url_open(
                f'/connect/stripe/pay_webhook?session_id={payment.twilio_session_id}',
                data={
                    'CallSid': self.call.call_sid,
                    'StatusCallbackType': 'payment-completed',
                    'Result': 'success',
                    'PaymentCardNumber': 'xxxxxxxxxxxx4242',
                    'PaymentCardType': 'visa',
                    'PaymentConfirmationCode': 'pm_test_token',
                },
            )
        self.assertEqual(res.status_code, 200)
        payment.invalidate_recordset()
        self.assertEqual(payment.state, 'complete')
        self.assertEqual(payment.masked_card, '4242')
        self.assertEqual(payment.card_type, 'visa')
        self.assertEqual(payment.stripe_payment_method_id, 'pm_test_token')
        self.assertTrue(payment.transaction_id)
        self.assertEqual(payment.transaction_id.state, 'done')

    def test_pay_webhook_intermediate_event_updates_card_only(self):
        payment = self._new_payment()
        res = self.url_open(
            f'/connect/stripe/pay_webhook?session_id={payment.twilio_session_id}',
            data={
                'CallSid': self.call.call_sid,
                'StatusCallbackType': 'card-number-collected',
                'PaymentCardNumber': 'xxxxxxxxxxxx4242',
                'PaymentCardType': 'visa',
            },
        )
        self.assertEqual(res.status_code, 200)
        payment.invalidate_recordset()
        self.assertEqual(payment.state, 'capturing')
        self.assertEqual(payment.masked_card, '4242')
        self.assertEqual(payment.card_type, 'visa')
        self.assertFalse(payment.stripe_payment_method_id)

    def test_pay_webhook_terminal_failure_marks_failed(self):
        payment = self._new_payment()
        res = self.url_open(
            f'/connect/stripe/pay_webhook?session_id={payment.twilio_session_id}',
            data={
                'CallSid': self.call.call_sid,
                'StatusCallbackType': 'payment-completed',
                'Result': 'failure',
                'ErrorType': 'invalid-card-number',
            },
        )
        self.assertEqual(res.status_code, 200)
        payment.invalidate_recordset()
        self.assertEqual(payment.state, 'failed')
        self.assertIn('invalid', (payment.error_message or '').lower())

    def test_pay_webhook_malformed_does_not_burn_record(self):
        """Twilio M3: a webhook missing StatusCallbackType must NOT mark
        the record failed. Old code's `else` branch did exactly that."""
        payment = self._new_payment()
        res = self.url_open(
            f'/connect/stripe/pay_webhook?session_id={payment.twilio_session_id}',
            data={'CallSid': self.call.call_sid},  # No StatusCallbackType, no Result.
        )
        self.assertEqual(res.status_code, 200)
        payment.invalidate_recordset()
        self.assertEqual(payment.state, 'capturing')
        self.assertFalse(payment.error_message)

    def test_pay_webhook_unknown_session_returns_empty(self):
        res = self.url_open(
            '/connect/stripe/pay_webhook?session_id=nope',
            data={'CallSid': 'CAxxxx', 'Result': 'success'},
        )
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('Hangup', res.text)

    # ----- /resume -------------------------------------------------------- #

    def test_resume_returns_conference_dial_when_known(self):
        self.call.write({'conference_name': 'conf-test-1'})
        payment = self._new_payment(state='complete', masked_card='4242')
        res = self.url_open(
            f'/connect/stripe/resume?session_id={payment.twilio_session_id}'
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn('<Dial>', res.text)
        self.assertIn('conf-test-1', res.text)
        # Last-4 confirmation in the customer's TwiML.
        self.assertIn('4242', res.text)
        self.assertIn('successful', res.text.lower())

    def test_resume_hangs_up_when_no_conference(self):
        payment = self._new_payment(state='complete')
        res = self.url_open(
            f'/connect/stripe/resume?session_id={payment.twilio_session_id}'
        )
        self.assertIn('<Hangup', res.text)

    def test_resume_failure_message_on_failed_payment(self):
        payment = self._new_payment(state='failed', error_message='card declined')
        res = self.url_open(
            f'/connect/stripe/resume?session_id={payment.twilio_session_id}'
        )
        self.assertIn('unable', res.text.lower())

    def test_resume_marks_payment_failed_on_terminal_result(self):
        """Twilio H2: /resume must own the call-leg routing AND defensively
        mark the payment terminal if Twilio reports a non-success Result
        before the status_callback lands."""
        payment = self._new_payment()  # state=capturing
        res = self.url_open(
            f'/connect/stripe/resume?session_id={payment.twilio_session_id}',
            data={'Result': 'max-failed-attempts'},
        )
        self.assertEqual(res.status_code, 200)
        payment.invalidate_recordset()
        self.assertEqual(payment.state, 'failed')

    def test_resume_marks_cancelled_on_caller_hung_up(self):
        payment = self._new_payment()
        res = self.url_open(
            f'/connect/stripe/resume?session_id={payment.twilio_session_id}',
            data={'Result': 'caller-hung-up'},
        )
        self.assertEqual(res.status_code, 200)
        payment.invalidate_recordset()
        self.assertEqual(payment.state, 'cancelled')

    # ----- Reject status code -------------------------------------------- #

    def test_reject_returns_200_not_403(self):
        """Twilio L4: invalid-signature reject must be 200 (Twilio treats
        4xx as transport error and retries)."""
        with patch.object(
            ConnectStripeController,
            'check_signature',
            return_value=False,
        ):
            res = self.url_open(
                '/connect/stripe/pay_webhook?session_id=any',
                data={'CallSid': 'CAxxx'},
            )
            self.assertEqual(res.status_code, 200)
            self.assertIn('<Hangup', res.text)
