# CLAUDE.md - connect_stripe

**Module**: `connect_stripe`
**Version**: 19.0.2.0.0
**License**: OPL-1
**Repository**: /workspace/HarrisonConsulting/connect_addons/connect_stripe/
**Product Family**: /mnt/gdo/docs/product/odoo/communication/CLAUDE.md

---

## Purpose

PCI-compliant DTMF phone payments over Twilio + Stripe. Customer enters card on keypad during a live call; agent never sees digits. Tokenize-then-charge architecture — Twilio captures, Odoo's `payment.transaction` pipeline does the charge.

## Architecture

Agent clicks Take Payment in the Active Calls popup → OWL dialog → backend `action_initiate` → Twilio `client.calls(sid).update(twiml=<Redirect>)` → live leg fetches `/connect/stripe/twiml` → returns `<Pay>` → DTMF capture → Twilio's Stripe Pay Connector tokenises → status_callback to `/pay_webhook` with `pm_xxx` → `connect.stripe.payment._process_payment` → creates `payment.token` + `payment.transaction(operation='offline')` → `_charge_with_token` → Odoo posts `account.payment` and reconciles invoice → action= redirects customer to `/resume` → rejoin conference or hangup.

The `_stripe_prepare_payment_intent_payload` override on `payment.transaction` swaps `off_session=True` for `payment_method_options[card][moto]=true` whenever the transaction was created by us — issuers correctly classify the auth as keypad-collected.

## Key Models

| Model | Description | Key Fields |
|-------|-------------|------------|
| `connect.stripe.payment` | Per-call payment record; orchestrates capture + charge | `state`, `call_id`, `partner_id`, `amount`, `stripe_payment_method_id`, `transaction_id`, `token_id`, `payment_id` (related), `twilio_call_sid`, `twilio_session_id` |
| `payment.transaction` (inherit) | Adds back-link to connect.stripe.payment; overrides intent payload for MOTO | `connect_stripe_payment_id` |
| `connect.settings` (inherit) | Stripe Pay Connector name + default currency | `stripe_pay_connector_name`, `stripe_default_currency_id`, `stripe_provider_active` (computed) |
| `connect.call` (inherit) | Stat-button + count of phone payments on the call | `stripe_payment_ids`, `stripe_payment_count` |

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST | `/connect/stripe/twiml` | Twilio fetches this for the live leg; returns `<Say>` + `<Pay>` |
| POST | `/connect/stripe/pay_webhook` | Twilio status_callback; intermediate events update card info, `payment-completed/Result=success` triggers the Stripe charge |
| GET/POST | `/connect/stripe/resume` | Twilio `action=` target after Pay; rejoins conference (if `call.conference_name` set) or hangs up; defensively marks payment failed/cancelled on terminal non-success `Result` |

All three endpoints verify the `X-Twilio-Signature` when `twilio_verify_requests` is enabled. Signature covers the full URL (query string included) plus POST form body — query-string `session_id` is NOT added to the params dict (Twilio double-counts otherwise).

## Security Groups

Uses the connect base groups:

| Group | XML ID | Access |
|-------|--------|--------|
| Connect User | `connect.group_connect_user` | read on `connect.stripe.payment` |
| Connect Admin | `connect.group_connect_admin` | full CRUD |
| Connect Webhook | `connect.group_connect_webhook` | read/write/create (no delete) for inbound Twilio callbacks; `_process_payment` is called via `.sudo()` because the webhook user lacks res.partner read |

## JavaScript Components

- `static/src/components/payment_dialog/payment_dialog.{js,xml}` — OWL dialog with phases: `setup → capturing → processing → complete | failed | cancelled`. Polls `action_get_state` every 2s. 5-minute max-duration timeout; mid-capture cancel button; surfaces partial card info as intermediate Twilio callbacks arrive.
- `static/src/active_calls/active_calls_stripe_patch.{js,xml}` — patches the Active Calls popup table to add a Pay button column.

## Dependencies

| Module | Why |
|--------|-----|
| `connect` | Telephony platform — provides `connect.call`, `connect.settings`, Twilio client, `connect.user_connect_webhook`, the active-calls popup we patch |
| `account_payment` | Bridges `payment.transaction` to `account.payment` so charges post and reconcile correctly |
| `payment_stripe` | Provides the Stripe payment.provider, `_stripe_create_intent`, `_stripe_prepare_payment_intent_payload` (which we override), `payment.token.stripe_payment_method` |
| `twilio` (Python) | Comes with `connect`; we use `RequestValidator`, `VoiceResponse`, `Pay`, `Dial` |

## Development

```bash
# Run module tests
gdo test 005 -i connect_stripe --simple-output

# Open shell
gdo shell -d 005
>>> env['connect.stripe.payment'].search([], limit=5).read(['name', 'state', 'amount'])
```

## Testing pattern

Tests live in `tests/` and use `StripeTestCase` from `tests/common.py`, which:
- Spins up a Stripe payment.provider in test state.
- Provides `mockTwilioClient()` — record-and-replay Twilio mock that persists `client.calls(sid).update(...)` across lookups.
- Provides `mockStripeApi()` — patches `_send_api_request` on both `payment.provider` and `payment.transaction` so the whole Stripe HTTP layer is intercepted in any thread (works under `HttpCase` too).

## Anti-patterns to avoid

- Do **not** call `stripe.PaymentIntent.create` directly anymore — the entire Stripe interaction goes through `payment.transaction._charge_with_token`. The Stripe SDK is not a dependency of this module.
- Do **not** hand-create `account.payment` — `_post_process` does it. Hand-rolling drops the `payment.method.line` mapping and the `outstanding_account_id`, which breaks the `in_payment → paid` lifecycle in enterprise installs.
- Do **not** add `session_id` to the params dict when verifying Twilio signatures — the full URL already includes it; double-counting causes signature rejection.

## References

- Product catalog: /mnt/gdo/docs/product/odoo/PRODUCT-CATALOG.md
- Repo CLAUDE.md: @/ws/005/HarrisonConsulting/connect_addons/CLAUDE.md
- Enterprise extension: /ws/005/HarrisonConsulting/connect_addons_ee/connect_stripe_enterprise/
- Twilio Pay docs: https://www.twilio.com/docs/voice/twiml/pay
- Odoo payment_stripe source: /mnt/19/odoo/addons/payment_stripe/
