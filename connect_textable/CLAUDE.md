# connect_textable

SMS carrier for Connect through ElectSys's white-label Textable tenant
(spec 5.29.16@85ffb089, sha256 in `tools/textable_client.py`). Depends on
`connect` only. It sends with `POST /api/send` and receives inbound
messages and outbound echoes on the unsigned per-user Relay webhook.

## Decisions it implements

- **D1 wire hygiene.** The body is exactly `{to, from, message}`;
  `sendStatus`, `type`, `sendAt`, `reminder` and `media` are refused
  (`build_send_body`). `requests` only, a fresh Session with
  `trust_env=False`, timeout (3, 10), no redirects, and the response body is
  never read.
- **D1 / F2 honest states.** accepted shows as "sent, delivery
  unconfirmed". ambiguous (read timeout, 5xx, any unclassified error) shows
  as "unknown" (status `unknown`, never `failed`), so neither core Retry nor
  any cron resubmits it. Other outcomes show as failed with their code.
- **D3.** The carrier module is `connect_textable`; the `sms.sms` bridge is
  `connect_sms` (out of scope here).
- **D5 secrets.** The token lives on each sender number
  (`connect.textable.number`), split into account UID and API key, both
  `groups='base.group_erp_manager'` with masked `display_` twins. Textable
  posts to each user's own relayWebhook (F11), so each Textable user has its
  own relay URL (`connect.textable.relay`, rotated from a number's form by
  a system administrator). Its path token is stored only as sha256 and
  compared with `hmac.compare_digest` against every configured hash; the
  payload's TextableUserID must be the user the token was issued to, and
  TextableOrganizationID must match. Payloads not to or from a number that
  user holds are dropped without a write. AccessToken is never logged or
  stored. A werkzeug log filter redacts the relay path. Relay strings with
  control characters, and phone fields that are not digits, are refused
  as malformed before any query. Posts refused before a token matches log
  one CRITICAL line per minute, which counts the rest; rate-limit the path
  at the proxy too (`limit_req` on `/textable/webhook/relay/`).
  `data/neutralize.sql` blanks the tokens and every relay hash.
- **D6.** Odoo is the only scheduler: no `sendAt`, no `reminder`.
- **D7, local copy.** `connect.message._textable_check_send_allowed` is
  the gate: assigned sender (or system context), approved campaign, NANP
  destination, phone.blacklist, per-number hourly and daily caps, and a
  per-recipient daily cap. **These checks move to connect core with 6642**;
  until then they live in that one method and nowhere else. Inbound STOP /
  START change phone.blacklist only for an owned ToNumber, within the
  number's hourly cap. Opt-out wording is matched as a whole message with
  punctuation, emoji and separators ignored (STOP, STOP ALL, END, CANCEL,
  QUIT, UNSUBSCRIBE, OPT OUT, REVOKE). A START lifts only the STOPs sent to
  the same number, and removes the global entry only when a STOP through
  this module made it and no STOP through another number still stands
  (`connect.textable.optout`).
- **F3.** Each send uses the token of the sender number's own Textable user.
- **F13.** `send()` acts only when `_get_message_provider()` returns
  `textable` (crons route with the `connect_tool_id` context key) and
  otherwise calls `super()`.

## Send path

`send()` → `_textable_send()`: resolve the sender, build the client (a
configuration error leaves nothing behind), then claim a
`connect.textable.send` intent through `registry.cursor()`: read committed,
an advisory lock per number, the gate, and a unique `send_key`. The intent
commits before the POST. An intent in flight, accepted or ambiguous under
the same key is never posted again; a definite failure may be, as a new
claim (`claimed_at`, which the caps count from). A caller's
`idempotency_key` is bound to its sender, recipient and body: reusing it
for another message is refused (`idempotency_conflict`). The claiming
transaction holds an advisory lock on its intent until it ends; a
duplicate that finds that lock held, or the message committed after its
own snapshot, is refused instead of recording a second row. The outcome
is written through a second cursor, and the `connect.message` is created
in the caller's transaction, linked by `textable_send_key` (the caller's
snapshot cannot see the intent row, so there is no foreign key).

Echoes (`MessageDirection: out`) reconcile a unique unconfirmed or unknown
send with the same sender, recipient and body hash within 15 minutes;
otherwise they are journaled as foreign (a send made in the Textable app).
An echo that lands while the POST is still in flight (the usual case for
a read timeout) stamps the intent; the outcome cursor (read committed)
reads that stamp back, and the message is created as sent with the echo's
MessageID. An echo that lands after the outcome commits but before the
caller's transaction does still only reaches the intent
(`echo_message_uid` in the send log).

## Nothing calls out by itself

No install hook, no cron. The only outbound requests are a send, the
admin-only "Test connection" (one `GET /api/canned-responses`) and the
admin-only "Send test message" wizard, which goes through the same gate.

## Bench

Use a clone of a database, never production. Name the module: `gdo test
<db> -i connect_textable`. Tests mock `requests.Session.request`; Odoo's
`BlockedRequest` turns any unmocked call into an ambiguous outcome, never a
live one. A supervised live send (T7a) needs a dedicated Textable user that
holds only a sandbox DID, an approved campaign on that number, and the
relay URL from that number's "Rotate relay URL" registered on that user.
