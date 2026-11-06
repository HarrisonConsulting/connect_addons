/** @odoo-module **/
"use strict"
/**
 * Optional preferences on a line's credential. With none, the phone is as it has
 * always been; that is what a Twilio credential is.
 */

import {after, animationFrame, describe, expect, test} from "@odoo/hoot"
import {waitUntil} from "@odoo/hoot-dom"
import {mockService, onRpc} from "@web/../tests/web_test_helpers"
import {registry} from "@web/core/registry"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {maskNumber} from "@connect/js/utils"
import {LoopbackCall, registerLoopbackProvider} from "@connect/../tests/helpers/loopback_transport"
import {mountLines, mountSingleLinePhone} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const SECOND = "preference_provider"
const NUMBER = "+15550100"

function watchNotifications() {
    const added = []
    mockService("notification", {
        add(message, options) {
            added.push({message, sticky: options.sticky})
            return () => {}
        },
    })
    return added
}

async function answerCall(phone) {
    phone.bus.trigger("busPhoneMakeCall", {phone: NUMBER})
    await waitUntil(() => phone.session)
    phone.session.accept()
}

function mountWith(preferences) {
    return mountSingleLinePhone({token: "t", ...preferences})
}

test("no preferences are today's defaults", async () => {
    const added = watchNotifications()
    const phone = await mountSingleLinePhone({token: "t", edge: "ashburn"})
    expect(phone.call_popup_is_enabled).toBe(false)
    expect(phone.call_popup_is_sticky).toBe(false)
    expect(phone.phone_ring_volume).toBe(70)
    expect(phone.isMaskCallNumber).toBe(false)
    expect(phone.displayNumber(NUMBER)).toBe(NUMBER)
    phone.notify("hello", {})
    expect(added).toEqual([])
})

test("the popup preference turns notifications on", async () => {
    const added = watchNotifications()
    const phone = await mountWith({call_popup_is_enabled: true})
    phone.notify("hello", {})
    expect(added).toEqual([{message: "hello", sticky: false}])
})

test("the sticky preference makes them stay, unless a caller says otherwise", async () => {
    const added = watchNotifications()
    const phone = await mountWith({call_popup_is_enabled: true, call_popup_is_sticky: true})
    phone.notify("stays", {})
    phone.notify("goes", {sticky: false})
    expect(added).toEqual([
        {message: "stays", sticky: true},
        {message: "goes", sticky: false},
    ])
})

test("the ring volume preference fills the value", async () => {
    const phone = await mountWith({phone_ring_volume: 40})
    expect(phone.phone_ring_volume).toBe(40)
})

test("a ring volume of zero is a value, not an absence", async () => {
    const phone = await mountWith({phone_ring_volume: 0})
    expect(phone.phone_ring_volume).toBe(0)
})

test("the masking preference masks the number on the panel, and only the display", async () => {
    const dials = []
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    const phone = await mountWith({mask_call_number: true})
    const {dial} = phone.activeLine
    phone.activeLine.dial = function (params) {
        dials.push({...params})
        return dial.call(this, params)
    }
    await answerCall(phone)
    await animationFrame()
    const masked = maskNumber(NUMBER)
    expect(masked).not.toBe(NUMBER)
    expect(dials).toEqual([{To: NUMBER, Called: NUMBER}])
    expect(phone.state.callerId.phoneNumber).toBe(NUMBER)
    const details = document.querySelector(".o_phone_call_details").textContent
    expect(details).toInclude(masked)
    expect(details).not.toInclude(NUMBER)
    phone.session.disconnect()
})

test("no masking shows the number on the panel", async () => {
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    const phone = await mountSingleLinePhone({token: "t"})
    await answerCall(phone)
    await animationFrame()
    expect(document.querySelector(".o_phone_call_details").textContent).toInclude(NUMBER)
    phone.session.disconnect()
})

test("masking covers a waiting caller's popup", async () => {
    const added = watchNotifications()
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    const phone = await mountWith({mask_call_number: true, call_popup_is_enabled: true})
    await animationFrame()
    await answerCall(phone)
    await phone.lines[0]._onIncoming(new LoopbackCall({From: "+15550199", To: "+15550199"}))
    await animationFrame()
    const text = added.map(({message}) => message).join()
    expect(text).toInclude(maskNumber("+15550199"))
    expect(text).not.toInclude("+15550199")
    expect(".call-waiting-caller").toHaveText(maskNumber("+15550199"))
    phone.waitingSession = null
    phone.state.hasWaitingCall = false
    phone.session.disconnect()
})

test("a preference belongs to the line on the panel", async () => {
    registerLoopbackProvider(SECOND)
    after(() => registry.category("connect.telephony_transports").remove(SECOND))
    const phone = await mountLines([
        {key: "twilio", provider: "twilio", media: "browser", capabilities: {}, token_data: {token: "a"}},
        {key: SECOND, provider: SECOND, media: "browser", capabilities: {}, token_data: {token: "b", phone_ring_volume: 25}},
    ])
    expect(phone.phone_ring_volume).toBe(70)
    const call = new LoopbackCall({})
    phone.lines[1]._calls.add(call)
    phone.session = call
    expect(phone.phone_ring_volume).toBe(25)
    phone.session = null
})

test("a refreshed credential brings its preferences", async () => {
    const phone = await mountWith({phone_ring_volume: 40})
    phone.lines[0].tokenData = {token: "t2", phone_ring_volume: 55}
    expect(phone.phone_ring_volume).toBe(55)
})
