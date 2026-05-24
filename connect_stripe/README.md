# Connect Stripe Payments

Take credit-card payments during a live Twilio phone call. The customer enters their card on the keypad (DTMF); the agent never sees the digits. PCI scope stays out of Odoo entirely — Twilio's Stripe Pay Connector tokenises the card, then Odoo charges through the standard `payment.transaction` pipeline.

## How it works

```
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│  Agent clicks   │       │   Twilio <Pay>  │       │ Stripe          │
│  Take Payment   │──────▶│   DTMF capture  │──────▶│ PaymentMethod   │
│                 │       │                 │       │ (pm_xxx)        │
└─────────────────┘       └─────────────────┘       └─────────────────┘
                                                              │
                                                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  payment.transaction (operation='offline', token, invoice)         │
│      ↓ _charge_with_token  →  PaymentIntent  →  succeeded          │
│      ↓ _post_process       →  account.payment  →  invoice reconcile│
└─────────────────────────────────────────────────────────────────────┘
```

Key design choices:

1. **Tokenize-then-charge, not charge-via-connector.** Twilio's `<Pay>` is used in `charge_amount=0, token_type=payment-method` mode — capture only, no Stripe charge yet. We then create a `payment.transaction` and let Odoo charge it. This keeps Odoo as the system of record: account.payment, refunds, disputes, statement reconciliation, multi-currency, token reuse all go through Odoo's existing plumbing.
2. **MOTO classification.** When charging the just-captured card, our `_stripe_prepare_payment_intent_payload` override sets `payment_method_options[card][moto]=true` and removes `off_session=True` — issuers correctly classify the auth as keypad-collected rather than as a stored-credential MIT, which keeps SCA regions happy.
3. **`payment.token` reuse.** The first call captures the card. Subsequent calls from the same partner reuse the saved token (no second DTMF capture needed).

## Setup

1. Install the **Odoo Stripe payment provider** under *Accounting → Configuration → Payment Providers*. Set state to **Test Mode** or **Enabled**. Configure API keys and the receivable journal.
2. In the Twilio Console, create a **Stripe Pay Connector** under *Account → Pay Connectors*.
3. In *Connect → Settings → Stripe*, set the **Pay Connector Name** to match step 2 and pick a **Default Currency**.
4. Take a test call. Click the 💳 button on the active-call row. Enter an amount and click **Charge**. The customer hears a prompt and enters the card on their keypad. After Stripe confirms, the customer is returned to the call (conference rejoin) or hangs up cleanly.

## Lifecycle: `in_payment` vs `paid`

Posted Odoo payments don't always land in `paid` immediately:

| Edition / config | After a successful Stripe charge, invoice goes to… | Reaches `paid` when… |
|---|---|---|
| Community (no `account_accountant`) | `paid` | — already there. |
| Enterprise (`account_accountant` installed) | `in_payment` | the Stripe payout hits the bank journal and is reconciled against a bank-statement line. |

`in_payment` is **not a bug**. It correctly reflects "Stripe has the money, but the funds haven't been deposited and reconciled with your bank statement yet." It will flip to `paid` automatically when the Stripe payout (typically 1–2 business days later) is imported and matched.

For automated payout reconciliation and Stripe-fees handling, see **[`connect_stripe_enterprise`](../../connect_addons_ee/connect_stripe_enterprise/)**, which adds:

- Auto-created "Stripe Fees" `account.reconcile.model` so the ~2.9% + 30¢ fee deducted at payout time reconciles cleanly to your fees expense account.
- Defensive backfill of the Stripe `payment.method.line.outstanding_account_id` so the `in_payment → paid` flip works end-to-end.
- Roadmap: cron-based Stripe payout sync, phone-payments dashboard.

## Refunds

Click **Refund** on a completed phone payment. Delegates to `payment.transaction.action_refund`, which calls Stripe's refund API, creates a reversing `account.payment`, and un-reconciles the invoice.

## Security

- The card PAN/CVC never reaches Odoo. Twilio's Stripe Pay Connector tokenises in-DTMF.
- Webhook signature verification (`twilio_verify_requests` setting) is enforced when enabled. The signing algorithm signs the full request URL (query string included) plus POST form body params — our controller follows that contract exactly.
- The `/twiml`, `/pay_webhook`, and `/resume` endpoints are authenticated by Twilio signature; on signature failure we return `200 <Hangup/>` (not `403`) so Twilio doesn't treat the reject as a transport error and retry.

## Limitations

- US/CA card flow assumed for the demo (USD currency, US card brands). Multi-currency is supported (Odoo handles zero/three-decimal correctly via `to_minor_currency_units`).
- SCA/3DS: a first-time card captured via MOTO will succeed off-session in most regions; SCA-required regions may need additional handling.
