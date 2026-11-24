/** @odoo-module **/
"use strict"
/**
 * Phone builds one line today. These tests give it a second by hand, over the
 * loopback under another provider key, because what they pin only shows with two:
 * an event acts on the line whose transport fired it, and the limit of one
 * foreground call plus one waiting call counts every line.
 */

import {advanceTime, after, animationFrame, describe, expect, freezeTime, test, unfreezeTime} from "@odoo/hoot"
import {waitUntil} from "@odoo/hoot-dom"
import {onRpc, patchWithCleanup} from "@web/../tests/web_test_helpers"
import {registry} from "@web/core/registry"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {MAX_AUTO_ATTEMPTS, PhoneLine, REGISTRATION_TIMEOUT_MS} from "@connect/components/phone/phone/phone_line"
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
        label: "Second",
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

// A line never waits for ever

const SILENT = "silent_provider"

/** A transport whose registration never concludes: no registered, no error. */
class NeverAnswers extends LoopbackTransport {
    async register() {}
}

/** A phone with one line that registers, and a second whose registration never concludes. */
async function mountWithSilentLine() {
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    registry.category("connect.telephony_transports").add(SILENT, NeverAnswers, {force: true})
    after(() => registry.category("connect.telephony_transports").remove(SILENT))
    const phone = await mountSingleLinePhone(TOKEN_DATA)
    const [first] = phone.lines
    const silent = new PhoneLine(phone, {key: SILENT, provider: SILENT, label: "VoiceTel", tokenData: {token: "t"}})
    phone.lines.push(silent)
    freezeTime()
    silent.initTransport()
    return {phone, first, silent}
}

const statusOf = (phone, line) => phone.state.lineStatuses[line.key]

test("a registration that does not conclude within the bound becomes an error in words", async () => {
    const {phone, first, silent} = await mountWithSilentLine()
    await advanceTime(REGISTRATION_TIMEOUT_MS - 1)
    expect(statusOf(phone, silent)).toBe(undefined)
    await advanceTime(1)
    expect(statusOf(phone, silent)).toEqual({
        status: "error",
        error: "VoiceTel did not finish signing in: the phone service did not answer.",
    })
    expect(statusOf(phone, first).status).toBe("ready")
})

test("a line that has given up does not try again by itself", async () => {
    const {silent} = await mountWithSilentLine()
    const calls = watchRecovery(silent)
    const transport = silent.transport
    await advanceTime(REGISTRATION_TIMEOUT_MS)
    await advanceTime(10 * REGISTRATION_TIMEOUT_MS)
    expect(calls).toEqual([])
    expect(silent.transport).toBe(transport)
})

test("a registration that concludes in time is never reported late", async () => {
    const {phone, first} = await mountWithSilentLine()
    await advanceTime(10 * REGISTRATION_TIMEOUT_MS)
    expect(statusOf(phone, first).status).toBe("ready")
})

test("an error that concludes the registration is not replaced by the clock's words", async () => {
    patchWithCleanup(console, {error() {}})
    const {phone, silent} = await mountWithSilentLine()
    silent.transport._emit("error", {name: "x", message: "the provider refused"})
    await advanceTime(10 * REGISTRATION_TIMEOUT_MS)
    expect(statusOf(phone, silent)).toEqual({status: "error", error: "the provider refused"})
})

test("a registration that arrives after the line gave up makes it ready", async () => {
    const {phone, silent} = await mountWithSilentLine()
    await advanceTime(REGISTRATION_TIMEOUT_MS)
    expect(statusOf(phone, silent).status).toBe("error")
    silent.transport._emit("registered")
    expect(statusOf(phone, silent)).toEqual({status: "ready", error: ""})
})

test("a line that is destroyed has no timer left to act", async () => {
    const {phone, silent} = await mountWithSilentLine()
    silent.destroy()
    await advanceTime(10 * REGISTRATION_TIMEOUT_MS)
    expect(statusOf(phone, silent)).toBe(undefined)
})

test("a line stops reconnecting by itself after its automatic attempts, and says why", async () => {
    patchWithCleanup(console, {error() {}})
    const {phone, first, second} = await mountTwoLines()
    const calls = watchRecovery(first, second)
    freezeTime()
    for (let attempt = 1; attempt <= MAX_AUTO_ATTEMPTS; attempt++) {
        second.transport._emit("error", {name: "AccessTokenInvalid", message: "rejected"})
        await advanceTime(2000)
        expect(calls.length).toBe(attempt)
    }
    expect(statusOf(phone, second).status).toBe("connecting")
    second.transport._emit("error", {name: "AccessTokenInvalid", message: "rejected"})
    await advanceTime(60000)
    expect(calls.length).toBe(MAX_AUTO_ATTEMPTS)
    expect(statusOf(phone, second)).toEqual({
        status: "error",
        error: `Second could not sign in after ${MAX_AUTO_ATTEMPTS} tries: rejected`,
    })
    expect(statusOf(phone, first).status).toBe("ready")
})

test("a successful registration resets the count of automatic attempts", async () => {
    patchWithCleanup(console, {error() {}})
    const {phone, first, second} = await mountTwoLines()
    const calls = watchRecovery(first, second)
    freezeTime()
    for (let round = 0; round < 2; round++) {
        for (let attempt = 0; attempt < MAX_AUTO_ATTEMPTS; attempt++) {
            second.transport._emit("error", {name: "AccessTokenInvalid", message: "rejected"})
            await advanceTime(2000)
        }
        second.transport._emit("registered")
    }
    expect(calls.length).toBe(2 * MAX_AUTO_ATTEMPTS)
    expect(statusOf(phone, second).status).toBe("ready")
})

test("Retry gives a line that gave up a fresh round, and touches no other", async () => {
    patchWithCleanup(console, {error() {}})
    const {phone, first, second} = await mountTwoLines()
    const calls = watchRecovery(first, second)
    freezeTime()
    for (let attempt = 0; attempt <= MAX_AUTO_ATTEMPTS; attempt++) {
        second.transport._emit("error", {name: "AccessTokenInvalid", message: "rejected"})
        await advanceTime(2000)
    }
    expect(statusOf(phone, second).status).toBe("error")
    expect(second._autoAttempts).toBe(MAX_AUTO_ATTEMPTS)
    calls.length = 0

    const retried = phone._reconnect()
    expect(second._autoAttempts).toBe(0)
    await retried
    expect(calls).toEqual([`${SECOND}.reconnect`])
})

test("a registration lost over and over stops being recovered after the automatic attempts", async () => {
    const {phone, first, second} = await mountTwoLines()
    watchRecovery(first, second)
    for (let attempt = 1; attempt <= MAX_AUTO_ATTEMPTS; attempt++) {
        second.transport._emit("unregistered")
        expect(statusOf(phone, second).status).toBe("connecting")
    }
    second.transport._emit("unregistered")
    expect(statusOf(phone, second)).toEqual({
        status: "error",
        error: `Second could not stay signed in after ${MAX_AUTO_ATTEMPTS} tries.`,
    })
})

test("a line that lost its registration signs in again by itself", async () => {
    onRpc("connect.user", "get_client_token", () => ({token: "fresh"}))
    const {phone, second} = await mountTwoLines()
    freezeTime()
    await second.transport.unregister()
    expect(statusOf(phone, second).status).toBe("connecting")
    await advanceTime(3000)
    unfreezeTime()
    await waitUntil(() => statusOf(phone, second).status === "ready")
    expect(second.transport.state).toBe("registered")
})
