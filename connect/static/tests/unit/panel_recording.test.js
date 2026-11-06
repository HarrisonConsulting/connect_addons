/** @odoo-module **/
"use strict"
/**
 * Recording from the panel. It reaches the server through connect.channel, on
 * the line the call is on, and nothing is even asked while it is not offered.
 */

import {after, animationFrame, describe, expect, test} from "@odoo/hoot"
import {click} from "@odoo/hoot-dom"
import {Deferred, advanceTime} from "@odoo/hoot-mock"
import {makeServerError, onRpc} from "@web/../tests/web_test_helpers"
import {registry} from "@web/core/registry"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {LoopbackTransport} from "@connect/../tests/helpers/loopback_transport"
import {ALL_CAPABILITIES, mountLines} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const FAKE = "recording_provider"
const CALL_SID = "CA" + "0".repeat(32)
const OFF = "Recording from the phone is turned off for this company."
const RECORDING_METHODS = [
    "get_softphone_recording_state",
    "start_softphone_recording",
    "pause_softphone_recording",
    "resume_softphone_recording",
    "stop_softphone_recording",
]
const NO_RECORDING = Object.fromEntries(Object.keys(ALL_CAPABILITIES).map(
    (action) => [action, !action.startsWith("recording_")]))
const NO_PAUSE = {
    recording_state: true,
    recording_start: true,
    recording_stop: true,
}

/** Mounts a line in an answered call; `answers` maps a method to its answer. */
async function inCall(capabilities, answers = {}) {
    const transports = registry.category("connect.telephony_transports")
    transports.add(FAKE, LoopbackTransport, {force: true})
    after(() => transports.remove(FAKE))
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    const phone = await mountLines([{
        key: FAKE,
        provider: FAKE,
        media: "browser",
        capabilities,
        token_data: {token: "t"},
    }])
    const asked = []
    for (const method of RECORDING_METHODS) {
        onRpc("connect.channel", method, ({args}) => {
            asked.push([method, args[0]])
            const answer = answers[method] ?? {state: "off", supported: true}
            return typeof answer === "function" ? answer(args[0]) : answer
        })
    }
    // Neither of these exists on the server.
    onRpc("connect.call", "get_recording_state", () => asked.push(["old"]))
    onRpc("connect.call", "toggle_recording", () => asked.push(["old"]))
    await phone.makeCall({phone: "+15550100"})
    phone.session.accept()
    await animationFrame()
    await animationFrame()
    const toasts = []
    phone.notify = (message) => toasts.push(message)
    return {phone, asked, toasts}
}

const pressed = (asked) => asked.map(([method]) => method).filter(
    (method) => method !== "get_softphone_recording_state")
const banner = () => document.querySelector(".connect-operation-error")?.textContent.trim()
const label = () => document.querySelector(".o_record_toggle")?.textContent.trim()

async function press(selector = ".o_record_toggle") {
    await click(selector)
    await animationFrame()
    await animationFrame()
}

test("while off there is no button, no question to the server and nothing to press", async () => {
    const {phone, asked} = await inCall(NO_RECORDING)
    expect(".o_recording_panel").toHaveCount(0)
    expect(".o_record_toggle").toHaveCount(0)
    expect(".o_record_stop").toHaveCount(0)
    expect(phone.recordingToggleAction).toBe(null)

    // Every way in: the handlers directly, and the event a recording start raises.
    await phone._onClickToggleRecording()
    await phone._onClickStopRecording()
    await phone._controlRecording("start")
    await phone._controlRecording("resume")
    await phone._fetchRecordingState()
    phone._recordingStartedHandler?.()
    await advanceTime(20000)
    await animationFrame()
    expect(asked).toEqual([])
    expect(phone.state.isRecording).toBe(false)
    phone.session.disconnect()
})

test("start goes to the channel with the call's own provider", async () => {
    const {phone, asked, toasts} = await inCall(ALL_CAPABILITIES, {
        start_softphone_recording: {state: "on", recording_ref: "RE1", supported: true},
    })
    expect(label()).toBe("Record call")
    await press()
    expect(pressed(asked)).toEqual(["start_softphone_recording"])
    expect(asked.at(-1)[1]).toEqual({provider: FAKE, channel_sid: CALL_SID})
    expect(label()).toBe("Recording")
    expect(toasts).toEqual(["Recording started"])
    phone.session.disconnect()
})

test("a line with pause goes recording, paused, recording, then stop", async () => {
    const {phone, asked} = await inCall(ALL_CAPABILITIES, {
        start_softphone_recording: {state: "on", recording_ref: "RE1", supported: true},
        pause_softphone_recording: {state: "paused", recording_ref: "RE1", supported: true},
        resume_softphone_recording: {state: "on", recording_ref: "RE1", supported: true},
        stop_softphone_recording: {state: "stopped", recording_ref: "stopped", supported: true},
    })
    expect(".o_record_stop").toHaveCount(0)
    await press()
    expect(".o_record_stop").toHaveCount(1)
    await press()
    expect(asked.at(-1)[1]).toEqual({provider: FAKE, channel_sid: CALL_SID, recording_ref: "RE1"})
    expect(label()).toBe("Paused")
    await press()
    expect(label()).toBe("Recording")
    await press(".o_record_stop")
    expect(pressed(asked)).toEqual([
        "start_softphone_recording",
        "pause_softphone_recording",
        "resume_softphone_recording",
        "stop_softphone_recording",
    ])
    expect(label()).toBe("Recording stopped")
    expect(".o_record_toggle").toHaveAttribute("title", "Recording stays off for the rest of this call.")
    expect(".o_record_toggle").toHaveProperty("disabled", true)
    expect(".o_record_stop").toHaveCount(0)
    phone.session.disconnect()
})

test("a line without pause never asks for one: pressing stops", async () => {
    const {phone, asked} = await inCall(NO_PAUSE, {
        start_softphone_recording: {state: "on", supported: true},
        stop_softphone_recording: {state: "off", supported: true},
    })
    await press()
    expect(".o_record_toggle").toHaveAttribute("title", "Stop recording")
    expect(".o_record_stop").toHaveCount(0)
    await press()
    expect(pressed(asked)).toEqual(["start_softphone_recording", "stop_softphone_recording"])
    expect(label()).toBe("Record call")
    phone.session.disconnect()
})

test("pause alone is not offered, because a paused recording could not come back", async () => {
    const {phone} = await inCall({...NO_PAUSE, recording_pause: true}, {
        start_softphone_recording: {state: "on", supported: true},
    })
    await press()
    expect(phone.recordingToggleAction).toBe("stop")
    phone.session.disconnect()
})

test("a line that can start but not stop says so once it is recording", async () => {
    const {phone} = await inCall({recording_start: true}, {
        start_softphone_recording: {state: "on", supported: true},
    })
    await press()
    expect(".o_record_toggle").toHaveProperty("disabled", true)
    expect(".o_record_toggle").toHaveAttribute("title", "This line cannot change the recording.")
    phone.session.disconnect()
})

test("the setting turned off during a session is refused in the server's words", async () => {
    const {phone, toasts} = await inCall(ALL_CAPABILITIES, {
        start_softphone_recording: () => {
            throw makeServerError({type: "UserError", message: OFF})
        },
    })
    await press()
    expect(label()).toBe("Record call")
    expect(phone.state.isRecording).toBe(false)
    expect(banner()).toBe(OFF)
    expect(toasts).toEqual([OFF])
    phone.session.disconnect()
})

test("a provider without the method says so and nothing looks like it recorded", async () => {
    const words = "Recording control is not supported for this provider."
    const {phone, toasts} = await inCall(ALL_CAPABILITIES, {
        start_softphone_recording: {supported: false, state: "unsupported", error: words},
    })
    await press()
    expect(label()).toBe("Record call")
    expect(toasts).toEqual([words])
    phone.session.disconnect()
})

test("a refused pause does not claim to have paused", async () => {
    const {phone, toasts} = await inCall(ALL_CAPABILITIES, {
        start_softphone_recording: {state: "on", recording_ref: "RE1", supported: true},
        pause_softphone_recording: {state: "paused", error: "carrier said no", supported: true},
    })
    await press()
    await press()
    expect(label()).toBe("Recording")
    expect(banner()).toBe("The recording could not be paused. It is still recording.")
    expect(toasts).not.toInclude("Recording paused")
    phone.session.disconnect()
})

test("a refused resume does not claim to be recording", async () => {
    const {phone} = await inCall(ALL_CAPABILITIES, {
        get_softphone_recording_state: {state: "paused", recording_ref: "RE1", supported: true},
        resume_softphone_recording: {state: "on", error: "carrier said no", supported: true},
    })
    expect(label()).toBe("Paused")
    await press()
    expect(label()).toBe("Paused")
    expect(banner()).toBe("The recording could not be resumed. It is still paused.")
    phone.session.disconnect()
})

test("a stop nobody can confirm says it may still be recording", async () => {
    const {phone} = await inCall(ALL_CAPABILITIES, {
        get_softphone_recording_state: {state: "on", recording_ref: "RE1", supported: true},
        stop_softphone_recording: {state: "stopped", error: "carrier said no", supported: true},
    })
    await press(".o_record_stop")
    expect(banner()).toBe("The recording could not be confirmed as stopped. It may still be recording.")
    phone.session.disconnect()
})

test("an answered call shows a recording already running, and carries its path to stop", async () => {
    const {phone, asked} = await inCall(NO_PAUSE, {
        get_softphone_recording_state: {state: "on", recording_path: "/r/1.wav", supported: true},
        stop_softphone_recording: {state: "off", supported: true},
    })
    expect(asked[0]).toEqual([
        "get_softphone_recording_state", {provider: FAKE, channel_sid: CALL_SID}])
    expect(label()).toBe("Recording")
    await press()
    expect(asked.at(-1)[1]).toEqual({provider: FAKE, channel_sid: CALL_SID, recording_path: "/r/1.wav"})
    phone.session.disconnect()
})

test("a recording that takes the provider a moment to list is found by looking again", async () => {
    let looks = 0
    const {phone} = await inCall(ALL_CAPABILITIES, {
        get_softphone_recording_state: () => {
            looks++
            return {state: looks < 2 ? "off" : "on", supported: true}
        },
    })
    expect(label()).toBe("Record call")
    await advanceTime(2000)
    await animationFrame()
    await animationFrame()
    expect(label()).toBe("Recording")
    await advanceTime(20000)
    expect(looks).toBe(2)
    phone.session.disconnect()
})

test("an older state answer does not overwrite a press", async () => {
    const answer = new Deferred()
    const {phone, asked} = await inCall(ALL_CAPABILITIES, {
        get_softphone_recording_state: () => answer,
        start_softphone_recording: {state: "on", supported: true},
    })
    await press()
    expect(label()).toBe("Recording")
    answer.resolve({state: "off", supported: true})
    await animationFrame()
    await animationFrame()
    expect(label()).toBe("Recording")
    await advanceTime(20000)
    expect(asked.filter(([method]) => method === "get_softphone_recording_state")).toHaveLength(1)
    phone.session.disconnect()
})

test("the unknown server methods are never called, and ending the call clears the state", async () => {
    const {phone, asked} = await inCall(ALL_CAPABILITIES, {
        start_softphone_recording: {state: "on", recording_ref: "RE1", supported: true},
    })
    await press()
    expect(asked.map(([method]) => method)).not.toInclude("old")
    phone.session.disconnect()
    await advanceTime(200)
    await animationFrame()
    expect(phone.state.isRecording).toBe(false)
    expect(phone.recording_ref).toBe(null)
})
