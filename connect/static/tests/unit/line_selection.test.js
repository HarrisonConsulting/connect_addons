/** @odoo-module **/
"use strict"
/**
 * Which line a call goes out on. The person chooses, the choice stays, and nothing
 * else ever moves it.
 */

import {after, animationFrame, describe, expect, test} from "@odoo/hoot"
import {click, select, waitUntil} from "@odoo/hoot-dom"
import {onRpc, patchWithCleanup} from "@web/../tests/web_test_helpers"
import {browser} from "@web/core/browser/browser"
import {registry} from "@web/core/registry"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {howLineConnects, loadSelectedLine, saveSelectedLine} from "@connect/components/phone/line_selector/line_selector"
import {
    LoopbackCall,
    LoopbackTransport,
    registerLoopbackProvider,
} from "@connect/../tests/helpers/loopback_transport"
import {mountLines, mountSingleLinePhone} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const NEUTRAL = "neutral_provider"
const NUMBER = "+15550199"
const STORAGE_KEY = "connect_phone_line"

function makeLine(key, label, extra = {}) {
    return {
        key,
        tool_id: 1,
        label,
        provider: key === "desk" ? "twilio" : NEUTRAL,
        media: "browser",
        capabilities: {},
        token_data: {token: `${key}-token`},
        ...extra,
    }
}

async function mountNamed(...lines) {
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    registerLoopbackProvider(NEUTRAL)
    after(() => registry.category("connect.telephony_transports").remove(NEUTRAL))
    after(() => browser.localStorage.removeItem(STORAGE_KEY))
    const phone = await mountLines(lines)
    await animationFrame()
    return phone
}

const mountTwo = (extra = {}) => mountNamed(makeLine("desk", "Desk"), makeLine("sales", "Sales", extra))

/** Records each dial with the transport it went through. */
function watchDials() {
    const dials = []
    patchWithCleanup(LoopbackTransport.prototype, {
        async dial(params) {
            dials.push({transport: this, params: {...params}})
            return super.dial(params)
        },
    })
    return dials
}

function ring(line) {
    const call = new LoopbackCall({From: "+15550100", Partner: "false"})
    line.transport._emit("incoming", call)
    return call
}

/** Refuses this one key: the rest of the web client reads storage while it mounts. */
function refuseTheChoice() {
    const refuse = (original) => function (key, ...args) {
        if (key === STORAGE_KEY) {
            throw new Error("storage is refused")
        }
        return original.call(browser.localStorage, key, ...args)
    }
    patchWithCleanup(browser.localStorage, {
        getItem: refuse(browser.localStorage.getItem),
        setItem: refuse(browser.localStorage.setItem),
    })
}

test("two or three lines are chips, by name", async () => {
    await mountNamed(makeLine("desk", "Desk"), makeLine("sales", "Sales"), makeLine("night", "Night"))
    expect(".o_phone_line_selector[aria-label='From']").toHaveCount(1)
    expect(".o_phone_line_chip").toHaveCount(3)
    expect(".o_phone_line_dropdown").toHaveCount(0)
    expect(".o_phone_line_chip.active").toHaveText("Desk")
})

test("four lines are a dropdown, and it selects", async () => {
    const phone = await mountNamed(
        makeLine("desk", "Desk"), makeLine("sales", "Sales"), makeLine("night", "Night"), makeLine("day", "Day"))
    expect(".o_phone_line_chip").toHaveCount(0)
    expect(".o_phone_line_dropdown option").toHaveCount(4)
    await click(".o_phone_line_dropdown")
    await select("night", {target: ".o_phone_line_dropdown"})
    await animationFrame()
    expect(phone.activeLine.key).toBe("night")
    expect(browser.localStorage.getItem(STORAGE_KEY)).toBe("night")
})

test("a line with no name is never shown by its key", async () => {
    await mountNamed(makeLine("desk", ""), makeLine("sales", "Sales"))
    expect(".o_phone_line_chip:first").toHaveText("Line 1")
})

test("one line has no row, no To label and no height of its own, and never reads the choice", async () => {
    refuseTheChoice()
    const phone = await mountSingleLinePhone({token: "only"})
    await animationFrame()
    expect(".o_phone_line_selector").toHaveCount(0)
    expect(".o_phone_to_label").toHaveCount(0)
    expect(".o_phone_line_how_block").toHaveCount(0)
    expect(".o_phone_multi_line").toHaveCount(0)
    expect(phone.phoneHeight).toBe(486)
    expect(phone.activeLine.key).toBe("twilio")
})

test("two lines raise the drag limit, and the class that raises the height", async () => {
    const phone = await mountTwo()
    expect(phone.phoneHeight).toBe(572)
    expect(".o_root_connect_phone.o_phone_multi_line").toHaveCount(1)
    expect(".o_phone_to_label").toHaveCount(1)
})

test("a choice is remembered, and comes back on the next mount", async () => {
    const phone = await mountTwo()
    expect(phone.activeLine.key).toBe("desk")
    expect(browser.localStorage.getItem(STORAGE_KEY)).toBe(null)
    await click(".o_phone_line_chip:last")
    await animationFrame()
    expect(phone.activeLine.key).toBe("sales")
    expect(".o_phone_line_chip.active").toHaveText("Sales")
    expect(browser.localStorage.getItem(STORAGE_KEY)).toBe("sales")
    expect(loadSelectedLine()).toBe("sales")
})

test("a remembered line is used from the start", async () => {
    browser.localStorage.setItem(STORAGE_KEY, "sales")
    const phone = await mountTwo()
    expect(phone.activeLine.key).toBe("sales")
    expect(".o_phone_line_chip.active").toHaveText("Sales")
})

test("a remembered line that is gone falls back, and the memory is left alone", async () => {
    browser.localStorage.setItem(STORAGE_KEY, "gone")
    const phone = await mountTwo()
    expect(phone.activeLine.key).toBe("desk")
    expect(browser.localStorage.getItem(STORAGE_KEY)).toBe("gone")
})

test("storage that throws costs the memory, not the phone", async () => {
    refuseTheChoice()
    expect(loadSelectedLine()).toBe(null)
    expect(() => saveSelectedLine("sales")).not.toThrow()
    const phone = await mountTwo()
    expect(phone.activeLine.key).toBe("desk")
    await click(".o_phone_line_chip:last")
    await animationFrame()
    expect(phone.activeLine.key).toBe("sales")
    expect(".o_phone_line_chip.active").toHaveText("Sales")
})

test("a call goes out on the selected line, with exactly To and Called", async () => {
    const dials = watchDials()
    const phone = await mountTwo()
    await click(".o_phone_line_chip:last")
    await animationFrame()
    phone.bus.trigger("busPhoneMakeCall", {phone: NUMBER})
    await waitUntil(() => dials.length > 0)
    expect(dials[0].transport).toBe(phone.lines[1].transport)
    expect(dials[0].params).toEqual({To: NUMBER, Called: NUMBER})
    await animationFrame()
    expect(phone.lines[1].session).not.toBe(null)
    expect(phone.lines[0].session).toBe(null)
})

test("a click-to-call on a collapsed panel opens it showing From", async () => {
    const dials = watchDials()
    const phone = await mountTwo()
    phone.state.isDisplay = true
    phone.state.isCollapsed = true
    await animationFrame()
    phone.bus.trigger("busPhoneMakeCall", {phone: NUMBER})
    await waitUntil(() => dials.length > 0)
    await animationFrame()
    expect(phone.state.isCollapsed).toBe(false)
    expect(".o_phone_line_selector").toHaveCount(1)
    expect(".o_phone_line_chip.active").toHaveText("Desk")
})

test("a dropped line says so, and no other line takes the call", async () => {
    const dials = watchDials()
    const phone = await mountTwo()
    await click(".o_phone_line_chip:last")
    await animationFrame()
    phone.lines[1].transport = null
    phone.bus.trigger("busPhoneMakeCall", {phone: NUMBER})
    await waitUntil(() => phone.state.lastOperationError)
    expect(dials).toEqual([])
    expect(phone.state.inCall).toBe(false)
    expect(phone.state.lastOperationError).toInclude("Sales")
    expect(phone.state.selectedLineKey).toBe("sales")
})

test("the row is read-only during a call, and selecting does nothing", async () => {
    const dials = watchDials()
    const phone = await mountTwo({caller_ids: ["+15550101", "+15550102"]})
    await click(".o_phone_line_chip:last")
    await animationFrame()
    expect(".o_phone_line_caller_id_picker").toHaveCount(1)
    expect(".o_phone_line_change").toHaveCount(1)
    phone.bus.trigger("busPhoneMakeCall", {phone: NUMBER})
    await waitUntil(() => dials.length > 0)
    await animationFrame()
    expect(".o_phone_line_chip:enabled").toHaveCount(0)
    expect(".o_phone_line_caller_id_picker").toHaveCount(0)
    expect(".o_phone_line_caller_id_value").toHaveText("+15550101")
    expect(".o_phone_line_change").toHaveCount(0)
    phone.selectLine("desk")
    phone.pickCallerId("+15550102")
    expect(phone.state.selectedLineKey).toBe("sales")
    expect(phone.callerIdOf(phone.activeLine)).toBe("+15550101")
})

test("a call on the other line shows that line and never moves the selection", async () => {
    const phone = await mountTwo()
    const call = ring(phone.lines[1])
    await animationFrame()
    expect(phone.activeLine.key).toBe("sales")
    expect(".o_phone_line_chip.active").toHaveText("Sales")
    expect(".o_phone_line_chip:enabled").toHaveCount(0)
    expect(phone.state.selectedLineKey).toBe("desk")
    expect(browser.localStorage.getItem(STORAGE_KEY)).toBe(null)
    call.reject()
    await waitUntil(() => phone.session === null && !phone.state.inIncoming)
    await animationFrame()
    expect(phone.activeLine.key).toBe("desk")
    expect(".o_phone_line_chip.active").toHaveText("Desk")
})

test("the row hides on the Work tab and on Audio Settings", async () => {
    const phone = await mountTwo()
    phone.state.activeTab = "work"
    await animationFrame()
    expect(".o_phone_line_selector").toHaveCount(0)
    phone.state.activeTab = "phone"
    await animationFrame()
    expect(".o_phone_line_selector").toHaveCount(1)
    await click(".o_phone_line_change")
    await animationFrame()
    expect(phone.state.isAudioSettings).toBe(true)
    expect(".o_phone_line_selector").toHaveCount(0)
    expect(".o_phone_line_how_item").toHaveCount(2)
})

test("the caption gives the caller ID and how the line connects", async () => {
    const phone = await mountTwo({
        media: "bridge",
        token_data: {media: "bridge", rings_first: "+15745555073"},
        caller_ids: ["+15550101"],
    })
    expect(".o_phone_line_caption").toHaveCount(1)
    expect(".o_phone_line_caption").not.toHaveText(/5073/)
    await click(".o_phone_line_chip:last")
    await animationFrame()
    expect(".o_phone_line_how").toHaveText(/Rings your phone \.\.\.5073 first/)
    expect(".o_phone_line_caller_id_value").toHaveText("+15550101")
    expect(".o_phone_line_caller_id_picker").toHaveCount(0)
    expect(howLineConnects({media: "bridge", tokenData: {}}).toString()).toBe("Rings your phone first")
    expect(phone.state.selectedLineKey).toBe("sales")
})

test("a line with no caller ID shows none, and has no picker", async () => {
    await mountTwo()
    expect(".o_phone_line_caller_id").toHaveCount(0)
    expect(".o_phone_line_how").toHaveCount(1)
})

test("the caller ID a person picked is the one the dial carries", async () => {
    const dials = watchDials()
    const phone = await mountTwo({caller_ids: ["+15550101", "+15550102"]})
    await click(".o_phone_line_chip:last")
    await animationFrame()
    await click(".o_phone_line_caller_id_picker")
    await select("+15550102", {target: ".o_phone_line_caller_id_picker"})
    await animationFrame()
    phone.bus.trigger("busPhoneMakeCall", {phone: NUMBER})
    await waitUntil(() => dials.length > 0)
    expect(dials[0].params).toEqual({To: NUMBER, Called: NUMBER, CallerId: "+15550102"})
})

test("a broadcast names the call's line, only with two lines", async () => {
    const seen = []
    const listener = new BroadcastChannel("connect")
    listener.onmessage = ({data}) => seen.push(data)
    after(() => listener.close())
    const dials = watchDials()
    const phone = await mountTwo()
    await click(".o_phone_line_chip:last")
    await animationFrame()
    seen.length = 0
    phone.bus.trigger("busPhoneMakeCall", {phone: NUMBER})
    await waitUntil(() => dials.length > 0 && seen.some(({event}) => event === "tbcSync"))
    expect(seen.find(({event}) => event === "tbcSync").params.lineKey).toBe("sales")
    // Said after the call has left its line, and still about that line.
    phone.session = null
    phone.state.selectedLineKey = "desk"
    phone.bc.postMessage({event: "tbcEndCall"})
    await waitUntil(() => seen.some(({event}) => event === "tbcEndCall"))
    expect(seen.find(({event}) => event === "tbcEndCall").params).toEqual({lineKey: "sales"})
})

test("one line's broadcasts are what they have always been", async () => {
    const seen = []
    const listener = new BroadcastChannel("connect")
    listener.onmessage = ({data}) => seen.push(data)
    after(() => listener.close())
    const dials = watchDials()
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    const phone = await mountSingleLinePhone({token: "only"})
    await animationFrame()
    phone.bus.trigger("busPhoneMakeCall", {phone: NUMBER})
    await waitUntil(() => dials.length > 0 && seen.some(({event}) => event === "tbcSync"))
    phone.bc.postMessage({event: "tbcEndCall"})
    await waitUntil(() => seen.some(({event}) => event === "tbcEndCall"))
    for (const {params} of seen) {
        expect("lineKey" in (params || {})).toBe(false)
    }
    expect(seen.find(({event}) => event === "tbcEndCall").params).toBe(undefined)
})

test("another tab ending a call on another line leaves this tab's call alone", async () => {
    const phone = await mountTwo()
    const call = ring(phone.lines[0])
    await animationFrame()
    let disconnected = 0
    call.on("disconnect", () => disconnected++)
    phone.bc.onmessage({data: {event: "tbcEndCall", params: {lineKey: "sales"}}})
    await animationFrame()
    expect(disconnected).toBe(0)
    expect(phone.session).toBe(call)
    phone.bc.onmessage({data: {event: "tbcEndCall", params: {lineKey: "desk"}}})
    await animationFrame()
    expect(disconnected).toBe(1)
})

test("a tab showing another tab's call shows that call's line, then its own again", async () => {
    const phone = await mountTwo()
    phone.bc.onmessage({data: {event: "tbcSync", params: {
        id: 1, isPartner: false, phoneStatus: "connecting", callerId: {}, lineKey: "sales",
    }}})
    await animationFrame()
    expect(phone.activeLine.key).toBe("sales")
    expect(phone.state.selectedLineKey).toBe("desk")
    expect(".o_phone_line_chip.active").toHaveText("Sales")
    expect(".o_phone_line_chip:enabled").toHaveCount(0)
    phone.bc.onmessage({data: {event: "tbcEndCall", params: {lineKey: "sales"}}})
    await waitUntil(() => !phone.state.inCall)
    await animationFrame()
    expect(phone.activeLine.key).toBe("desk")
})

test("call control names the provider of the line the call is on", async () => {
    const phone = await mountTwo()
    const call = ring(phone.lines[1])
    await animationFrame()
    expect(phone._callControlPayload(call.params.CallSid, {step: "start"})).toEqual({
        provider: NEUTRAL, channel_sid: call.params.CallSid, step: "start",
    })
    // On hold on the first line, with the panel on another.
    const held = new LoopbackCall({From: "+15550100", Partner: "false", CallSid: "CAheld"})
    phone.lines[0]._calls.add(held)
    phone.heldSession = held
    expect(phone._callControlPayload("CAheld").provider).toBe("twilio")
    expect(phone._callControlPayload("CAunknown").provider).toBe(NEUTRAL)
})

test("a waiting call rings by its own line's flag", async () => {
    const ringing = class extends LoopbackTransport {
        static ringsInApp = true
    }
    const key = "ringing_provider"
    registry.category("connect.telephony_transports").add(key, ringing, {force: true})
    after(() => registry.category("connect.telephony_transports").remove(key))
    const phone = await mountNamed(makeLine("desk", "Desk"), makeLine("sales", "Sales", {provider: key}))
    const foreground = ring(phone.lines[0])
    await animationFrame()
    foreground.accept()
    phone.state.inIncoming = false
    expect(phone._ringsInApp()).toBe(false)
    ring(phone.lines[1])
    expect(phone.waitingSession).not.toBe(null)
    expect(phone._ringsInApp()).toBe(true)
})
