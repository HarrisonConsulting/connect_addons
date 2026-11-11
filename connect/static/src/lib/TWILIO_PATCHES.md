# Bundled Twilio Voice SDK

The shipped SDK remains Twilio Voice SDK 2.15.0 under its existing Apache-2.0 and embedded third-party notices. Connect changes its checked-in unminified source and regenerates the two runtime bundles; runtime code uses these artifacts directly.

## Local patch 17379 — 2026-11-11

`OutputDeviceCollection.delete` owns device-loss cleanup. Upstream 2.15.0 invokes `_beforeChange` without the capability check used by `set`, and discards its promise. `AudioHelper._updateDevices` calls deletion during device enumeration even in browsers without output selection. An active call's internal sink setter then rejects, with no cleanup caller observing the rejection.

The local patch preserves synchronous deletion, fallback collection bookkeeping, return value and supported callback invocation timing. It guards sink changes with the existing collection capability flag. It handles asynchronous rejection and synchronous callback exceptions at their source, logging the actual error at SDK ERROR level. Explicit `set` still rejects unsupported selection and real selection failures to its caller. This patch changes no public browser API and adds no global error suppression.

The browser capability is `HTMLAudioElement.prototype.setSinkId`; the SDK's `_setSinkIds` methods are internal wrappers. The production error establishes the unsupported cleanup path. Production HTTPS, user gestures and audible playback impact have separate evidence; this source repair makes no claim about gesture state or successful audio playback.

Pre-patch unminified source SHA256: `46cdf71aabf3c6b6f8469a704c746aaddefb687a1041e13fcd23bc471f4b70bd`.
Pre-patch runtime bundles (both identical) SHA256: `4cc60c1680bb25f99718993fd8045a5ebcc459e066da357ce729ac14e1d4f9ff`.

## Reproduce artifacts and regressions

Use esbuild **0.21.5**, supplied as the first positional module path or available through Node's normal module resolution. The build script fixes target, minification, character and legal-comment options and removes generated trailing whitespace.

```sh
node scripts/build_twilio_vendor.cjs /path/to/node_modules/esbuild
node scripts/build_twilio_vendor.cjs /path/to/node_modules/esbuild --check
node scripts/test_twilio_output_cleanup.cjs
```

The build writes core `twilio.min.js` and the identical Website snippet copy. The source SDK header retains all embedded licensing notices and marks the local patch.

The regression executes actual source and generated SDKs in isolated Node VMs. Test-only instrumentation exposes the existing Browserify loader. It runs actual AudioHelper enumeration, OutputDeviceCollection, Device speaker update, Call sink forwarding and PeerConnection sink methods with controlled device fixtures. It covers unsupported device removal, supported fallback selection, asynchronous/synchronous cleanup errors, explicit selection refusal and leaked-rejection detection. `--baseline` runs against the repository HEAD artifacts and fails on the original unsupported cleanup callback, establishing sensitivity. Browser/device/permissions and live audio remain separate acceptance gates.
