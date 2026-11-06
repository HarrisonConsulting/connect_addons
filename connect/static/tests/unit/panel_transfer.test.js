/** @odoo-module **/
"use strict"
/**
 * Transfer from the panel. The call ends only once the provider has moved the
 * other party. Any failure leaves the person on the call and tells them so.
 */

import {after, animationFrame, describe, expect, test} from "@odoo/hoot"
import {click, waitFor} from "@odoo/hoot-dom"
import {Deferred, advanceTime} from "@odoo/hoot-mock"
import {makeServerError, onRpc} from "@web/../tests/web_test_helpers"
import {registry} from "@web/core/registry"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {LoopbackTransport} from "@connect/../tests/helpers/loopback_transport"
import {mountLines} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const FAKE = "transfer_provider"
const TARGET = "+15550142"
// What the loopback call reports as its own.
const CALL_SID = "CA" + "0".repeat(32)
const STILL_ON_IT = "The call could not be transferred. You are still on it."

async function inCall(forward) {
    const transports = registry.category("connect.telephony_transports")
    transports.add(FAKE, LoopbackTransport, {force: true})
    after(() => transports.remove(FAKE))
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    const payloads = []
    onRpc("connect.channel", "forward_softphone_call", ({args}) => {
        payloads.push(args[0])
        return forward(args[0])
    })
    const phone = await mountLines([{
        key: FAKE,
        provider: FAKE,
        media: "browser",
        capabilities: {forward: true},
        token_data: {token: "t"},
    }])
    await phone.makeCall({phone: "+15550100"})
    phone.session.accept()
    await animationFrame()
    const sounds = []
    phone.audioNotification = {play: (sound) => sounds.push(sound)}
    const broadcasts = []
    phone.bc = {postMessage: (message) => broadcasts.push(message.event), close() {}}
    return {phone, payloads, sounds, broadcasts, call: phone.session}
}

/** Picks the target and presses Blind, leaving the question open. */
async function pressBlind(phone) {
    phone.bus.trigger("busPhoneMakeTransfer", {phoneNumber: TARGET})
    await animationFrame()
    await click(".btn:contains('Blind')")
    await waitFor(".modal .btn-primary")
}

async function transfer(phone) {
    await pressBlind(phone)
    await click(".modal .btn-primary")
    await animationFrame()
    await animationFrame()
}

const banner = () => document.querySelector(".connect-operation-error")?.textContent.trim()

async function expectStillOnTheCall({phone, call, sounds, broadcasts}, words) {
    await advanceTime(200)
    await animationFrame()
    expect(phone.session).toBe(call)
    expect(phone.state.inCall).toBe(true)
    expect(phone.state.isDialingPanel).toBe(true)
    expect(call._connectSettled).toBe(undefined)
    expect(banner()).toBe(words)
    expect(sounds).toEqual(["transfer_failed"])
    expect(broadcasts).not.toInclude("tbcTransfer")
}

test("a provider's plain answer is a success, and ends the call", async () => {
    const context = await inCall(({number}) => ({forwarded_to: number, channel_sid: "CAOTHER"}))
    const {phone, payloads, sounds, broadcasts, call} = context
    await transfer(phone)
    expect(payloads).toEqual([{provider: FAKE, channel_sid: CALL_SID, number: TARGET}])
    await advanceTime(200)
    await animationFrame()
    expect(phone.state.inCall).toBe(false)
    expect(phone.state.isDialingPanel).toBe(false)
    expect(banner()).toBe(undefined)
    expect(sounds).toEqual(["transfer_complete"])
    expect(broadcasts).toInclude("tbcTransfer")
    // The carrier then drops our leg, which still settles the call record.
    call.disconnect()
    await animationFrame()
    expect(phone.session).toBe(null)
})

test("a refusal keeps the call up and says why in the server's words", async () => {
    const context = await inCall(() => {
        throw makeServerError({type: "UserError", description: "Could not forward the call: busy"})
    })
    await transfer(context.phone)
    expect(context.payloads.length).toBe(1)
    await expectStillOnTheCall(context, "Could not forward the call: busy")
    context.call.disconnect()
})

test("a success false answer keeps the call up", async () => {
    const context = await inCall(() => ({success: false, error: "No target"}))
    await transfer(context.phone)
    await expectStillOnTheCall(context, "No target")
    context.call.disconnect()
})

test("after a failure the person can press Transfer again and it can work", async () => {
    let refuse = true
    const context = await inCall(({number}) => {
        if (refuse) {
            throw makeServerError({type: "UserError", description: "Busy"})
        }
        return {forwarded_to: number}
    })
    const {phone, payloads, call} = context
    await transfer(phone)
    await expectStillOnTheCall(context, "Busy")
    refuse = false
    await transfer(phone)
    expect(payloads.length).toBe(2)
    await advanceTime(200)
    await animationFrame()
    expect(phone.state.inCall).toBe(false)
    call.disconnect()
})

test("saying no to the question sends nothing and keeps the call", async () => {
    const {phone, payloads, call} = await inCall(() => ({}))
    await pressBlind(phone)
    await click(".modal .btn-secondary")
    await animationFrame()
    expect(payloads).toEqual([])
    expect(phone.session).toBe(call)
    expect(phone.state.inCall).toBe(true)
    call.disconnect()
})

test("a call that ended while the question was open is not transferred", async () => {
    const {phone, payloads, call, sounds} = await inCall(() => ({}))
    await pressBlind(phone)
    call.disconnect()
    await animationFrame()
    await click(".modal .btn-primary")
    await animationFrame()
    expect(payloads).toEqual([])
    expect(banner()).toBe("That call has already ended, so there is nothing to transfer.")
    expect(sounds).toEqual(["transfer_failed"])
})

test("an answer that comes after the leg dropped does not end the next call", async () => {
    const answer = new Deferred()
    const {phone, call} = await inCall(() => answer)
    await transfer(phone)
    call.disconnect()
    await animationFrame()
    await advanceTime(200)
    await animationFrame()
    await phone.makeCall({phone: "+15550111"})
    const next = phone.session
    next.accept()
    await animationFrame()
    answer.resolve({forwarded_to: TARGET})
    await animationFrame()
    await advanceTime(200)
    await animationFrame()
    expect(phone.session).toBe(next)
    expect(phone.state.inCall).toBe(true)
    next.disconnect()
})

test("the fallback words stand in when an error carries none", async () => {
    const context = await inCall(() => ({success: false}))
    await transfer(context.phone)
    await expectStillOnTheCall(context, STILL_ON_IT)
    context.call.disconnect()
})
