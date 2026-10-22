# connect_voicetel

VoiceTel VoiceML provider for Connect: calls, numbers, SIP domains and
credentials, call flows, voicemail, recordings, SMS and a JsSIP browser
phone, on VoiceTel's `voiceml` SDK. Administrator and user guides are in
[`docs/`](docs/index.md); the design is in [`docs/spec.md`](docs/spec.md)
and [`docs/adr-066-connect-voicetel-provider.md`](docs/adr-066-connect-voicetel-provider.md).

## Origin

Written by Michael Mavroudis (VoiceTel) as oduist/connect_addons_ng PR #230
(head a3f94860), grafted verbatim and then adapted to this repository's
core and hardened in separate commits. Version 19.0.4.0.0 continues the
`connect_voicetel` version line of this repository (19.0.2.x module,
19.0.3.x retirement shim); see `migrations/19.0.4.0.0` and `hooks.py`.
It replaces `connect_voiceml`, which is no longer shipped.

## Licence record

- **Module**: the manifest declares `Other proprietary`, author Oduist, as
  in PR #230. The module appends itself to `ODUIST_MODULES` and gates
  routing, rendering, originate and messaging on `check_license('connect')`.
  Whether the HC dogfood fork keeps these gates (and the Oduist terms that
  come with them) or strips them is an open decision; until it is made the
  gates are kept as upstream wrote them. `connect_twilio`'s
  `test_license_catalog` asserts `connect_voicetel` is not an Oduist
  catalog module and fails while both are installed.
- **voiceml SDK** (PyPI `voiceml`, pinned `>=0.9.5,<0.10`): MIT with the
  Commons Clause restriction (`LicenseRef-MIT-Commons-Clause`), copyright
  VoiceTel. The Commons Clause forbids selling the SDK itself or a product
  whose value derives substantially from it; check this before offering
  the module commercially.
