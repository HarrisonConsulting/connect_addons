# Webhooks & Security

## Routes

All VoiceML callbacks arrive under `/voicetel/webhook/*` and run as the
dedicated webhook user (`connect.user_connect_webhook`):

| Route | Purpose |
|-------|---------|
| `/voicetel/webhook/domain` | Voice URL of the tenant SIP domains: routes calls placed from registered devices |
| `/voicetel/webhook/number` | Voice URL of the account's numbers |
| `/voicetel/webhook/voiceml/<id>` | Voice URL of a VoiceML app |
| `/voicetel/webhook/callstatus` | Call status callbacks (initiated/ringing/answered/completed) |
| `/voicetel/webhook/<model>/call_action/<id>` | `<Dial>` action of a user or call flow |
| `/voicetel/webhook/callflow/<id>/gather` | `<Gather>` action of a call flow |
| `/voicetel/webhook/recordingstatus` | Recording status callbacks |
| `/voicetel/webhook/vm_recordingstatus` | Voicemail recording callbacks |
| `/voicetel/webhook/outgoing_callerid` | Caller-ID verification result |
| `/voicetel/webhook/message` | Inbound messages and delivery receipts |

## Signature validation

VoiceML signs every callback and every voice URL fetch with the
`X-Twilio-Signature` header: an HMAC-SHA1 of the URL as transmitted plus the
POST parameters sorted by key (every value of a repeated key), keyed with
the account **API key**. The module validates it with the voiceml SDK's
`validate_request`, port-tolerant and constant-time.

The check fails closed. A request is rejected, and nothing is processed,
when the signature is missing or wrong, when **Verify VoiceTel Requests** is
off, or when no API key is set; the configuration cases are logged at
CRITICAL. Voice URLs answer a spoken "Invalid VoiceTel request", status
callbacks an empty body.

The validated URL is the request URL with its scheme set to `https` (only
the scheme: an `http:` inside the query string is kept), query string
included. Keep the Connect **API URL** equal to the public address VoiceML
calls; a reverse proxy that rewrites the host or path breaks validation.

## Entry points are not RPC

Every webhook handler (`on_voicetel_*`, `route_call`, `render`,
`gather_action`, `voicetel_receive`, `update_status`, …) and every helper
that acts with the account's credentials (`get_voicetel_client`,
`voicetel_client_send`, `voicetel_download`, `_get_media_auth`) is
`@api.private` or underscore-named, so it cannot be called through
`/web/dataset/call_kw` with forged webhook input. Sync, caller-ID
validation and the revoke ledger require **Connect Administrator**.

## Applications

A VoiceML app renders in a sandboxed, autoescaping template; a
`model.method` app may only name the SIP domain router. There is no Python
app type. See [Call Flows & VoiceML Apps](call-flows-voiceml.md).

## Recordings

Recording callbacks download the audio into Odoo immediately, with the
account credentials: VoiceML keeps the file only until it is archived to
the tenant's own bucket or cleaned up. A recording that could not be
fetched in time keeps its URL and is proxied for as long as VoiceML serves
it.

Recording, voicemail and MMS URLs are webhook input, so the server fetches
one only when it is `https`, carries no userinfo and is on the API host or
a configured **Media Host**; anything else is dropped. The API key goes to
the API host alone. A redirect (to the tenant's presigned bucket) is
followed once, over https, without credentials; downloads have a timeout
and a 200 MB cap.

## Messages

SMS bodies are escaped before they reach the chatter. Sending checks read
access to the target record as the sender and only allows the sender's own
or the default caller ID. A replayed inbound `MessageSid` is stored once; a
processing failure answers 500 so VoiceML retries.

## Neutralized databases

`data/neutralize.sql` blanks the API key and every SIP password, turns auto
sync and price fetching off, deactivates the price cron and blanks secrets
in the `connect_voicetel_legacy_*` tables. With no API key every webhook is
rejected.

## Old credentials

**VoiceTel ▸ Configuration ▸ Orphaned SIP credentials (dry run)** lists
credentials no active SIP user owns, plus the retired module's credentials
and domains (from `connect_voicetel_user_archive` and
`connect_voicetel_legacy_domain`). Nothing is deleted from the menu; after
reviewing the ledger in the server log, an administrator runs
`env['connect.voicetel.domain']._revoke_orphan_credentials(apply=True)`
from a shell.

## Access groups

| Group | Access |
|-------|--------|
| **Connect User** | Read access to numbers, extensions, call flows, caller IDs, VoiceML apps and SIP domains; their own SIP password through the browser phone only |
| **Connect Administrator** | Full access, settings, message configuration, SIP passwords |
| **Webhook user** | Read access needed to route calls; the verification callback writes under sudo |

The API key is stored on a field restricted to the Settings manager group
and is never returned to the webhook user or over RPC.
