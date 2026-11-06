/** @odoo-module **/
"use strict"

import {describe, expect, test} from "@odoo/hoot"
import {click, queryOne} from "@odoo/hoot-dom"
import {EventBus} from "@odoo/owl"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {
    defineModels,
    fields,
    models,
    mountView,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers"

describe.current.tags("desktop")

class Partner extends models.Model {
    phone = fields.Char()
    _records = [{id: 1, phone: "+1 555 0100"}]
}
defineMailModels()
defineModels([Partner])

const NUMBER = "+1 555 0100"
const CALL_LINK = ".o_phone_form_link:not(.o_field_phone_whatsapp)"

async function clickLink(selector) {
    const originates = []
    onRpc("connect.settings", "originate_call", ({args, kwargs}) => {
        const sent = Object.entries(kwargs).filter(([key]) => key !== "context")
        originates.push({args, kwargs: Object.fromEntries(sent)})
        return false
    })
    await mountView({
        type: "form",
        resModel: "partner",
        resId: 1,
        readonly: true,
        arch: `<form><sheet><group><field name="phone" widget="phone"/></group></sheet></form>`,
    })
    // One Call link beside the two WhatsApp links, not one from each module.
    expect(CALL_LINK).toHaveCount(1)
    expect(".o_field_phone_whatsapp").toHaveCount(2)
    const link = queryOne(selector)
    let defaultPrevented
    link.parentElement.addEventListener("click", (ev) => {
        defaultPrevented = ev.defaultPrevented
        ev.preventDefault()
    })
    await click(link)
    return {originates, defaultPrevented}
}

test("a user with no softphone still gets a server call", async () => {
    const {originates, defaultPrevented} = await clickLink(CALL_LINK)
    expect(defaultPrevented).toBe(true)
    expect(originates).toEqual([{args: [NUMBER, "partner", 1], kwargs: {}}])
})

test("a softphone goes through core's click: dials in this browser", async () => {
    const bus = Object.assign(new EventBus(), {clickToCallLocal: true, browserSessionNonce: "tab-1"})
    patchWithCleanup(window, {connectBus: bus})
    const dials = []
    bus.addEventListener("busPhoneMakeCall", ({detail}) => dials.push(detail))
    const {originates} = await clickLink(CALL_LINK)
    expect(dials).toEqual([{phone: NUMBER}])
    expect(originates).toEqual([])
})

test("a softphone goes through core's click: asks the server towards this tab", async () => {
    patchWithCleanup(window, {
        connectBus: Object.assign(new EventBus(), {clickToCallLocal: false, browserSessionNonce: "tab-1"}),
    })
    const {originates} = await clickLink(CALL_LINK)
    expect(originates).toEqual([{args: [NUMBER, "partner", 1], kwargs: {origin_nonce: "tab-1"}}])
})

test("a WhatsApp call carries its flag and this tab's nonce", async () => {
    patchWithCleanup(window, {
        connectBus: Object.assign(new EventBus(), {browserSessionNonce: "tab-1"}),
    })
    const {originates, defaultPrevented} = await clickLink("[title='WhatsApp Call']")
    expect(defaultPrevented).toBe(true)
    expect(originates).toEqual([{
        args: [NUMBER, "partner", 1],
        kwargs: {whatsapp_call: true, origin_nonce: "tab-1"},
    }])
})

test("a WhatsApp call with no softphone carries only its flag", async () => {
    const {originates} = await clickLink("[title='WhatsApp Call']")
    expect(originates).toEqual([{args: [NUMBER, "partner", 1], kwargs: {whatsapp_call: true}}])
})
