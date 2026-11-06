/** @odoo-module **/
"use strict"

import {advanceTime, animationFrame, describe, expect, freezeTime, test, tick} from "@odoo/hoot"
import {patchWithCleanup} from "@web/../tests/web_test_helpers"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {mountSingleLinePhone} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const CAP_MS = 60000

/**
 * A mounted phone whose presence calls go to `answer`, with its clock stopped.
 * The list it returns holds every status sent.
 */
async function phoneWithPresence(answer) {
    patchWithCleanup(console, {warn() {}})
    const phone = await mountSingleLinePhone({token: "test-token", edge: "ashburn"})
    await animationFrame()
    const sent = []
    phone.orm = {
        call(model, method, args) {
            if (method !== "update_presence") {
                return Promise.resolve(false)
            }
            sent.push(args[0])
            return answer(sent.length)
        },
    }
    // The mount has reported already, so the next status starts the count.
    phone._presenceDesired = null
    phone._presenceSent = null
    freezeTime()
    return {phone, sent}
}

const refuse = () => Promise.reject(new Error("refused"))

async function pass(ms) {
    await advanceTime(ms)
    await tick()
}

test("a refused update waits, doubling up to a cap", async () => {
    const {phone, sent} = await phoneWithPresence(refuse)
    phone._updatePresence("available")
    await tick()
    expect(sent.length).toBe(1)

    let expected = 1
    for (const wait of [1000, 2000, 4000, 8000, 16000, 32000, CAP_MS, CAP_MS, CAP_MS]) {
        await pass(wait - 1)
        expect(sent.length).toBe(expected)
        await pass(1)
        expected++
        expect(sent.length).toBe(expected)
    }
})

test("a new status during a wait sends nothing until the wait ends, then the latest only", async () => {
    const {phone, sent} = await phoneWithPresence((count) =>
        count === 1 ? refuse() : Promise.resolve(true)
    )
    phone._updatePresence("available")
    await tick()
    phone._updatePresence("on_call")
    phone._updatePresence("on_hold")
    await tick()
    expect(sent).toEqual(["available"])

    await pass(1000)
    expect(sent).toEqual(["available", "on_hold"])
    await pass(CAP_MS)
    expect(sent).toEqual(["available", "on_hold"])
})

test("a success ends the retries and the next failure waits from the start", async () => {
    const {phone, sent} = await phoneWithPresence((count) =>
        [1, 2, 4].includes(count) ? refuse() : Promise.resolve(true)
    )
    phone._updatePresence("available")
    await tick()
    await pass(1000)
    await pass(2000)
    expect(sent.length).toBe(3)
    await pass(CAP_MS)
    expect(sent.length).toBe(3)

    phone._updatePresence("on_call")
    await tick()
    expect(sent.length).toBe(4)
    await pass(1000)
    expect(sent.length).toBe(5)
})

test("a status that succeeds with no failure sends once, at once", async () => {
    const {phone, sent} = await phoneWithPresence(() => Promise.resolve(true))
    phone._updatePresence("available")
    await tick()
    phone._updatePresence("available")
    await tick()
    expect(sent).toEqual(["available"])
})

test("unmounting the phone cancels a waiting retry", async () => {
    const {phone, sent} = await phoneWithPresence(refuse)
    phone._updatePresence("available")
    await tick()
    expect(phone._presenceRetryTimer).not.toBe(null)
    phone.__owl__.app.destroy()
    expect(phone._presenceRetryTimer).toBe(null)
    await pass(CAP_MS)
    expect(sent.length).toBe(1)
})
