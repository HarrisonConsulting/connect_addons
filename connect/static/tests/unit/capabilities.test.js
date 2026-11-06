/** @odoo-module **/
"use strict"
/**
 * The panel offers what the line can do. A provider that can do nothing still
 * gets a working call with mute and keypad, and the phone never asks the server
 * what a line can do, because the line record already said.
 */

import {after, animationFrame, describe, expect, test} from "@odoo/hoot"
import {advanceTime} from "@odoo/hoot-mock"
import {onRpc, patchWithCleanup} from "@web/../tests/web_test_helpers"
import {registry} from "@web/core/registry"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {TelephonyTransport} from "@connect/components/phone/phone/transports/telephony_transport"
import {TwilioTransport} from "@connect/components/phone/phone/transports/twilio_transport"
import {LoopbackCall, LoopbackTransport} from "@connect/../tests/helpers/loopback_transport"
import {ALL_CAPABILITIES, mountLines} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const FAKE = "capability_less_provider"
const CONTROLS = ["Hold", "Transfer", "Add Participant", "DTMF", "Mute Microphone"]

function registerFake(TransportClass = LoopbackTransport) {
    const transports = registry.category("connect.telephony_transports")
    transports.add(FAKE, TransportClass, {force: true})
    after(() => transports.remove(FAKE))
}

function line(capabilities) {
    return {key: FAKE, provider: FAKE, media: "browser", capabilities, token_data: {token: "t"}}
}

function watchServer() {
    const asked = {recording: 0, capabilities: 0}
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    onRpc("connect.channel", "softphone_capabilities", () => {
        asked.capabilities++
        return {}
    })
    return asked
}

async function mountInCall(capabilities, TransportClass) {
    registerFake(TransportClass)
    const asked = watchServer()
    const phone = await mountLines([line(capabilities)])
    // After the mount, because the mount helper answers this one too and the
    // last handler wins.
    onRpc("connect.channel", "get_softphone_recording_state", () => {
        asked.recording++
        return {}
    })
    await phone.makeCall({phone: "+15550100"})
    phone.session.accept()
    await animationFrame()
    return {phone, asked}
}

function controlsShown() {
    return CONTROLS.filter(
        (label) => document.querySelector(`.o_root_connect_phone button[aria-label='${label}']`))
}

test("a capability-less provider still has its call, mute and keypad", async () => {
    const {phone, asked} = await mountInCall({})
    expect(controlsShown()).toEqual(["DTMF", "Mute Microphone"])
    expect(".o_recording_panel").toHaveCount(0)
    expect(asked).toEqual({recording: 0, capabilities: 0})
    phone.session.disconnect()
})

test("a line with every capability shows every control", async () => {
    const {phone, asked} = await mountInCall(ALL_CAPABILITIES)
    expect(controlsShown()).toEqual(CONTROLS)
    expect(".o_recording_panel").toHaveCount(1)
    expect(asked).toEqual({recording: 1, capabilities: 0})
    phone.session.disconnect()
})

test("each control answers to its own capability", async () => {
    const {phone} = await mountInCall({forward: true, add_participant: true})
    expect(controlsShown()).toEqual(["Transfer", "Add Participant", "DTMF", "Mute Microphone"])
    expect(".o_recording_panel").toHaveCount(0)
    phone.session.disconnect()
})

test("the recording toolbar needs start, and the state read needs state", async () => {
    const {phone, asked} = await mountInCall({recording_start: true})
    expect(".o_recording_panel").toHaveCount(1)
    expect(asked.recording).toBe(0)
    phone.session.disconnect()
})

test("hold needs both directions, so a caller cannot be stranded", async () => {
    const {phone} = await mountInCall({hold: true})
    expect(controlsShown()).not.toInclude("Hold")
    phone.lines[0].capabilities = {hold: true, resume: true}
    phone.render()
    await animationFrame()
    expect(controlsShown()).toInclude("Hold")
    phone.session.disconnect()
})

test("an attended transfer needs start, complete and cancel", async () => {
    const {phone} = await mountInCall({
        forward: true, attended_start: true, attended_complete: true})
    phone.state.showTransferChoice = true
    await animationFrame()
    expect(".btn:contains('Blind')").toHaveCount(1)
    expect(".btn:contains('Attended')").toHaveCount(0)
    phone.lines[0].capabilities = {...phone.lines[0].capabilities, attended_cancel: true}
    phone.render()
    await animationFrame()
    expect(".btn:contains('Attended')").toHaveCount(1)
    phone.state.showTransferChoice = false
    phone.session.disconnect()
})

class NoMediaCall extends LoopbackCall {
    sendDigits(digits) {
        NoMediaCall.sent.push(digits)
    }
}
NoMediaCall.sent = []

class NoMediaTransport extends LoopbackTransport {
    static needsBrowserMedia = false

    async dial(params) {
        return new NoMediaCall(params)
    }
}

test("a line with no browser media has no mute and no keypad", async () => {
    NoMediaCall.sent = []
    const {phone} = await mountInCall(ALL_CAPABILITIES, NoMediaTransport)
    expect(controlsShown()).toEqual(["Hold", "Transfer", "Add Participant"])
    phone.sendDTMF("5")
    expect(NoMediaCall.sent).toEqual([])
    phone.session.disconnect()
})

test("a line with browser media sends the digit", async () => {
    const sent = []
    patchWithCleanup(LoopbackCall.prototype, {
        sendDigits(digits) {
            sent.push(digits)
        },
    })
    const {phone} = await mountInCall({})
    phone.sendDTMF("5")
    expect(sent).toEqual(["5"])
    phone.session.disconnect()
})

test("a line with no capabilities value offers none", async () => {
    registerFake()
    watchServer()
    const phone = await mountLines([
        {key: FAKE, provider: FAKE, media: "browser", token_data: {token: "t"}},
    ])
    expect(phone.can("hold")).toBe(false)
    expect(phone.can("mute")).toBe(true)
})

test("a line with no provider offers nothing and does not throw", async () => {
    watchServer()
    const phone = await mountLines([
        {key: "", provider: "", media: "browser", capabilities: {}, token_data: {}},
    ])
    expect(phone.can("mute")).toBe(false)
    expect(phone.can("dtmf")).toBe(false)
    expect(phone.can("hold")).toBe(false)
})

test("the base transport classifies by neutral names and knows no carrier's codes", () => {
    const classify = (error) => TelephonyTransport.classifyError(error)
    expect(classify({name: "AccessTokenExpired"})).toBe("auth")
    expect(classify({name: "AccessTokenInvalid"})).toBe("auth")
    expect(classify({name: "NotSupportedError"})).toBe("unsupported")
    expect(classify({code: 31009})).toBe("other")
    expect(classify({code: 31005})).toBe("other")
    expect(classify({})).toBe("other")
})

test("Twilio's dead-connection codes are Twilio's", () => {
    const classify = (error) => TwilioTransport.classifyError(error)
    expect(classify({code: 31009})).toBe("transport")
    expect(classify({code: 31005})).toBe("transport")
    expect(classify({code: 31000})).toBe("other")
    expect(classify({name: "AccessTokenInvalid"})).toBe("auth")
    expect(classify({name: "NotSupportedError"})).toBe("unsupported")
})

test("both transports default to browser media", () => {
    expect(TelephonyTransport.needsBrowserMedia).toBe(true)
    expect(TwilioTransport.needsBrowserMedia).toBe(true)
})

class DeadTransport extends LoopbackTransport {
    static classifyError() {
        return "transport"
    }
}

test("a dead transport waits longer than a rejected credential, by its class's word", async () => {
    patchWithCleanup(console, {error() {}})
    registerFake(DeadTransport)
    watchServer()
    const phone = await mountLines([line({})])
    await animationFrame()
    const [only] = phone.lines
    let reconnects = 0
    patchWithCleanup(only, {
        reconnect() {
            reconnects++
        },
    })
    only.transport._emit("error", {code: 1, message: "gone"})
    await advanceTime(4999)
    expect(reconnects).toBe(0)
    await advanceTime(1)
    expect(reconnects).toBe(1)
})

test("an unclassified error is shown and does not reconnect", async () => {
    patchWithCleanup(console, {error() {}})
    registerFake()
    watchServer()
    const phone = await mountLines([line({})])
    await animationFrame()
    const [only] = phone.lines
    let reconnects = 0
    patchWithCleanup(only, {
        reconnect() {
            reconnects++
        },
    })
    only.transport._emit("error", {code: 31009, message: "a code only Twilio knows"})
    await advanceTime(6000)
    expect(reconnects).toBe(0)
    expect(phone.state.connectionStatus).toBe("error")
})
