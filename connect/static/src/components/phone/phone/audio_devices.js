/** @odoo-module **/
"use strict"
/**
 * Which microphone and speakers the softphone uses. The choice belongs to this
 * browser, since the devices do, so it is kept in localStorage. Losing it must
 * never stop the phone, so every access is guarded.
 */

import {browser} from "@web/core/browser/browser"

export const AUDIO_DEVICES_KEY = 'connect_audio_devices'

export function loadAudioDeviceChoice() {
    try {
        const stored = JSON.parse(browser.localStorage.getItem(AUDIO_DEVICES_KEY) || '{}')
        const pick = (value) => (typeof value === 'string' ? value : '')
        return {input: pick(stored?.input), speaker: pick(stored?.speaker), ringtone: pick(stored?.ringtone)}
    } catch {
        return {input: '', speaker: '', ringtone: ''}
    }
}

export function saveAudioDeviceChoice(choice) {
    try {
        browser.localStorage.setItem(AUDIO_DEVICES_KEY, JSON.stringify(choice))
    } catch {
        // Not remembering the choice is not worth interrupting the phone.
    }
}

/** The only code that touches the browser's device API, so tests patch this object. */
export const audioDeviceSource = {
    async enumerate() {
        const m = browser.navigator.mediaDevices
        return m?.enumerateDevices ? m.enumerateDevices() : []
    },
    outputSelectable() {
        return typeof HTMLMediaElement !== 'undefined' && 'setSinkId' in HTMLMediaElement.prototype
    },
    async requestPermission() {
        const s = await browser.navigator.mediaDevices.getUserMedia({audio: true})
        s.getTracks().forEach((t) => t.stop())
    },
    onChange(cb) {
        const m = browser.navigator.mediaDevices
        if (!m?.addEventListener) return () => {}
        m.addEventListener('devicechange', cb)
        return () => m.removeEventListener('devicechange', cb)
    },
}

// Chromium lists these beside the real devices; "System default" already stands for them.
const PSEUDO = new Set(['default', 'communications'])

export async function listAudioDevices() {
    let devices = []
    try {
        devices = await audioDeviceSource.enumerate()
    } catch {
        devices = []
    }
    const pick = (kind) => devices
        .filter((d) => d.kind === kind && d.deviceId && !PSEUDO.has(d.deviceId))
        .map((d) => ({id: d.deviceId, label: d.label}))
    // Labels stay empty until the browser has granted microphone access.
    const needsPermission = devices.some((d) => d.kind === 'audioinput')
        && !devices.some((d) => d.kind === 'audioinput' && d.label)
    const speakers = pick('audiooutput').filter((d) => d.label)
    return {
        inputs: needsPermission ? [] : pick('audioinput'),
        speakers,
        needsPermission,
        // Firefox has setSinkId but lists no outputs, so the select stays hidden there.
        outputSelectable: audioDeviceSource.outputSelectable() && speakers.length > 0,
    }
}
