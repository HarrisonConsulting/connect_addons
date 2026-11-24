/** @odoo-module **/
"use strict"
/**
 * Choosing the microphone and speaker in Audio Settings: what the panel lists,
 * what it remembers, and what the Twilio transport does with the choice.
 */

import {after, advanceTime, animationFrame, describe, expect, test} from "@odoo/hoot"
import {click, select} from "@odoo/hoot-dom"
import {onRpc, patchWithCleanup} from "@web/../tests/web_test_helpers"
import {browser} from "@web/core/browser/browser"
import {registry} from "@web/core/registry"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {AUDIO_DEVICES_KEY, audioDeviceSource} from "@connect/components/phone/phone/audio_devices"
import {TwilioCall, TwilioTransport} from "@connect/components/phone/phone/transports/twilio_transport"
import {LoopbackTransport} from "@connect/../tests/helpers/loopback_transport"
import {mountLines} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const CATEGORY = "connect.telephony_transports"
const applied = []

class DeviceSpyTransport extends LoopbackTransport {
    static choosesAudioDevices = true
    setAudioDevices(choice) {
        applied.push(choice)
    }
}

const spyLine = (key = "device_spy") => ({
    key, provider: key, media: "browser", capabilities: {}, token_data: {token: "t"},
})

function fixture({selectable = true, labeled = true} = {}) {
    const state = {
        permissionAsked: 0,
        changeCallback: null,
        devices: [
            {kind: "audioinput", deviceId: "default", label: labeled ? "Default" : ""},
            {kind: "audioinput", deviceId: "mic-usb", label: labeled ? "USB Mic" : ""},
            {kind: "audiooutput", deviceId: "default", label: labeled ? "Default" : ""},
            {kind: "audiooutput", deviceId: "spk-hdmi", label: labeled ? "HDMI" : ""},
        ],
    }
    patchWithCleanup(audioDeviceSource, {
        enumerate: async () => state.devices,
        outputSelectable: () => selectable,
        requestPermission: async () => {
            state.permissionAsked++
            for (const d of state.devices) {
                d.label = d.label || d.deviceId
            }
        },
        onChange: (cb) => {
            state.changeCallback = cb
            return () => {}
        },
    })
    return state
}

async function mountSpy(keys = ["device_spy"]) {
    applied.length = 0
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    for (const key of keys) {
        registry.category(CATEGORY).add(key, DeviceSpyTransport, {force: true})
        after(() => registry.category(CATEGORY).remove(key))
    }
    after(() => browser.localStorage.removeItem(AUDIO_DEVICES_KEY))
    const phone = await mountLines(keys.map(spyLine))
    await animationFrame()
    return phone
}

const openSettings = async () => {
    await click("button[aria-label='Audio Settings']")
    await animationFrame()
}
const optionTexts = (selector) => [...document.querySelectorAll(`${selector} option`)].map((o) => o.textContent.trim())
const stored = () => JSON.parse(browser.localStorage.getItem(AUDIO_DEVICES_KEY))

test("the panel lists real devices and hides the pseudo entries", async () => {
    browser.localStorage.removeItem(AUDIO_DEVICES_KEY)
    fixture()
    await mountSpy()
    await openSettings()
    expect(optionTexts(".o_audio_device_input")).toEqual(["System default", "USB Mic"])
    expect(".o_audio_device_speaker").toHaveCount(1)
})

test("choosing a microphone applies and remembers it", async () => {
    browser.localStorage.removeItem(AUDIO_DEVICES_KEY)
    fixture()
    await mountSpy()
    await openSettings()
    await select("mic-usb", {target: ".o_audio_device_input"})
    await animationFrame()
    expect(applied.at(-1)).toEqual({input: "mic-usb", speaker: "", ringtone: ""})
    expect(stored()).toEqual({input: "mic-usb", speaker: "", ringtone: ""})
})

test("a remembered choice is applied when the line registers", async () => {
    browser.localStorage.setItem(AUDIO_DEVICES_KEY, JSON.stringify({input: "mic-usb"}))
    fixture()
    await mountSpy()
    expect(applied.length).toBeGreaterThan(0)
    expect(applied[0].input).toBe("mic-usb")
})

test("a remembered device that is gone shows the default but stays stored", async () => {
    browser.localStorage.setItem(AUDIO_DEVICES_KEY, JSON.stringify({input: "gone"}))
    fixture()
    await mountSpy()
    await openSettings()
    expect(".o_audio_device_input").toHaveValue("")
    expect(stored().input).toBe("gone")
})

test("no speaker select where output cannot be chosen", async () => {
    browser.localStorage.removeItem(AUDIO_DEVICES_KEY)
    fixture({selectable: false})
    await mountSpy()
    await openSettings()
    expect(".o_audio_device_speaker").toHaveCount(0)
    expect(".o_audio_device_output_note").toHaveCount(1)
})

test("unlabeled devices ask for microphone access first", async () => {
    browser.localStorage.removeItem(AUDIO_DEVICES_KEY)
    const state = fixture({labeled: false})
    await mountSpy()
    await openSettings()
    expect(".o_audio_device_allow").toHaveCount(1)
    expect(".o_audio_device_input").toHaveCount(0)
    await click(".o_audio_device_allow")
    await animationFrame()
    expect(state.permissionAsked).toBe(1)
    expect(".o_audio_device_input").toHaveCount(1)
})

test("a device change refreshes the lists", async () => {
    browser.localStorage.removeItem(AUDIO_DEVICES_KEY)
    const state = fixture()
    await mountSpy()
    await openSettings()
    state.devices.push({kind: "audioinput", deviceId: "mic-2", label: "Headset"})
    state.changeCallback()
    await animationFrame()
    expect(optionTexts(".o_audio_device_input")).toEqual(["System default", "USB Mic", "Headset"])
})

test("Change in the From row opens the device section", async () => {
    browser.localStorage.removeItem(AUDIO_DEVICES_KEY)
    fixture()
    const phone = await mountSpy(["device_spy", "device_spy_2"])
    phone.state.isDisplay = true
    await animationFrame()
    await click(".o_phone_line_change")
    await animationFrame()
    await animationFrame()
    expect(phone.state.isAudioSettings).toBe(true)
    expect(document.activeElement).toBe(document.querySelector(".o_audio_device_input"))
})

test("an unreadable store leaves the choice empty", async () => {
    fixture()
    patchWithCleanup(browser.localStorage, {
        getItem(key) {
            if (key === AUDIO_DEVICES_KEY) {
                throw new Error("blocked")
            }
            return super.getItem(key)
        },
    })
    const phone = await mountSpy()
    expect(phone.state.audioDeviceChoice).toEqual({input: "", speaker: "", ringtone: ""})
})

// ---- Twilio transport ----

function fakeDevice({outputs = true, busy = false, calls = [], inputDevice = null} = {}) {
    const log = []
    const device = {
        isBusy: busy,
        calls,
        audio: {
            isOutputSelectionSupported: outputs,
            availableInputDevices: new Map([["default", {}], ["mic-usb", {}]]),
            availableOutputDevices: new Map([["default", {}], ["spk-hdmi", {}]]),
            inputDevice,
            speakerDevices: {set: async (id) => void log.push(`speaker:${id}`)},
            ringtoneDevices: {set: async (id) => void log.push(`ringtone:${id}`)},
            setInputDevice: async (id) => {
                log.push(`input:${id}`)
                device.audio.inputDevice = {deviceId: id}
            },
            unsetInputDevice: async () => void log.push("release"),
        },
        connect: async () => {
            log.push("connect")
            return fakeCall(log)
        },
    }
    return {device, log}
}

function fakeCall(log) {
    const handlers = {}
    return {
        on(ev, cb) {
            (handlers[ev] ||= []).push(cb)
        },
        emit(ev) {
            (handlers[ev] || []).forEach((cb) => cb())
        },
        parameters: {},
        customParameters: new Map(),
        accept: () => void log.push("accept"),
    }
}

function twilio(options, choice) {
    const transport = new TwilioTransport()
    const fake = fakeDevice(options)
    transport._device = fake.device
    if (choice) transport.setAudioDevices(choice)
    fake.log.length = 0
    return {transport, ...fake}
}

const MIC = {input: "mic-usb", speaker: "", ringtone: ""}

test("idle choice sets outputs and leaves the microphone closed", async () => {
    const {transport, log} = twilio()
    transport.setAudioDevices({input: "mic-usb", speaker: "spk-hdmi", ringtone: ""})
    expect(log).toEqual(["speaker:spk-hdmi", "ringtone:default"])
})

test("no output entries where output selection is unsupported", async () => {
    const {transport, log} = twilio({outputs: false})
    transport.setAudioDevices({input: "mic-usb", speaker: "spk-hdmi", ringtone: ""})
    expect(log).toEqual([])
})

test("a gone speaker falls back to the default", async () => {
    const {transport, log} = twilio()
    transport.setAudioDevices({input: "", speaker: "gone", ringtone: ""})
    expect(log).toInclude("speaker:default")
})

test("dial opens the chosen microphone before connecting", async () => {
    const {transport, log} = twilio({}, MIC)
    await transport.dial({})
    expect(log).toEqual(["input:mic-usb", "connect"])
})

test("dial with no choice only connects", async () => {
    const {transport, log} = twilio()
    await transport.dial({})
    expect(log).toEqual(["connect"])
})

test("accepting opens the chosen microphone first", async () => {
    const {transport, log} = twilio({}, MIC)
    new TwilioCall(fakeCall(log), transport).accept()
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
    expect(log).toEqual(["input:mic-usb", "accept", "release"])
})

test("accepting a call that was cancelled while the microphone opened releases it", async () => {
    const {transport, log} = twilio({}, MIC)
    new TwilioCall(fakeCall(log), transport).accept()
    for (let i = 0; i < 5; i++) await Promise.resolve()
    expect(log.slice(-2)).toEqual(["accept", "release"])
})

test("accepting a call that stays live keeps the microphone", async () => {
    const {transport, device, log} = twilio({}, MIC)
    device.isBusy = true
    new TwilioCall(fakeCall(log), transport).accept()
    for (let i = 0; i < 5; i++) await Promise.resolve()
    expect(log).toEqual(["input:mic-usb", "accept"])
})

test("a failed dial releases the microphone it opened", async () => {
    const {transport, device, log} = twilio({}, MIC)
    device.connect = async () => {
        log.push("connect")
        throw new Error("denied")
    }
    let failure
    await transport.dial({}).catch((error) => (failure = error))
    expect(failure?.message).toBe("denied")
    expect(log).toEqual(["input:mic-usb", "connect", "release"])
})

test("accepting with no choice is synchronous", async () => {
    const {transport, log} = twilio()
    new TwilioCall(fakeCall(log), transport).accept()
    expect(log).toEqual(["accept"])
})

test("the microphone is released when the last call ends", async () => {
    const {transport, device, log} = twilio({inputDevice: {deviceId: "mic-usb"}})
    const call = fakeCall(log)
    new TwilioCall(call, transport)
    call.emit("disconnect")
    await advanceTime(0)
    expect(log).toInclude("release")
    log.length = 0
    device.calls = [{}]
    call.emit("disconnect")
    await advanceTime(0)
    expect(log).not.toInclude("release")
})

test("a live call moves to the new microphone at once", async () => {
    const {transport, log} = twilio({busy: true})
    transport.setAudioDevices(MIC)
    expect(log).toInclude("input:mic-usb")
})
