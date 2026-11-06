/** @odoo-module **/
"use strict"
/**
 * A bridge line: the server rings the person's own phone, then the far end, and
 * tells the browser how it is going. No provider's JavaScript is involved.
 */

import {after, animationFrame, describe, expect, test} from "@odoo/hoot"
import {advanceTime} from "@odoo/hoot-mock"
import {waitUntil} from "@odoo/hoot-dom"
import {makeServerError} from "@web/../tests/web_test_helpers"
import {ConnectionLostError} from "@web/core/network/rpc"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {
    ServerOriginateTransport,
    SETUP_SILENCE_MS,
    UNKNOWN_CLEAR_MS,
} from "@connect/components/phone/phone/transports/server_originate_transport"
import {pbxActionService} from "@connect/services/actions/actions"
import {
    bridgeLine,
    installFakeBridgeProvider,
    publish,
} from "@connect/../tests/unit/helpers/fake_bridge_provider"
import {ALL_CAPABILITIES, mountLines} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const NUMBER = "+15550199"
// Real time passes between arming a timer and advancing past it, so a "not yet"
// stops short by this much.
const MARGIN = 5000
const UNCONFIRMED = "We could not confirm this call. It may still ring. Do not redial yet."

async function mount(options = {}, lines = [bridgeLine()]) {
    const requests = installFakeBridgeProvider(options)
    const phone = await mountLines(lines)
    await animationFrame()
    return {phone, requests}
}

/** Presses dial and waits for the originate request to have gone out. */
async function dial(phone, requests, number = NUMBER) {
    const before = requests.originates.length
    await phone.prepareCall({phone: number})
    await waitUntil(() => requests.originates.length > before)
    await animationFrame()
    return phone.session
}

function words() {
    return document.querySelector(".o_call_state_text")?.textContent ?? ""
}

async function say(phone, call, state, extra) {
    publish(phone, call.ref, state, extra)
    await animationFrame()
}

test("a bridge line takes the neutral transport, whatever the registry holds", async () => {
    const {phone} = await mount()
    const [line] = phone.lines
    expect(line.transport).toBeInstanceOf(ServerOriginateTransport)
    expect(line.needsBrowserMedia).toBe(false)
    expect(phone.state.connectionStatus).toBe("ready")
})

test("a browser line still comes from the registry", async () => {
    const {phone} = await mount({}, [{
        key: "twilio",
        tool_id: 7,
        provider: "twilio",
        media: "browser",
        capabilities: ALL_CAPABILITIES,
        token_data: {token: "t"},
    }])
    expect(phone.lines[0].transport).not.toBeInstanceOf(ServerOriginateTransport)
    expect(phone.lines[0].needsBrowserMedia).toBe(true)
})

test("a bridge line works in a browser with no microphone API", async () => {
    const original = Object.getOwnPropertyDescriptor(navigator, "mediaDevices")
    Object.defineProperty(navigator, "mediaDevices", {value: undefined, configurable: true})
    after(() => {
        if (original) {
            Object.defineProperty(navigator, "mediaDevices", original)
        } else {
            delete navigator.mediaDevices
        }
    })
    const {phone} = await mount()
    expect(phone.state.connectionStatus).toBe("ready")
    expect(phone.state.isActive).toBe(true)
})

test("a press originates once, on its tool, with a key of its own", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    expect(requests.originates.length).toBe(1)
    const [{number, kwargs}] = requests.originates
    expect(number).toBe(NUMBER)
    expect(kwargs.context.connect_tool_id).toBe(41)
    expect(kwargs.idempotency_key).toBe(call.ref)
    expect(kwargs.idempotency_key).toMatch(/^[0-9a-f]{48}$/)
    expect("caller_id" in kwargs).toBe(false)
})

test("the picked caller ID goes as caller_id with two lines", async () => {
    const {phone, requests} = await mount({}, [
        bridgeLine({caller_ids: ["+15550100", "+15550101"]}),
        bridgeLine({key: "bridge:2", tool_id: 42, label: "Support"}),
    ])
    phone.pickCallerId("+15550101")
    await dial(phone, requests)
    expect(requests.originates[0].kwargs.caller_id).toBe("+15550101")
})

test("a line with no tool sends no tool context", async () => {
    const {phone, requests} = await mount({}, [bridgeLine({tool_id: false})])
    await dial(phone, requests)
    expect(requests.originates[0].kwargs.context?.connect_tool_id).toBe(undefined)
})

test("a double press places one call", async () => {
    const {phone, requests} = await mount()
    const presses = [phone.prepareCall({phone: NUMBER}), phone.prepareCall({phone: NUMBER})]
    await Promise.all(presses)
    await waitUntil(() => requests.originates.length > 0)
    await animationFrame()
    await advanceTime(1000)
    expect(requests.originates.length).toBe(1)
    expect(phone.state.lastOperationError).toBe(false)
})

test("the next call has a new key", async () => {
    const {phone, requests} = await mount()
    const first = await dial(phone, requests)
    await say(phone, first, "ended")
    await waitUntil(() => phone.session === null && !phone.state.inCall)
    await dial(phone, requests)
    expect(requests.originates.length).toBe(2)
    expect(requests.originates[1].kwargs.idempotency_key).not.toBe(requests.originates[0].kwargs.idempotency_key)
})

test("every setup state in its words, and the timer counts from the pick-up", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    expect(words()).toBe("Calling your phone")
    expect(phone.state.phone_status).not.toBe("accepted")

    const pickedUp = new Date(Date.now() - 65000).toISOString().substring(0, 19).replace("T", " ")
    await say(phone, call, "first_leg_answered", {since: pickedUp})
    expect(words()).toBe(`You picked up, connecting ${NUMBER}`)

    await say(phone, call, "connecting")
    expect(words()).toBe(`You picked up, connecting ${NUMBER}`)
    expect(phone.state.phone_status).not.toBe("accepted")

    await say(phone, call, "in_call")
    expect(words()).toBe("In call")
    expect(phone.state.phone_status).toBe("accepted")
    expect(phone.state.callDurationTime).toBe("00:01:05")
    expect(".o_call_state_timer_label").toHaveText("since you picked up")
})

test("a far end nobody can vouch for is said differently", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    await say(phone, call, "in_call", {reason_code: "far_end_unknown"})
    expect(words()).toBe("Connected from your phone")
})

test("in a call, keypad and mute are gone and the note says where they are", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    expect(".o_call_state_note").toHaveCount(0)
    await say(phone, call, "in_call")
    expect(".o_call_state_note").toHaveText("Mute and keypad are on your phone")
    expect("button[aria-label='DTMF']").toHaveCount(0)
    expect("button[aria-label='Mute Microphone']").toHaveCount(0)
    expect("button[aria-label='Hold']").toHaveCount(0)
    expect("button[aria-label='Transfer']").toHaveCount(0)
})

for (const [state, reason_code, expected] of [
    ["ended", "", "Call ended"],
    ["ended", "normal", "Call ended"],
    ["ended", "no_answer", "They did not answer"],
    ["ended", "busy", "Line busy"],
    ["failed", "busy", "Line busy"],
    ["ended", "own_phone_no_answer", "Your phone did not answer"],
    ["ended", "own_phone_may_ring", "Your phone may ring until it times out. Answering will not connect."],
    ["failed", "", "The call could not be placed."],
]) {
    test(`${state} ${reason_code || "with no reason"} says "${expected}", ends the panel, then clears`, async () => {
        const {phone, requests} = await mount()
        const call = await dial(phone, requests)
        await say(phone, call, state, {reason_code})
        await waitUntil(() => phone.session === null && !phone.state.inCall)
        await animationFrame()
        expect(words()).toBe(expected)
        await advanceTime(10000)
        await animationFrame()
        expect(".o_call_state").toHaveCount(0)
    })
}

test("a late or repeated state cannot move the call back", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    await say(phone, call, "in_call")
    for (const late of ["connecting", "first_leg_answered", "calling", "in_call"]) {
        await say(phone, call, late)
        expect(words()).toBe("In call")
    }
})

test("a call that goes unconfirmed and comes back is not answered twice", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    let accepts = 0
    call.on("accept", () => accepts++)
    await say(phone, call, "in_call")
    await say(phone, call, "unknown")
    expect(words()).toBe(UNCONFIRMED)
    await say(phone, call, "in_call")
    expect(words()).toBe("In call")
    expect(accepts).toBe(1)
})

test("after the end, nothing more is heard", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    await say(phone, call, "ended", {reason_code: "busy"})
    await waitUntil(() => phone.session === null && !phone.state.inCall)
    await say(phone, call, "in_call")
    await say(phone, call, "ended")
    expect(words()).toBe("Line busy")
    expect(phone.state.inCall).toBe(false)
})

test("a state nobody knows, or for someone else's call, changes nothing", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    await say(phone, call, "ringing")
    expect(words()).toBe("Calling your phone")
    publish(phone, "another-tabs-call", "ended")
    await animationFrame()
    expect(words()).toBe("Calling your phone")
    expect(phone.session).toBe(call)
})

test("a refusal says why in the server's words, and allows the next press", async () => {
    let refuse = true
    const {phone, requests} = await mount({
        originate() {
            if (refuse) {
                throw makeServerError({
                    type: "UserError",
                    description: "Emergency and service numbers are not available on this line. Use your phone.",
                })
            }
            return true
        },
    })
    await dial(phone, requests)
    await waitUntil(() => phone.session === null && !phone.state.inCall)
    await animationFrame()
    expect(words()).toBe("Emergency and service numbers are not available on this line. Use your phone.")
    expect(requests.hangups.length).toBe(0)
    refuse = false
    await dial(phone, requests)
    expect(requests.originates.length).toBe(2)
})

test("a lost answer is said, blocks a redial, and clears after a bounded time", async () => {
    let lose = true
    const {phone, requests} = await mount({
        originate() {
            if (lose) {
                throw new ConnectionLostError("/web/dataset/call_kw")
            }
            return true
        },
    })
    await dial(phone, requests)
    await waitUntil(() => words() === UNCONFIRMED)
    expect(phone.state.inCall).toBe(true)

    await advanceTime(UNKNOWN_CLEAR_MS - MARGIN)
    await animationFrame()
    expect(words()).toBe(UNCONFIRMED)
    expect(phone.session).not.toBe(null)

    await advanceTime(MARGIN)
    await waitUntil(() => phone.session === null && !phone.state.inCall)
    await animationFrame()
    expect(".o_call_state").toHaveCount(0)

    lose = false
    await dial(phone, requests)
    expect(requests.originates.length).toBe(2)
    expect(requests.originates[1].kwargs.idempotency_key).not.toBe(requests.originates[0].kwargs.idempotency_key)
})

test("hanging up on an unconfirmed call still blocks the redial until the clear", async () => {
    const {phone, requests} = await mount({
        originate() {
            throw new ConnectionLostError("/web/dataset/call_kw")
        },
    })
    await dial(phone, requests)
    await waitUntil(() => words() === UNCONFIRMED)
    await phone._onClickEndCall()
    await waitUntil(() => phone.session === null && !phone.state.inCall)
    expect(requests.hangups.length).toBe(1)

    await phone.prepareCall({phone: NUMBER})
    await animationFrame()
    expect(requests.originates.length).toBe(1)
    expect(phone.state.inCall).toBe(false)
    expect(phone.state.lastOperationError).toBe(UNCONFIRMED)

    await advanceTime(UNKNOWN_CLEAR_MS)
    await animationFrame()
    expect(".o_call_state").toHaveCount(0)
    await dial(phone, requests)
    expect(requests.originates.length).toBe(2)
})

test("an event after a lost answer still finds the call, and settles it", async () => {
    const {phone, requests} = await mount({
        originate() {
            throw new ConnectionLostError("/web/dataset/call_kw")
        },
    })
    const call = await dial(phone, requests)
    await waitUntil(() => words() === UNCONFIRMED)
    await say(phone, call, "in_call")
    expect(words()).toBe("In call")
    expect(phone.state.phone_status).toBe("accepted")
    await advanceTime(UNKNOWN_CLEAR_MS)
    await animationFrame()
    expect(phone.session).toBe(call)
    expect(phone.lines[0].transport.dialRefusal).toBe(null)
})

test("a lost answer does not undo what the server already said", async () => {
    let release
    const {phone, requests} = await mount({
        originate: () => new Promise((resolve, reject) => {
            release = () => reject(new ConnectionLostError("/web/dataset/call_kw"))
        }),
    })
    const call = await dial(phone, requests)
    await say(phone, call, "in_call")
    release()
    await animationFrame()
    await animationFrame()
    expect(words()).toBe("In call")
})

test("silence while setting up becomes unconfirmed, and any word restarts the wait", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    await advanceTime(SETUP_SILENCE_MS - MARGIN)
    await say(phone, call, "first_leg_answered")
    await advanceTime(SETUP_SILENCE_MS - MARGIN)
    await animationFrame()
    expect(words()).toBe(`You picked up, connecting ${NUMBER}`)
    await advanceTime(MARGIN)
    await animationFrame()
    expect(words()).toBe(UNCONFIRMED)
})

test("a call in progress does not time out", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    await say(phone, call, "in_call")
    await advanceTime(SETUP_SILENCE_MS + UNKNOWN_CLEAR_MS)
    await animationFrame()
    expect(words()).toBe("In call")
    expect(phone.session).toBe(call)
})

test("End hangs up with the call's ref, provider and tool", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    await say(phone, call, "in_call")
    await phone._onClickEndCall()
    await waitUntil(() => requests.hangups.length > 0)
    const [{payload, kwargs}] = requests.hangups
    expect(payload).toEqual({provider: "fake_bridge", call_ref: call.ref, line_key: "bridge:1"})
    expect(kwargs.context.connect_tool_id).toBe(41)
    await waitUntil(() => phone.session === null && !phone.state.inCall)
    await animationFrame()
    expect(words()).toBe("Call ended")
})

test("the server's reason still reaches the panel after End", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    await phone._onClickEndCall()
    await waitUntil(() => phone.session === null && !phone.state.inCall)
    await say(phone, call, "ended", {reason_code: "own_phone_may_ring"})
    expect(words()).toBe("Your phone may ring until it times out. Answering will not connect.")
})

test("End while the request is out hangs up only when it has an answer", async () => {
    let answer
    const {phone, requests} = await mount({
        originate: () => new Promise((resolve) => { answer = resolve }),
    })
    await dial(phone, requests)
    await phone._onClickEndCall()
    await animationFrame()
    expect(requests.hangups.length).toBe(0)
    answer(true)
    await waitUntil(() => requests.hangups.length === 1)
})

test("End before anything was sent sends nothing", async () => {
    const {phone, requests} = await mount()
    await phone.makeCall({phone: NUMBER})
    await phone._onClickEndCall()
    await advanceTime(100)
    await animationFrame()
    expect(requests.originates.length).toBe(0)
    expect(requests.hangups.length).toBe(0)
})

test("a hang-up that gets no answer is said, and blocks the redial", async () => {
    const {phone, requests} = await mount({
        hangup() {
            throw new ConnectionLostError("/web/dataset/call_kw")
        },
    })
    await dial(phone, requests)
    await phone._onClickEndCall()
    await waitUntil(() => words() === UNCONFIRMED)
    await waitUntil(() => phone.session === null && !phone.state.inCall)
    expect(phone.lines[0].transport.dialRefusal).toBe(UNCONFIRMED)
})

test("closing the tab leaves a call on the person's own phone alone", async () => {
    const {phone, requests} = await mount()
    await dial(phone, requests)
    phone._unloadHandler()
    await animationFrame()
    expect(requests.hangups.length).toBe(0)
})

test("the actions service routes the event to the phone's bus", async () => {
    const {phone, requests} = await mount()
    const call = await dial(phone, requests)
    const subscribed = {}
    const previous = window.connectBus
    window.connectBus = phone.bus
    after(() => { window.connectBus = previous })
    pbxActionService.start({}, {
        action: {},
        notification: {},
        bus_service: {
            addChannel() {},
            subscribe(name, callback) { subscribed[name] = callback },
        },
    })
    subscribed.connect_call_state({call_ref: call.ref, line_key: "bridge:1", state: "in_call", reason_code: "", since: false})
    await animationFrame()
    expect(words()).toBe("In call")
})

test("the service has nowhere to route for someone with no phone, and does not mind", async () => {
    const previous = window.connectBus
    window.connectBus = undefined
    after(() => { window.connectBus = previous })
    expect(pbxActionService.connect_handle_call_state({call_ref: "k", state: "ended"})).toBe(undefined)
})

test("a browser line is unchanged: no words, and its wire stays To and Called", async () => {
    const {phone, requests} = await mount({}, [{
        key: "twilio",
        tool_id: 7,
        provider: "twilio",
        media: "browser",
        capabilities: ALL_CAPABILITIES,
        token_data: {token: "t"},
    }])
    await phone.prepareCall({phone: NUMBER})
    await waitUntil(() => phone.session)
    await animationFrame()
    expect(requests.originates.length).toBe(0)
    expect(phone.session._params).toEqual({To: NUMBER, Called: NUMBER})
    phone.session.accept()
    await animationFrame()
    expect(phone.state.callDurationTime).toBe("00:00:00")
    expect(".o_call_state").toHaveCount(0)
    phone.session.disconnect()
    await waitUntil(() => phone.session === null)
})

test("the keypad's unload path still ends a browser call", async () => {
    const {phone} = await mount({}, [{
        key: "twilio",
        tool_id: 7,
        provider: "twilio",
        media: "browser",
        capabilities: ALL_CAPABILITIES,
        token_data: {token: "t"},
    }])
    await phone.prepareCall({phone: NUMBER})
    await waitUntil(() => phone.session)
    const call = phone.session
    let ended = false
    call.on("disconnect", () => { ended = true })
    phone._unloadHandler()
    expect(ended).toBe(true)
})
