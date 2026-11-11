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

const CALL_LINK = ".o_phone_form_link:not(.o_field_phone_whatsapp)"
const NUMBER = "+1 555 0100"

function mountBus(values) {
    const bus = Object.assign(new EventBus(), values)
    patchWithCleanup(window, {connectBus: bus})
    return bus
}

async function clickCall(readonly) {
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
        readonly,
        arch: `<form><sheet><group><field name="phone" widget="phone"/></group></sheet></form>`,
    })
    expect(CALL_LINK).toHaveCount(1)
    const link = queryOne(CALL_LINK)
    let defaultPrevented
    // Reads the handler's decision, then keeps the test page from following the link.
    link.parentElement.addEventListener("click", (ev) => {
        defaultPrevented = ev.defaultPrevented
        ev.preventDefault()
    })
    await click(link)
    return {originates, defaultPrevented, link}
}

for (const [mode, readonly] of [["edit", false], ["readonly", true]]) {
    describe(mode, () => {
        test("with no phone mounted the Call link stays a plain tel link", async () => {
            expect(window.connectBus).toBe(undefined)
            const {originates, defaultPrevented, link} = await clickCall(readonly)
            expect(link).toHaveAttribute("href", "tel:+15550100")
            expect(defaultPrevented).toBe(false)
            expect(originates).toEqual([])
        })

        test("a web client first callflow dials in this browser", async () => {
            const bus = mountBus({clickToCallLocal: true, browserSessionNonce: "tab-1"})
            const dials = []
            bus.addEventListener("busPhoneMakeCall", ({detail}) => dials.push(detail))
            const {originates, defaultPrevented} = await clickCall(readonly)
            expect(defaultPrevented).toBe(true)
            expect(dials).toEqual([{phone: NUMBER}])
            expect(originates).toEqual([])
        })

        test("any other first callflow asks the server, towards this tab", async () => {
            const bus = mountBus({clickToCallLocal: false, browserSessionNonce: "tab-1"})
            const dials = []
            bus.addEventListener("busPhoneMakeCall", ({detail}) => dials.push(detail))
            const {originates, defaultPrevented} = await clickCall(readonly)
            expect(defaultPrevented).toBe(true)
            expect(dials).toEqual([])
            expect(originates).toEqual([
                {args: [NUMBER, "partner", 1], kwargs: {origin_nonce: "tab-1"}},
            ])
        })
    })
}

test("a server call carries the From number currently selected on the phone", async () => {
    const bus = mountBus({clickToCallLocal: false, clickToCallCallerId: '+15745646338'})
    const {originates} = await clickCall(false)
    expect(originates).toEqual([
        {args: [NUMBER, "partner", 1], kwargs: {caller_id: '+15745646338'}},
    ])
})
