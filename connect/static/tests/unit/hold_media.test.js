/** @odoo-module **/
"use strict"
/**
 * A transport that holds its own media moves the audio only after the server says
 * yes, and a hold that did not move the audio is not called a hold.
 */

import {animationFrame, describe, expect, test} from "@odoo/hoot"
import {onRpc, patchWithCleanup} from "@web/../tests/web_test_helpers"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {LoopbackCall} from "@connect/../tests/helpers/loopback_transport"
import {mountSingleLinePhone} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

async function inCall({success = true, moves = true} = {}) {
    const steps = []
    patchWithCleanup(LoopbackCall.prototype, {
        hold(on) {
            steps.push(`media ${on}`)
            return Promise.resolve(moves)
        },
    })
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    onRpc("connect.channel", "hold_softphone_call", () => {
        steps.push("server hold")
        return success ? {success} : {success, error: "Refused here."}
    })
    onRpc("connect.channel", "resume_softphone_call", () => {
        steps.push("server resume")
        return {success: true}
    })
    onRpc("connect.channel", "attended_transfer_softphone_call", ({args}) => {
        steps.push(`server ${args[0].step}`)
        return {success: true}
    })
    const phone = await mountSingleLinePhone({token: "t"})
    await phone.makeCall({phone: "+15550100"})
    phone.session.accept()
    await animationFrame()
    return {phone, steps}
}

test("the audio moves after the server says yes, both ways", async () => {
    const {phone, steps} = await inCall()
    await phone._onClickHold()
    expect(phone.state.isOnHold).toBe(true)
    await phone._onClickHold()
    expect(phone.state.isOnHold).toBe(false)
    expect(steps).toEqual(["server hold", "media true", "server resume", "media false"])
})

test("a server that refuses leaves the audio alone", async () => {
    const {phone, steps} = await inCall({success: false})
    await phone._onClickHold()
    expect(phone.state.isOnHold).toBe(false)
    expect(steps).toEqual(["server hold"])
    expect(phone.state.lastOperationError).toBe("Refused here.")
})

test("a hold the audio did not follow is not called a hold, and says so", async () => {
    const {phone} = await inCall({moves: false})
    await phone._onClickHold()
    expect(phone.state.isOnHold).toBe(false)
    expect(phone.state.lastOperationError).toBe("Hold failed")
})

test("an attended transfer holds the audio and gives it back", async () => {
    const {phone, steps} = await inCall()
    phone.state.pendingTransferNumber = "+15550200"
    await phone._onClickAttendedTransfer()
    expect(phone.state.isAttendedTransfer).toBe(true)
    await phone._onClickCancelTransfer()
    expect(phone.state.isOnHold).toBe(false)
    expect(steps).toEqual(["server start", "media true", "server cancel", "media false"])
})

test("a waiting call stays waiting when the audio did not move", async () => {
    const {phone, steps} = await inCall({moves: false})
    const waiting = new LoopbackCall({})
    phone.waitingSession = waiting
    await phone._onClickAcceptWaiting()
    expect(steps).toEqual(["server hold", "media true"])
    expect(phone.waitingSession).toBe(waiting)
    expect(phone.state.isOnHold).toBe(false)
})
