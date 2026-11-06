/** @odoo-module **/
"use strict"
/**
 * What happens when one line goes wrong. It stays that line's problem: the others
 * keep working, nothing moves to another line by itself, and the person is told.
 */

import {after, animationFrame, describe, expect, test} from "@odoo/hoot"
import {click, waitUntil} from "@odoo/hoot-dom"
import {Deferred} from "@odoo/hoot-mock"
import {onRpc, patchWithCleanup} from "@web/../tests/web_test_helpers"
import {browser} from "@web/core/browser/browser"
import {registry} from "@web/core/registry"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {PhoneField} from "@web/views/fields/phone/phone_field"
import {saveSelectedLine} from "@connect/components/phone/line_selector/line_selector"
import {LoopbackCall, LoopbackTransport, registerLoopbackProvider} from "@connect/../tests/helpers/loopback_transport"
import {bridgeLine, installFakeBridgeProvider, publish} from "@connect/../tests/unit/helpers/fake_bridge_provider"
import {SINGLE_LINE_NONCE, mountLines} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const NUMBER = "+15550199"
const STORAGE_KEY = "connect_phone_line"
const UNCONFIRMED = "We could not confirm this call. It may still ring. Do not redial yet."
const TRANSPORTS = registry.category("connect.telephony_transports")

const browserLine = (extra = {}) => ({
    key: "twilio",
    tool_id: 7,
    label: "Desk",
    provider: "twilio",
    media: "browser",
    capabilities: {},
    caller_ids: [],
    token_data: {token: "t", first_callflow: "user"},
    ...extra,
})
const bridgeB = () => bridgeLine({key: "bridge:2", tool_id: 42, label: "Plan B"})
/** A line whose credential could not be had, as the server sends it. */
const failedLine = (words = "The plan's server did not answer.") => bridgeLine({
    media: "browser",
    token_data: {},
    unavailable: words,
})

function register(key, Transport) {
    TRANSPORTS.add(key, Transport, {force: true})
    after(() => TRANSPORTS.remove(key))
}

/** Forgets the choice afterwards, because storage outlives the test. */
function remember(key) {
    saveSelectedLine(key)
    after(() => browser.localStorage.removeItem(STORAGE_KEY))
}

async function mount(lines, options) {
    const requests = installFakeBridgeProvider(options)
    const phone = await mountLines(lines)
    await animationFrame()
    return {phone, requests}
}

async function dial(phone, requests) {
    const before = requests.originates.length
    await phone.prepareCall({phone: NUMBER})
    await waitUntil(() => requests.originates.length > before)
    await animationFrame()
    return phone.session
}

async function settleEnd() {
    // endCall lets go of inCall on a short timer.
    await new Promise((resolve) => setTimeout(resolve, 150))
    await animationFrame()
}

function bannerText() {
    return document.querySelector(".connect-status-banner.error")?.textContent ?? ""
}

// A surviving contexted line

test("a line minted under a tool refreshes under it, even as the only line", async () => {
    const seen = []
    onRpc("connect.user", "get_client_token", ({kwargs}) => {
        seen.push(kwargs.context?.connect_tool_id ?? null)
        return kwargs.context?.connect_tool_id === 7 ? {token: "fresh"} : {token: false}
    })
    const {phone} = await mount([browserLine({mint_tool_id: 7})])
    const [line] = phone.lines
    await line.updateToken()
    expect(line.tokenData.token).toBe("fresh")
    await line.reconnect()
    await animationFrame()
    expect(seen).toEqual([7, 7])
    expect(line.hasLiveTransport).toBe(true)
    expect(phone.state.connectionStatus).toBe("ready")
})

test("a line that knows its tool but was minted with none still sends only the nonce", async () => {
    const sent = []
    onRpc("connect.user", "get_client_token", ({kwargs}) => {
        sent.push(Object.keys(kwargs).filter((key) => key !== "context").concat("connect_tool_id" in (kwargs.context || {}) ? ["tool"] : []))
        return {token: "fresh"}
    })
    const {phone} = await mount([browserLine()])
    await phone.lines[0].updateToken()
    expect(sent).toEqual([["nonce"]])
})

// Click-to-call follows the From selection

function clickToCall(phone, record = NUMBER) {
    const calls = []
    // What mountPhoneLines does, which these tests mount around.
    phone.bus.browserSessionNonce = phone.browserSessionNonce
    patchWithCleanup(window, {connectBus: phone.bus})
    const proto = PhoneField.prototype
    proto._onClickCallButton.call({
        props: {name: "phone", record: {data: {phone: record}, model: {config: {resModel: "res.partner", resId: 1}}}},
        env: {model: {orm: {call: (...args) => {
            calls.push(args)
            return Promise.resolve(true)
        }}}},
        _originateCall: proto._originateCall,
    }, {preventDefault() {}})
    return calls
}

/** Records each browser dial. */
function watchBrowserDials() {
    const dials = []
    patchWithCleanup(LoopbackTransport.prototype, {
        async dial(params) {
            dials.push({params: {...params}})
            return super.dial(params)
        },
    })
    return dials
}

test("a click with a bridge line selected goes through the panel, on that line", async () => {
    remember("bridge:1")
    const {phone, requests} = await mount([browserLine(), bridgeLine()])
    expect(phone.activeLine.key).toBe("bridge:1")
    const direct = clickToCall(phone)
    await waitUntil(() => requests.originates.length === 1)
    expect(direct).toEqual([])
    expect(requests.originates[0].kwargs.context.connect_tool_id).toBe(41)
    expect(requests.originates[0].kwargs.idempotency_key).toMatch(/^[0-9a-f]{48}$/)
})

test("a click with a client line selected behind a bridge line dials in this browser", async () => {
    remember("twilio")
    const dials = watchBrowserDials()
    const {phone, requests} = await mount([
        bridgeLine(),
        browserLine({token_data: {token: "t", first_callflow: "client"}}),
    ])
    expect(phone.activeLine.key).toBe("twilio")
    const direct = clickToCall(phone)
    await waitUntil(() => dials.length === 1)
    expect(direct).toEqual([])
    expect(requests.originates).toEqual([])
})

test("a click with a line other than the first selected goes through the panel", async () => {
    remember("other")
    register("other", LoopbackTransport)
    const dials = watchBrowserDials()
    const {phone} = await mount([
        browserLine(),
        browserLine({key: "other", tool_id: 8, provider: "other", label: "Other"}),
    ])
    const direct = clickToCall(phone)
    await waitUntil(() => dials.length === 1)
    expect(direct).toEqual([])
    expect(phone.state.foregroundLineKey ?? phone.activeLine.key).toBe("other")
})

test("the server path carries the tool of the first line, when there are two", async () => {
    remember("twilio")
    const {phone, requests} = await mount([browserLine(), bridgeLine()])
    const direct = clickToCall(phone)
    expect(direct.length).toBe(1)
    const [model, method, args, kwargs] = direct[0]
    expect([model, method, args]).toEqual(["connect.settings", "originate_call", [NUMBER, "res.partner", 1]])
    expect(kwargs.origin_nonce).toBe(SINGLE_LINE_NONCE)
    expect(kwargs.context).toEqual({connect_tool_id: 7})
    expect(requests.originates).toEqual([])
})

test("the click reads the selection when it happens, not at mount", async () => {
    remember("twilio")
    const {phone, requests} = await mount([browserLine(), bridgeLine()])
    expect(clickToCall(phone).length).toBe(1)
    phone.selectLine("bridge:1")
    expect(clickToCall(phone)).toEqual([])
    await waitUntil(() => requests.originates.length === 1)
})

test("one line clicks as it always has", async () => {
    const {phone} = await mount([browserLine({tool_id: 7})])
    const direct = clickToCall(phone)
    expect(direct).toEqual([[
        "connect.settings", "originate_call", [NUMBER, "res.partner", 1], {origin_nonce: SINGLE_LINE_NONCE},
    ]])
    expect(phone.bus.clickToCallLocal).toBe(false)
})

test("one client line dials in this browser, as it always has", async () => {
    const dials = watchBrowserDials()
    const {phone} = await mount([browserLine({token_data: {token: "t", first_callflow: "client"}})])
    expect(clickToCall(phone)).toEqual([])
    await waitUntil(() => dials.length === 1)
    expect(dials[0].params).toEqual({To: NUMBER, Called: NUMBER})
})

test("the only line left, minted under its tool, clicks under that tool", async () => {
    const {phone} = await mount([browserLine({mint_tool_id: 7})])
    const direct = clickToCall(phone)
    expect(direct[0][3].context).toEqual({connect_tool_id: 7})
})

// Reconnect and a bridge line's call

test("a reconnect leaves a live bridge call in place, and it ends normally", async () => {
    const {phone, requests} = await mount([bridgeLine()])
    const call = await dial(phone, requests)
    publish(phone, call.ref, "in_call")
    await animationFrame()
    const transport = phone.lines[0].transport

    await phone._reconnect()
    await animationFrame()
    expect(phone.lines[0].transport).toBe(transport)
    expect(phone.session).toBe(call)
    expect(phone.state.connectionStatus).toBe("ready")

    publish(phone, call.ref, "ended", {reason_code: "normal"})
    await animationFrame()
    expect(call.state).toBe("ended")
    expect(phone.session).toBe(null)
    await settleEnd()
    expect(phone.state.inCall).toBe(false)
})

test("a reconnect keeps a call nobody can confirm, its refusal and its clear", async () => {
    const {phone, requests} = await mount([bridgeLine()])
    const call = await dial(phone, requests)
    publish(phone, call.ref, "unknown")
    await animationFrame()
    expect(phone.lines[0].transport.dialRefusal).toBe(UNCONFIRMED)

    await phone._reconnect()
    expect(phone.lines[0].transport.dialRefusal).toBe(UNCONFIRMED)
    expect(call._timer).not.toBe(null)
})

test("the network coming back leaves the bridge call too, and the banner goes", async () => {
    const {phone, requests} = await mount([bridgeLine()])
    const call = await dial(phone, requests)
    publish(phone, call.ref, "in_call")
    await animationFrame()
    window.dispatchEvent(new Event("offline"))
    await animationFrame()
    expect(phone.state.connectionStatus).toBe("offline")
    window.dispatchEvent(new Event("online"))
    await waitUntil(() => phone.state.connectionStatus === "ready")
    expect(phone.session).toBe(call)
})

test("a reconnect still rebuilds a browser line beside the bridge line", async () => {
    onRpc("connect.user", "get_client_token", () => ({token: "fresh"}))
    const {phone} = await mount([browserLine(), bridgeLine()])
    const [browserOne, bridgeOne] = phone.lines
    const browserTransport = browserOne.transport
    const bridgeTransport = bridgeOne.transport
    await phone._reconnect()
    await animationFrame()
    expect(browserOne.transport).not.toBe(browserTransport)
    expect(bridgeOne.transport).toBe(bridgeTransport)
})

// A line that cannot start

class ThrowsOnInit extends LoopbackTransport {
    init() {
        throw new Error("Device would not start")
    }
}

class SdkWillNotLoad extends LoopbackTransport {
    static async loadDependencies() {
        throw new Error("The phone library did not load")
    }
}

test("a line whose start throws costs only that line", async () => {
    patchWithCleanup(console, {error() {}})
    register("throws", ThrowsOnInit)
    const {phone, requests} = await mount([browserLine({key: "throws", provider: "throws"}), bridgeLine()])
    const [broken, bridge] = phone.lines
    expect(phone.state.isActive).toBe(true)
    expect(bridge.hasLiveTransport).toBe(true)
    expect(broken.hasLiveTransport).toBe(false)
    expect(bannerText()).toInclude("Device would not start")

    phone.selectLine("bridge:1")
    await animationFrame()
    expect(bannerText()).toBe("")
    await dial(phone, requests)
    expect(requests.originates.length).toBe(1)
})

test("no WebRTC stops a browser line, and a bridge line works on", async () => {
    patchWithCleanup(console, {error() {}})
    const original = Object.getOwnPropertyDescriptor(navigator, "mediaDevices")
    Object.defineProperty(navigator, "mediaDevices", {value: undefined, configurable: true})
    after(() => {
        if (original) {
            Object.defineProperty(navigator, "mediaDevices", original)
        } else {
            delete navigator.mediaDevices
        }
    })
    const {phone} = await mount([browserLine(), bridgeLine()])
    const [webrtc, bridge] = phone.lines
    expect(phone.state.isActive).toBe(true)
    expect(webrtc.hasLiveTransport).toBe(false)
    expect(bridge.hasLiveTransport).toBe(true)
    expect(bannerText()).toInclude("does not support WebRTC")
})

test("a library that will not load costs only its line", async () => {
    patchWithCleanup(console, {error() {}})
    register("no_sdk", SdkWillNotLoad)
    const {phone} = await mount([browserLine({key: "no_sdk", provider: "no_sdk"}), bridgeLine()])
    expect(".o_root_connect_phone").toHaveCount(1)
    expect(phone.state.isActive).toBe(true)
    expect(phone.lines[1].hasLiveTransport).toBe(true)
    expect(bannerText()).toInclude("The phone library did not load")
})

test("the phone stops only when no line can work", async () => {
    patchWithCleanup(console, {error() {}})
    register("throws", ThrowsOnInit)
    register("no_sdk", SdkWillNotLoad)
    const {phone} = await mount([
        browserLine({key: "throws", provider: "throws"}),
        browserLine({key: "no_sdk", provider: "no_sdk", tool_id: 8}),
    ])
    expect(phone.state.isActive).toBe(false)
})

test("a line that cannot start refuses a dial in words, and no other line takes it", async () => {
    patchWithCleanup(console, {error() {}})
    register("throws", ThrowsOnInit)
    const {phone, requests} = await mount([browserLine({key: "throws", provider: "throws"}), bridgeLine()])
    await phone.prepareCall({phone: NUMBER})
    await animationFrame()
    expect(requests.originates).toEqual([])
    expect(phone.state.lastOperationError).toInclude("Desk")
    expect(phone.state.selectedLineKey).toBe("throws")
})

test("Retry starts a line that failed to start again", async () => {
    patchWithCleanup(console, {error() {}})
    onRpc("connect.user", "get_client_token", () => ({token: "fresh"}))
    let failing = true
    class FailsOnce extends LoopbackTransport {
        init() {
            if (failing) {
                throw new Error("Device would not start")
            }
            super.init()
        }
    }
    register("once", FailsOnce)
    const {phone} = await mount([browserLine({key: "once", provider: "once"})])
    expect(phone.state.isActive).toBe(false)
    failing = false
    await phone._reconnect()
    await animationFrame()
    expect(phone.state.isActive).toBe(true)
    expect(phone.lines[0].hasLiveTransport).toBe(true)
    expect(phone.state.connectionStatus).toBe("ready")
})

// The chosen line failed to mint

test("a chosen line that failed to mint stays chosen, is marked, and says why", async () => {
    remember("bridge:1")
    const {phone} = await mount([browserLine(), failedLine()])
    expect(phone.isMultiLine).toBe(true)
    expect(phone.activeLine.key).toBe("bridge:1")
    expect(browser.localStorage.getItem(STORAGE_KEY)).toBe("bridge:1")
    expect(".o_phone_line_chip").toHaveCount(2)
    expect(".o_phone_line_chip.active.o_phone_line_down").toHaveCount(1)
    expect(".o_phone_line_problem").toHaveText("The plan's server did not answer.")
    expect(bannerText()).toInclude("The plan's server did not answer.")
})

test("dialing on a line that failed to mint refuses with the reason", async () => {
    remember("bridge:1")
    const dials = watchBrowserDials()
    const {phone, requests} = await mount([browserLine(), failedLine()])
    await phone.prepareCall({phone: NUMBER})
    await animationFrame()
    expect(dials).toEqual([])
    expect(requests.originates).toEqual([])
    expect(phone.state.inCall).toBe(false)
    expect(phone.state.lastOperationError).toInclude("The plan's server did not answer.")
    expect(phone.state.lastOperationError).toInclude("Sales plan")
    expect(phone.state.selectedLineKey).toBe("bridge:1")
})

test("a click-to-call on it refuses too, with the panel open to say so", async () => {
    remember("bridge:1")
    const {phone, requests} = await mount([browserLine(), failedLine()])
    expect(phone.state.isDisplay).toBe(false)
    expect(clickToCall(phone)).toEqual([])
    await waitUntil(() => phone.state.lastOperationError)
    await animationFrame()
    expect(requests.originates).toEqual([])
    expect(phone.state.isDisplay).toBe(true)
    expect(".connect-operation-error").toHaveCount(1)
})

test("the person changes line themselves, and the choice is theirs", async () => {
    remember("bridge:1")
    const dials = watchBrowserDials()
    const {phone} = await mount([browserLine(), failedLine()])
    phone.state.isDisplay = true
    await animationFrame()
    await click(".o_phone_line_chip:first")
    await animationFrame()
    expect(phone.activeLine.key).toBe("twilio")
    expect(browser.localStorage.getItem(STORAGE_KEY)).toBe("twilio")
    expect(bannerText()).toBe("")
    await phone.prepareCall({phone: NUMBER})
    await waitUntil(() => dials.length === 1)
})

test("a line that failed to mint and is not chosen leaves the chosen one alone", async () => {
    const {phone} = await mount([browserLine(), failedLine()])
    expect(phone.activeLine.key).toBe("twilio")
    expect(phone.state.connectionStatus).toBe("ready")
    expect(".o_phone_line_chip.active.o_phone_line_down").toHaveCount(0)
    expect(".o_phone_line_chip.o_phone_line_down").toHaveCount(1)
    expect(document.querySelector(".o_phone_line_chip.o_phone_line_down").title).toInclude("The plan's server did not answer.")
})

test("four lines mark the one that is down in the dropdown", async () => {
    await mount([
        browserLine(), failedLine(), bridgeB(), bridgeLine({key: "bridge:3", tool_id: 43, label: "Plan C"}),
    ])
    expect(document.querySelector(".o_phone_line_dropdown option[value='bridge:1']").textContent).toInclude("not available")
    expect(document.querySelector(".o_phone_line_dropdown option[value='bridge:2']").textContent).not.toInclude("not available")
})

test("a person whose lines all fail sees the error banner", async () => {
    const {phone} = await mount([failedLine("No credential today")])
    expect(phone.state.connectionStatus).toBe("error")
    expect(phone.state.isActive).toBe(true)
    expect(bannerText()).toInclude("No credential today")
    expect(phone.lines[0].transport).toBe(null)
    await phone.prepareCall({phone: NUMBER})
    expect(phone.state.lastOperationError).toInclude("No credential today")
    expect(phone.state.inCall).toBe(false)
})

test("Retry brings a line back when its credential can be had", async () => {
    onRpc("connect.user", "get_client_token", ({kwargs}) => {
        expect(kwargs.context.connect_tool_id).toBe(41)
        return {media: "bridge", label: "Sales plan", caller_ids: ["+15550100"]}
    })
    const {phone} = await mount([browserLine(), failedLine()])
    const line = phone.lines[1]
    line.mintToolId = 41
    await line.reconnect()
    await animationFrame()
    expect(line.unavailable).toBe("")
    expect(line.media).toBe("bridge")
    expect(line.callerIds).toEqual(["+15550100"])
    expect(line.hasLiveTransport).toBe(true)
    expect(".o_phone_line_chip.o_phone_line_down").toHaveCount(0)
})

test("a mint error on a line that still works reaches the panel", async () => {
    installFakeBridgeProvider()
    const phone = await mountLines([browserLine()], {mintError: "That credential is about to run out."})
    await animationFrame()
    expect(phone.state.lastOperationError).toBe("That credential is about to run out.")
})

test("a remembered line that has gone is said once the panel shows", async () => {
    remember("gone")
    const {phone} = await mount([browserLine(), bridgeLine()])
    expect(phone.activeLine.key).toBe("twilio")
    expect(phone.state.lastOperationError).toInclude("Desk")
    expect(browser.localStorage.getItem(STORAGE_KEY)).toBe("gone")
})

// A failed one-time redial

class RejectsDial extends LoopbackTransport {
    async dial() {
        throw new Error("dial refused by device")
    }
}

test("a failed redial on another line cannot stick", async () => {
    register("rejects", RejectsDial)
    remember("bridge:1")
    const {phone, requests} = await mount([
        bridgeLine(),
        browserLine({key: "rejects", provider: "rejects", label: "Desk"}),
    ])
    await phone.makeCall({phone: NUMBER, lineKey: "rejects"})
    await settleEnd()
    expect(phone.state.foregroundLineKey).toBe(null)
    expect(phone.state.inCall).toBe(false)
    expect(phone.activeLine.key).toBe("bridge:1")
    expect(phone.state.selectedLineKey).toBe("bridge:1")
    expect(phone.state.lastOperationError).toInclude("dial refused by device")
    await dial(phone, requests)
    expect(requests.originates[0].kwargs.context.connect_tool_id).toBe(41)
})

test("one line's failed dial still rejects, as it always has", async () => {
    register("rejects", RejectsDial)
    const {phone} = await mount([browserLine({key: "rejects", provider: "rejects"})])
    let message = ""
    await phone.makeCall({phone: NUMBER}).catch((error) => {
        message = error.message
    })
    expect(message).toBe("dial refused by device")
    expect(phone.state.foregroundLineKey).toBe(null)
})

// The in-call check is global

test("two presses on two lines inside the partner lookup place one call", async () => {
    const {phone, requests} = await mount([bridgeLine(), bridgeB()])
    const presses = [
        phone.prepareCall({phone: NUMBER, lineKey: "bridge:1"}),
        phone.prepareCall({phone: "+15550198", lineKey: "bridge:2"}),
    ]
    await Promise.all(presses)
    await waitUntil(() => requests.originates.length === 1)
    await animationFrame()
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(requests.originates.length).toBe(1)
    expect(requests.originates[0].number).toBe(NUMBER)
    expect(phone.state.foregroundLineKey).toBe("bridge:1")
    expect(phone.lines[0].session).not.toBe(null)
    expect(phone.state.callPhoneNumber).toBe(NUMBER)
    expect(phone.state.lastOperationError).toInclude("already")
})

test("a call that rings during the lookup stops the dial", async () => {
    const lookup = new Deferred()
    const requests = installFakeBridgeProvider()
    // Only the press's lookup hangs: the ring does its own and goes on.
    let lookups = 0
    onRpc("res.partner", "api_get_partner", () => (lookups++ ? false : lookup))
    registerLoopbackProvider("other")
    after(() => TRANSPORTS.remove("other"))
    const phone = await mountLines([bridgeLine(), browserLine({key: "other", provider: "other"})])
    await animationFrame()
    const press = phone.prepareCall({phone: NUMBER})
    await animationFrame()
    const call = new LoopbackCall({From: "+15550100", Partner: "false"})
    phone.lines[1].transport._emit("incoming", call)
    await animationFrame()
    expect(phone.state.inCall).toBe(true)
    lookup.resolve(false)
    await press
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(requests.originates).toEqual([])
    expect(phone.session).toBe(call)
})

test("a second press of the same number stays silent", async () => {
    const {phone, requests} = await mount([bridgeLine(), bridgeB()])
    await Promise.all([phone.prepareCall({phone: NUMBER}), phone.prepareCall({phone: NUMBER})])
    await waitUntil(() => requests.originates.length === 1)
    expect(phone.state.lastOperationError).toBe(false)
})

// The refusal is phone-wide

test("a call nobody can confirm on one line stops a dial on another", async () => {
    const {phone, requests} = await mount([bridgeLine(), bridgeB()])
    const call = await dial(phone, requests)
    publish(phone, call.ref, "unknown")
    await animationFrame()
    await phone._onClickEndCall()
    await settleEnd()
    phone.selectLine("bridge:2")
    await phone.prepareCall({phone: NUMBER})
    await animationFrame()
    expect(requests.originates.length).toBe(1)
    expect(phone.state.lastOperationError).toBe(UNCONFIRMED)
})

test("a hang-up that fails after a quick redial refuses the next one, and says so", async () => {
    const hangup = new Deferred()
    const {phone, requests} = await mount([bridgeLine()], {hangup: () => hangup})
    const first = await dial(phone, requests)
    publish(phone, first.ref, "in_call")
    await animationFrame()
    await phone._onClickEndCall()
    await settleEnd()
    await waitUntil(() => requests.hangups.length === 1)
    const second = await dial(phone, requests)
    expect(second).not.toBe(first)

    hangup.reject(new Error("network gone"))
    await new Promise((resolve) => setTimeout(resolve, 10))
    await animationFrame()
    expect(first.state).toBe("unknown")
    expect(phone.lines[0].transport.dialRefusal).toBe(UNCONFIRMED)
    expect(phone.state.lastOperationError).toBe(UNCONFIRMED)
    expect(phone.session).toBe(second)
})

test("an event for the older call still settles it, and lifts the refusal", async () => {
    const hangup = new Deferred()
    const {phone, requests} = await mount([bridgeLine()], {hangup: () => hangup})
    const first = await dial(phone, requests)
    publish(phone, first.ref, "in_call")
    await animationFrame()
    await phone._onClickEndCall()
    await settleEnd()
    await waitUntil(() => requests.hangups.length === 1)
    const second = await dial(phone, requests)
    hangup.reject(new Error("network gone"))
    await new Promise((resolve) => setTimeout(resolve, 10))
    publish(phone, first.ref, "ended", {reason_code: "normal"})
    await animationFrame()
    expect(first.terminal).toBe(true)
    expect(phone.lines[0].transport.dialRefusal).toBe(null)
    expect(phone.session).toBe(second)
})

// Status per line

test("an error on a line not selected leaves the banner to the selected line", async () => {
    patchWithCleanup(console, {error() {}})
    remember("bridge:1")
    const {phone} = await mount([browserLine(), bridgeLine()])
    expect(phone.state.connectionStatus).toBe("ready")
    phone.lines[0].transport._emit("error", {name: "x", message: "line two broke"})
    await animationFrame()
    expect(phone.state.connectionStatus).toBe("ready")
    expect(bannerText()).toBe("")
    expect(".o_phone_line_chip:first").toHaveClass("o_phone_line_down")
    expect(document.querySelector(".o_phone_line_chip").title).toInclude("line two broke")

    phone.selectLine("twilio")
    await animationFrame()
    expect(phone.state.connectionStatus).toBe("error")
    expect(bannerText()).toInclude("line two broke")
    expect(".o_phone_line_problem").toHaveText("line two broke")

    phone.selectLine("bridge:1")
    await animationFrame()
    expect(phone.state.connectionStatus).toBe("ready")
    expect(".o_phone_line_problem").toHaveCount(0)
})

test("a line coming up does not lift another line's failure", async () => {
    patchWithCleanup(console, {error() {}})
    const {phone} = await mount([browserLine(), bridgeLine()])
    phone.lines[0].transport._emit("error", {name: "x", message: "line one broke"})
    await animationFrame()
    expect(phone.state.connectionStatus).toBe("error")
    await phone.lines[1].transport.register()
    await animationFrame()
    expect(phone.state.connectionStatus).toBe("error")
    expect(bannerText()).toInclude("line one broke")
})

test("one line connecting does not stop another refreshing", async () => {
    let mints = 0
    onRpc("connect.user", "get_client_token", () => {
        mints++
        return {token: "fresh"}
    })
    const {phone} = await mount([browserLine({mint_tool_id: 7}), browserLine({
        key: "other", tool_id: 8, mint_tool_id: 8, provider: "other",
    })].map((line) => {
        registerLoopbackProvider("other")
        return line
    }))
    after(() => TRANSPORTS.remove("other"))
    phone._setLineStatus(phone.lines[0], "connecting")
    await phone.lines[1].updateToken()
    expect(mints).toBe(1)
    await phone.lines[0].updateToken()
    expect(mints).toBe(1)
})

test("a call on another line shows that line's status while it lasts", async () => {
    patchWithCleanup(console, {error() {}})
    remember("bridge:1")
    registerLoopbackProvider("other")
    after(() => TRANSPORTS.remove("other"))
    const {phone} = await mount([browserLine({key: "other", provider: "other"}), bridgeLine()])
    phone.lines[0].transport._emit("error", {name: "x", message: "that line is poorly"})
    await animationFrame()
    expect(phone.state.connectionStatus).toBe("ready")
    const call = new LoopbackCall({From: "+15550100", Partner: "false"})
    phone.lines[0].transport._emit("incoming", call)
    await animationFrame()
    expect(phone.activeLine.key).toBe("other")
    expect(phone.state.connectionStatus).toBe("error")
})

// A click the server would place answers to the same rules

/** A call on the bridge line, with the selection left on the line before it. */
async function redialOnBridge(phone, requests) {
    await phone.prepareCall({phone: NUMBER, lineKey: "bridge:1"})
    await waitUntil(() => requests.originates.length === 1)
    await animationFrame()
    return phone.session
}

test("a click over a call nobody can confirm refuses, on a line the server would call from", async () => {
    const {phone, requests} = await mount([browserLine(), bridgeLine()])
    phone.selectLine("bridge:1")
    const call = await dial(phone, requests)
    publish(phone, call.ref, "unknown")
    await animationFrame()
    await phone._onClickEndCall()
    await settleEnd()
    phone.selectLine("twilio")
    phone.state.lastOperationError = false
    expect(clickToCall(phone)).toEqual([])
    await waitUntil(() => phone.state.lastOperationError)
    expect(phone.state.lastOperationError).toBe(UNCONFIRMED)
    expect(phone.state.isDisplay).toBe(true)
    expect(requests.originates.length).toBe(1)
})

test("a click during a call on another line places nothing, and says so", async () => {
    const {phone, requests} = await mount([browserLine(), bridgeLine()])
    const call = await redialOnBridge(phone, requests)
    publish(phone, call.ref, "in_call")
    await animationFrame()
    expect(phone.state.selectedLineKey).toBe("twilio")
    expect(clickToCall(phone, "+15550198")).toEqual([])
    await waitUntil(() => phone.state.lastOperationError)
    expect(phone.state.lastOperationError).toInclude("already")
    expect(requests.originates.length).toBe(1)
    expect(phone.session).toBe(call)
})

test("a second click of the number on the call places nothing either", async () => {
    const {phone, requests} = await mount([browserLine(), bridgeLine()])
    const call = await redialOnBridge(phone, requests)
    publish(phone, call.ref, "in_call")
    await animationFrame()
    expect(clickToCall(phone)).toEqual([])
    await animationFrame()
    expect(requests.originates.length).toBe(1)
    expect(phone.state.lastOperationError).toBe(false)
})

test("once nothing stands in the way, the click goes to the server again", async () => {
    const {phone, requests} = await mount([browserLine(), bridgeLine()])
    phone.selectLine("bridge:1")
    const call = await dial(phone, requests)
    publish(phone, call.ref, "unknown")
    await animationFrame()
    await phone._onClickEndCall()
    await settleEnd()
    publish(phone, call.ref, "ended")
    await animationFrame()
    phone.selectLine("twilio")
    const calls = clickToCall(phone)
    expect(calls.length).toBe(1)
    expect(calls[0][3].context).toEqual({connect_tool_id: 7})
})

test("one line clicks to the server during a call, as it always has", async () => {
    const {phone} = await mount([browserLine()])
    await phone.prepareCall({phone: NUMBER})
    await animationFrame()
    expect(phone.state.inCall).toBe(true)
    expect(clickToCall(phone, "+15550198").length).toBe(1)
})

// Retry on a line that could not mint

class NeedsItsLibrary extends LoopbackTransport {
    static loads = 0
    static async loadDependencies() {
        this.loads++
    }
    init() {
        if (!NeedsItsLibrary.loads) {
            throw new Error("Library is not defined")
        }
        super.init()
    }
}

test("a line that could not mint loads its library when Retry succeeds", async () => {
    NeedsItsLibrary.loads = 0
    register("library", NeedsItsLibrary)
    onRpc("connect.user", "get_client_token", () => ({token: "fresh"}))
    const failed = browserLine({key: "library", provider: "library", token_data: {}, unavailable: "No credential today"})
    const {phone} = await mount([failed])
    const [line] = phone.lines
    expect(NeedsItsLibrary.loads).toBe(0)
    await line.reconnect()
    await animationFrame()
    expect(NeedsItsLibrary.loads).toBe(1)
    expect(line.startFailed).toBe(false)
    expect(line.unavailable).toBe("")
    expect(line.hasLiveTransport).toBe(true)
    expect(phone.state.connectionStatus).toBe("ready")
    await line.reconnect()
    await animationFrame()
    expect(NeedsItsLibrary.loads).toBe(1)
})

test("a library that will not load on that Retry says so, on that line only", async () => {
    patchWithCleanup(console, {error() {}})
    register("noload", SdkWillNotLoad)
    onRpc("connect.user", "get_client_token", () => ({token: "fresh"}))
    const failed = browserLine({key: "noload", provider: "noload", token_data: {}, unavailable: "No credential today"})
    const {phone} = await mount([bridgeLine(), failed])
    const line = phone.lines[1]
    await line.reconnect()
    await animationFrame()
    expect(line.startFailed).toBe(true)
    expect(phone.state.lineStatuses.noload.error).toBe("The phone library did not load")
    expect(phone.lines[0].hasLiveTransport).toBe(true)
    expect(phone.state.isActive).toBe(true)
})

test("Retry on a line still failing says the server's reason, and refuses with it", async () => {
    onRpc("connect.user", "get_client_token", () => ({token: false, error: "Still no credential"}))
    const {phone} = await mount([browserLine(), failedLine()])
    phone.selectLine("bridge:1")
    const line = phone.lines[1]
    await line.reconnect()
    await animationFrame()
    expect(phone.state.connectionStatus).toBe("error")
    expect(bannerText()).toInclude("Still no credential")
    expect(bannerText()).not.toInclude("fake_bridge")
    await phone.prepareCall({phone: NUMBER})
    expect(phone.state.lastOperationError).toInclude("Still no credential")
})

test("Retry that gets nothing at all still names no provider", async () => {
    onRpc("connect.user", "get_client_token", () => ({token: false}))
    const {phone} = await mount([browserLine(), failedLine()])
    phone.selectLine("bridge:1")
    await phone.lines[1].reconnect()
    await animationFrame()
    expect(bannerText()).toInclude("Could not obtain a phone token.")
    expect(bannerText()).not.toInclude("fake_bridge")
})

test("one line's Retry keeps the words it always had", async () => {
    onRpc("connect.user", "get_client_token", () => ({error: "internal-host refused for key SK123"}))
    const {phone} = await mount([browserLine()])
    await phone.lines[0].reconnect()
    await animationFrame()
    expect(phone.state.connectionStatus).toBe("error")
    expect(bannerText()).toInclude("Could not obtain a phone token.")
    expect(bannerText()).not.toInclude("SK123")
})

// Presence belongs to the person

test("a line coming back during a call leaves the person on the call", async () => {
    onRpc("connect.user", "get_client_token", () => ({token: "fresh"}))
    const {phone, requests} = await mount([browserLine(), bridgeLine()])
    phone.selectLine("bridge:1")
    const call = await dial(phone, requests)
    publish(phone, call.ref, "in_call")
    await animationFrame()
    expect(phone._presenceDesired).toBe("on_call")
    window.dispatchEvent(new Event("offline"))
    window.dispatchEvent(new Event("online"))
    await waitUntil(() => phone.lines[0].registered && phone.state.lineStatuses.twilio.status === "ready")
    await animationFrame()
    expect(phone._presenceDesired).toBe("on_call")
    expect(phone.session).toBe(call)
    await phone._onClickEndCall()
    await settleEnd()
    expect(phone._presenceDesired).toBe("available")
})

test("a line coming up with no call still says available", async () => {
    const {phone} = await mount([browserLine(), bridgeLine()])
    phone._presenceDesired = "offline"
    phone.lines[0].transport._emit("registered")
    expect(phone._presenceDesired).toBe("available")
})

test("one line registering during its call does as it always has", async () => {
    const {phone} = await mount([browserLine()])
    await phone.prepareCall({phone: NUMBER})
    await animationFrame()
    phone._presenceDesired = "on_call"
    phone.lines[0].transport._emit("registered")
    expect(phone._presenceDesired).toBe("available")
})
