/** @odoo-module **/
"use strict"
/**
 * Phone builds one line today. These tests give it a second by hand, over the
 * loopback under another provider key, because what they pin only shows with two:
 * an event acts on the line whose transport fired it, and the limit of one
 * foreground call plus one waiting call counts every line.
 */

import {advanceTime, after, animationFrame, describe, expect, freezeTime, test} from "@odoo/hoot"
import {onRpc, patchWithCleanup} from "@web/../tests/web_test_helpers"
import {registry} from "@web/core/registry"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {PhoneLine} from "@connect/components/phone/phone/phone_line"
import {
    LoopbackCall,
    LoopbackTransport,
    registerLoopbackProvider,
} from "@connect/../tests/helpers/loopback_transport"
import {mountSingleLinePhone} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const SECOND = "second_provider"
const TOKEN_DATA = {token: "first-token", edge: "ashburn"}

async function mountTwoLines() {
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    registerLoopbackProvider(SECOND)
    after(() => registry.category("connect.telephony_transports").remove(SECOND))
    const phone = await mountSingleLinePhone(TOKEN_DATA)
    const [first] = phone.lines
    const second = new PhoneLine(phone, {
        key: SECOND,
        provider: SECOND,
        tokenData: {token: "second-token"},
    })
    phone.lines.push(second)
    second.initTransport()
    await animationFrame()
    return {phone, first, second}
}

/** Rings on a line, and returns the call with the count of what it was told to do. */
function ring(line) {
    const call = new LoopbackCall({From: "+15550100", Partner: "false"})
    const seen = {accept: 0, reject: 0, ignore: 0}
    for (const name of Object.keys(seen)) {
        call.on(name, () => seen[name]++)
    }
    line.transport._emit("incoming", call)
    return {call, seen}
}

/** Replaces a line's recovery methods, and returns the list of those called. */
function watchRecovery(...lines) {
    const calls = []
    for (const line of lines) {
        line.updateToken = () => calls.push(`${line.key}.updateToken`)
        line.reconnect = () => calls.push(`${line.key}.reconnect`)
    }
    return calls
}

test("a phone builds one line, and its getters read that line", async () => {
    const phone = await mountSingleLinePhone(TOKEN_DATA)
    await animationFrame()
    expect(phone.lines.length).toBe(1)
    const [line] = phone.lines
    expect(line.transport).toBeInstanceOf(LoopbackTransport)
    expect(phone.transport).toBe(line.transport)
    expect(phone.provider).toBe("twilio")
    expect(line.provider).toBe("twilio")
    expect(phone.session).toBe(null)
    expect(phone.sipRegistered).toBe(true)
})

test("each line has its own transport and registration", async () => {
    const {phone, first, second} = await mountTwoLines()
    expect(first.transport).not.toBe(second.transport)
    expect(first.registered).toBe(true)
    expect(second.registered).toBe(true)

    await second.transport.unregister()
    expect(second.registered).toBe(false)
    expect(first.registered).toBe(true)
    expect(first.transport.state).toBe("registered")
    expect(phone.sipRegistered).toBe(true)
})

test("an expiring credential refreshes only the line it belongs to", async () => {
    const {first, second} = await mountTwoLines()
    const calls = watchRecovery(first, second)
    second.transport._emit("authWillExpire")
    expect(calls).toEqual([`${SECOND}.updateToken`])
    first.transport._emit("authWillExpire")
    expect(calls).toEqual([`${SECOND}.updateToken`, `${first.key}.updateToken`])
})

test("a lost registration retries only the line that lost it", async () => {
    const {first, second} = await mountTwoLines()
    const calls = watchRecovery(first, second)
    freezeTime()
    await second.transport.unregister()
    await advanceTime(3000)
    expect(calls).toEqual([`${SECOND}.updateToken`])
})

test("a dead transport reconnects only its own line", async () => {
    patchWithCleanup(console, {error() {}})
    const {first, second} = await mountTwoLines()
    const calls = watchRecovery(first, second)
    freezeTime()
    second.transport._emit("error", {name: "AccessTokenInvalid", message: "rejected"})
    await advanceTime(2000)
    expect(calls).toEqual([`${SECOND}.reconnect`])
})

test("a reconnect replaces only its own line's transport and credential", async () => {
    const fresh = {token: "fresh-token"}
    const mints = []
    onRpc("connect.user", "get_client_token", () => {
        mints.push("get_client_token")
        return fresh
    })
    const {first, second} = await mountTwoLines()
    const firstTransport = first.transport
    const firstToken = first.tokenData
    const oldTransport = second.transport

    await second.reconnect()
    await animationFrame()

    expect(mints.length).toBe(1)
    expect(oldTransport.state).toBe("destroyed")
    expect(second.transport).not.toBe(oldTransport)
    expect(second.transport.state).toBe("registered")
    expect(second.tokenData).toEqual(fresh)
    expect(first.transport).toBe(firstTransport)
    expect(first.transport.state).toBe("registered")
    expect(first.tokenData).toBe(firstToken)
})

test("an incoming call lands on the line it rang on, and ends there", async () => {
    const {phone, first, second} = await mountTwoLines()
    const {call} = ring(second)
    expect(second.session).toBe(call)
    expect(first.session).toBe(null)
    expect(phone.session).toBe(call)
    expect(phone.transport).toBe(second.transport)
    expect(phone.provider).toBe(SECOND)
    await animationFrame()

    call.disconnect()
    await animationFrame()
    expect(second.session).toBe(null)
    expect(phone.session).toBe(null)
    expect(phone.provider).toBe("twilio")
})

test("a call placed on a line belongs to that line", async () => {
    const {phone, first, second} = await mountTwoLines()
    const call = await second.dial({To: "+15550100", Called: "+15550100"})
    expect(second.owns(call)).toBe(true)
    expect(first.owns(call)).toBe(false)
    expect(phone.session).toBe(null)

    phone.session = call
    expect(second.session).toBe(call)
    expect(first.session).toBe(null)

    phone.session = null
    expect(second.session).toBe(null)
})

test("a third call is refused on either line", async () => {
    const {phone, first, second} = await mountTwoLines()
    const foreground = ring(first)
    expect(phone.session).toBe(foreground.call)

    // The other line is idle, but the phone already has a foreground call.
    const waiting = ring(second)
    expect(second.session).toBe(null)
    expect(phone.waitingSession).toBe(waiting.call)
    expect(waiting.seen.reject).toBe(0)
    await animationFrame()

    const thirdOnFirst = ring(first)
    const thirdOnSecond = ring(second)
    expect(thirdOnFirst.seen.reject).toBe(1)
    expect(thirdOnSecond.seen.reject).toBe(1)
    expect(phone.session).toBe(foreground.call)
    expect(phone.waitingSession).toBe(waiting.call)
    expect(foreground.seen.reject).toBe(0)
    expect(waiting.seen.reject).toBe(0)
})

test("answering a waiting call from another line moves the foreground to that line", async () => {
    onRpc("connect.channel", "hold_softphone_call", () => ({success: true}))
    const {phone, first, second} = await mountTwoLines()
    const foreground = ring(first)
    await animationFrame()
    const waiting = ring(second)
    await animationFrame()

    await phone._onClickAcceptWaiting()
    await animationFrame()

    expect(waiting.seen.accept).toBe(1)
    expect(second.session).toBe(waiting.call)
    expect(first.session).toBe(null)
    expect(phone.session).toBe(waiting.call)
    expect(phone.heldSession).toBe(foreground.call)
    expect(phone.provider).toBe(SECOND)
    expect(phone.waitingSession).toBe(null)
})
