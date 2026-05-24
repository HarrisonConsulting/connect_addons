# -*- coding: utf-8 -*-

from unittest.mock import patch

from odoo.exceptions import UserError

from .common import StripeTestCase


class TestStripePaymentDefaults(StripeTestCase):

    def test_default_currency_from_settings(self):
        payment = self.env['connect.stripe.payment'].new({'call_id': self.call.id})
        self.assertEqual(payment.currency_id, self.settings.stripe_default_currency_id)

    def test_create_auto_fills_partner_from_call(self):
        payment = self.env['connect.stripe.payment'].create({
            'call_id': self.call.id,
            'amount': 10.0,
        })
        self.assertEqual(payment.partner_id, self.partner_1)

    def test_create_respects_explicit_partner(self):
        other = self.env['res.partner'].create({'name': 'Override'})
        payment = self.env['connect.stripe.payment'].create({
            'call_id': self.call.id,
            'amount': 10.0,
            'partner_id': other.id,
        })
        self.assertEqual(payment.partner_id, other)


class TestStripePaymentInitiate(StripeTestCase):

    def test_action_initiate_sets_session_and_redirects_call(self):
        with self.mockTwilioClient() as twilio:
            payment = self.env['connect.stripe.payment'].create({
                'call_id': self.call.id,
                'amount': 25.0,
            })
            payment.action_initiate()
            self.assertEqual(payment.state, 'capturing')
            self.assertTrue(payment.twilio_session_id)
            twiml = twilio.calls(self.call.call_sid)._data.get('twiml', '')
            self.assertIn('/connect/stripe/twiml', twiml)
            self.assertIn(payment.twilio_session_id, twiml)


class TestStripePaymentProcess(StripeTestCase):

    def _new_captured_payment(self, **overrides):
        vals = {
            'call_id': self.call.id,
            'amount': 42.0,
            'twilio_session_id': 'sess_test',
            'twilio_call_sid': self.call.call_sid,
            'masked_card': '4242',
            'card_type': 'visa',
            'stripe_payment_method_id': 'pm_test_xyz',
            'state': 'captured',
        }
        vals.update(overrides)
        return self.env['connect.stripe.payment'].create(vals)

    def test_process_payment_success_creates_transaction(self):
        payment = self._new_captured_payment()
        with self.mockStripeApi():
            payment._process_payment()

        self.assertEqual(payment.state, 'complete')
        self.assertTrue(payment.transaction_id)
        self.assertEqual(payment.transaction_id.state, 'done')
        self.assertEqual(payment.transaction_id.provider_id, self.provider)
        self.assertEqual(payment.transaction_id.connect_stripe_payment_id, payment)
        self.assertTrue(payment.transaction_id.token_id)
        self.assertTrue(payment.stripe_payment_intent_id.startswith('pi_test_'))

    def test_process_payment_creates_payment_token(self):
        payment = self._new_captured_payment()
        with self.mockStripeApi():
            payment._process_payment()
        token = payment.token_id
        self.assertTrue(token)
        self.assertEqual(token.partner_id, self.partner_1)
        self.assertEqual(token.provider_id, self.provider)
        self.assertEqual(token.stripe_payment_method, 'pm_test_xyz')
        self.assertTrue(token.provider_ref.startswith('cus_test_'))

    def test_process_payment_token_reuse(self):
        # First payment creates the token.
        first = self._new_captured_payment()
        with self.mockStripeApi():
            first._process_payment()
        token_id = first.token_id.id

        # Second payment for same partner+pm reuses the token (no new customer call).
        second = self._new_captured_payment()
        with self.mockStripeApi() as recorder:
            second._process_payment()
        self.assertEqual(second.token_id.id, token_id)
        customer_calls = [c for c in recorder.calls if c['endpoint'] == 'customers']
        self.assertFalse(customer_calls, 'Should reuse the existing Stripe customer.')

    def test_process_payment_failure_marks_failed(self):
        payment = self._new_captured_payment(stripe_payment_method_id='pm_test_decline')
        with self.mockStripeApi() as recorder:
            recorder._make_payment_intent_orig = recorder._make_payment_intent

            def _failing_intent(method, data, **_):
                resp = recorder._make_payment_intent_orig(method, data)
                resp['status'] = 'requires_payment_method'
                resp['last_payment_error'] = {'message': 'Your card was declined.'}
                return resp

            recorder._make_payment_intent = _failing_intent
            payment._process_payment()

        self.assertEqual(payment.state, 'failed')
        self.assertIn('declined', (payment.error_message or '').lower())

    def test_process_payment_requires_partner(self):
        payment = self._new_captured_payment()
        payment.partner_id = False
        with self.mockStripeApi():
            with self.assertRaises(UserError):
                payment._process_payment()

    def test_moto_payload_strips_off_session_and_sets_moto(self):
        payment = self._new_captured_payment()
        Token = self.env['payment.token'].sudo()
        card_method = self.env.ref('payment.payment_method_card')
        token = Token.create({
            'provider_id': self.provider.id,
            'payment_method_id': card_method.id,
            'partner_id': self.partner_1.id,
            'payment_details': '4242',
            'provider_ref': 'cus_test_x',
            'stripe_payment_method': 'pm_test_xyz',
        })
        tx = self.env['payment.transaction'].sudo().create({
            'provider_id': self.provider.id,
            'payment_method_id': card_method.id,
            'token_id': token.id,
            'reference': 'CONNECT-MOTO-TEST',
            'amount': 10.0,
            'currency_id': self.env.company.currency_id.id,
            'partner_id': self.partner_1.id,
            'operation': 'offline',
            'connect_stripe_payment_id': payment.id,
        })
        payload = tx._stripe_prepare_payment_intent_payload()
        self.assertEqual(payload.get('payment_method_options[card][moto]'), 'true')
        self.assertNotIn('off_session', payload)


class TestStripePaymentCancel(StripeTestCase):

    def test_action_cancel_sets_state_before_redirect(self):
        """State must flip to cancelled BEFORE the Twilio redirect so a late
        payment-completed webhook can't race past us."""
        with self.mockTwilioClient() as twilio:
            payment = self.env['connect.stripe.payment'].create({
                'call_id': self.call.id,
                'amount': 10.0,
            })
            payment.action_initiate()
            payment.action_cancel()
            self.assertEqual(payment.state, 'cancelled')
            twiml = twilio.calls(self.call.call_sid)._data.get('twiml', '')
            self.assertIn('/connect/stripe/resume', twiml)

    def test_action_cancel_on_terminal_state_is_noop(self):
        payment = self.env['connect.stripe.payment'].create({
            'call_id': self.call.id,
            'amount': 10.0,
            'state': 'complete',
        })
        payment.action_cancel()
        self.assertEqual(payment.state, 'complete')


class TestStripePaymentRefund(StripeTestCase):

    def test_action_refund_requires_complete_state(self):
        payment = self.env['connect.stripe.payment'].create({
            'call_id': self.call.id,
            'amount': 10.0,
            'state': 'draft',
        })
        with self.assertRaises(UserError):
            payment.action_refund()

    def test_action_refund_delegates_to_transaction(self):
        payment = self.env['connect.stripe.payment'].create({
            'call_id': self.call.id,
            'amount': 30.0,
            'twilio_session_id': 'sess_refund',
            'twilio_call_sid': self.call.call_sid,
            'masked_card': '4242',
            'card_type': 'visa',
            'stripe_payment_method_id': 'pm_test_refund',
            'state': 'captured',
        })
        with self.mockStripeApi():
            payment._process_payment()
        self.assertEqual(payment.state, 'complete')

        with self.mockStripeApi():
            with patch.object(
                self.env['payment.transaction'].__class__,
                'action_refund',
                return_value={'name': 'mocked-refund'},
            ) as mock_refund:
                payment.action_refund(amount_to_refund=10.0)
                mock_refund.assert_called_once_with(amount_to_refund=10.0)


class TestStripePaymentMisc(StripeTestCase):

    def test_action_get_state_snapshot(self):
        payment = self.env['connect.stripe.payment'].create({
            'call_id': self.call.id,
            'amount': 10.0,
            'masked_card': '1111',
            'card_type': 'visa',
            'state': 'capturing',
        })
        snap = payment.action_get_state()
        self.assertEqual(snap['state'], 'capturing')
        self.assertEqual(snap['masked_card'], '1111')

    def test_call_stripe_payment_count(self):
        for _ in range(3):
            self.env['connect.stripe.payment'].create({
                'call_id': self.call.id,
                'amount': 1.0,
            })
        self.call.invalidate_recordset(['stripe_payment_count'])
        self.assertEqual(self.call.stripe_payment_count, 3)
