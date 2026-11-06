/** @odoo-module **/
"use strict"
/**
 * Freeze: what a person with one line gets from the phone. Written against the
 * phone before lines existed, and not edited since. If one of these has to
 * change, one-line users have been changed.
 */

import {describe, expect, test} from "@odoo/hoot"
import {animationFrame} from "@odoo/hoot-mock"
import {click, waitUntil} from "@odoo/hoot-dom"
import {mountWithCleanup, onRpc, patchWithCleanup} from "@web/../tests/web_test_helpers"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {PhoneSysTray} from "@connect/components/phone/tray/tray"
import {LoopbackTransport} from "@connect/../tests/helpers/loopback_transport"
import {mountSingleLinePhone} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const TOKEN_DATA = {token: "single-line-token", edge: "ashburn"}
const NUMBER = "+15550100"

function countMints() {
    const mints = []
    onRpc("connect.user", "get_client_token", () => {
        mints.push("get_client_token")
        return TOKEN_DATA
    })
    onRpc("connect.user", "get_phone_lines", () => {
        mints.push("get_phone_lines")
        return {lines: [], error: false}
    })
    return mints
}

test("mounting a line asks the server for nothing", async () => {
    const mints = countMints()
    await mountSingleLinePhone(TOKEN_DATA)
    await animationFrame()
    expect(mints).toEqual([])
})

test("a dial sends exactly To and Called", async () => {
    const dials = []
    patchWithCleanup(LoopbackTransport.prototype, {
        async dial(params) {
            dials.push({...params})
            return super.dial(params)
        },
    })
    const mints = countMints()
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    const phone = await mountSingleLinePhone(TOKEN_DATA)
    phone.bus.trigger("busPhoneMakeCall", {phone: NUMBER})
    await waitUntil(() => dials.length > 0)
    await animationFrame()
    expect(dials).toEqual([{To: NUMBER, Called: NUMBER}])
    expect(mints).toEqual([])
    phone.bus.trigger("busPhoneHangUp")
    await animationFrame()
})

test("the panel has no From row", async () => {
    await mountSingleLinePhone(TOKEN_DATA)
    await animationFrame()
    expect(".o_root_connect_phone").toHaveCount(1)
    expect(".o_phone_line_selector").toHaveCount(0)
    expect(".o_root_connect_phone [aria-label='From']").toHaveCount(0)
})

test("one toggle opens and closes the one panel", async () => {
    const phone = await mountSingleLinePhone(TOKEN_DATA)
    await mountWithCleanup(PhoneSysTray, {props: {bus: phone.bus}})
    await animationFrame()
    expect(".toggle-phone").toHaveCount(1)
    expect(".o_root_connect_phone").toHaveClass("o_hide")
    await click(".toggle-phone")
    await animationFrame()
    expect(".o_root_connect_phone").not.toHaveClass("o_hide")
    await click(".toggle-phone")
    await animationFrame()
    expect(".o_root_connect_phone").toHaveClass("o_hide")
})
