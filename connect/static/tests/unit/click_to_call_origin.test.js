/** @odoo-module **/
"use strict"

import {describe, expect, test} from "@odoo/hoot"
import {EventBus} from "@odoo/owl"
import {mockService, mountWithCleanup} from "@web/../tests/web_test_helpers"
import {Phone} from "@connect/components/phone/phone/phone"
import {LoopbackCall} from "@connect/../tests/helpers/loopback_transport"

describe.current.tags("desktop")

const NONCE = "tab-nonce-1"

async function mountPhone() {
    mockService("connect_audio", {})
    const phone = await mountWithCleanup(Phone, {
        props: {
            bus: new EventBus(),
            token_data: {token: "test-token", edge: "ashburn"},
            browserSessionNonce: NONCE,
        },
    })
    return phone
}

function ringWith(phone, params) {
    const call = new LoopbackCall({From: "+15550100", autoAnswer: "yes", ...params})
    const seen = {accept: 0, reject: 0, ignore: 0}
    for (const name of Object.keys(seen)) {
        call.on(name, () => seen[name]++)
    }
    phone.transport._emit("incoming", call)
    return seen
}

test("click-to-call is answered by the tab that placed it", async () => {
    const phone = await mountPhone()
    const seen = ringWith(phone, {Origin: NONCE})
    expect(seen.accept).toBe(1)
    expect(seen.ignore).toBe(0)
})

test("click-to-call placed by another device is ignored, never rejected", async () => {
    const phone = await mountPhone()
    const seen = ringWith(phone, {Origin: "another-device"})
    expect(seen.ignore).toBe(1)
    expect(seen.accept).toBe(0)
    expect(seen.reject).toBe(0)
})

test("auto-answer without an origin keeps answering", async () => {
    const phone = await mountPhone()
    const seen = ringWith(phone, {})
    expect(seen.accept).toBe(1)
    expect(seen.ignore).toBe(0)
})
