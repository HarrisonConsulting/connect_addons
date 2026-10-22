# Call Flows & VoiceML Apps

## Call flows

**Connect ▸ VoiceTel ▸ Call Flows** builds IVR menus and ring groups without
writing markup.

| Section | Fields |
|---------|--------|
| **General** | Name, language and voice for `<Say>`, **Record Calls**, **Gather Input** |
| **Gather Settings** | Input type (DTMF, speech, both), timeout, number of digits, speech hints |
| **Prompt Message** | Spoken before the menu; **Invalid Input Message** when no choice matches |
| **Choices** | Digits (or a spoken phrase) → extension |
| **Ring Users** | Users rung at once; each rings on every device registered with their SIP credential |
| **Voicemail** | Prompt recorded when nobody answers |

Assign an extension with the **Extension** button, or route a number to the
call flow.

Speech recognition and the `voice` names depend on the TTS/STT engines
configured on the VoiceML account.

## VoiceML apps

**Connect ▸ VoiceTel ▸ VoiceML Apps** holds custom voice applications. Each
app is mirrored as an Application on the VoiceML account whose voice URL
points at `/voicetel/webhook/voiceml/<id>`.

| Type | Content |
|------|---------|
| **VoiceML** | Raw markup, rendered as a sandboxed Jinja2 template with the request parameters (`From`, `To`, `CallSid`, …). Parameters are XML-escaped, so a caller-supplied value cannot add verbs. |
| **model.method** | Calls one of the module's routing entry points. Only `connect.voicetel.domain.route_call` is allowed: the **SIP Domain Calls** app, which routes every call placed from a registered device. Any other target is refused on save and answered with `<Reject/>`. |

There is no Python application type: an application runs on every call
that reaches a public webhook, so it cannot execute code.

VoiceML accepts the full Twilio voice verb set except `<Pay>`; `<Dial>`
supports `<Number>`, `<Sip>`, `<Client>`, `<Conference>`, `<Queue>` and
`<Application>`. Only `<Sip>` to the tenant domain reaches registered
devices.

!!! note "The SIP Domain Calls app is required"
    Every tenant SIP domain points at it. If it is deleted it is recreated
    the next time a domain is saved or synced.
