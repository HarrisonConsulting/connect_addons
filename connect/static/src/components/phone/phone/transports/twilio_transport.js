/** @odoo-module **/
"use strict"

import {loadJS} from "@web/core/assets"
import {registry} from "@web/core/registry"
import {TelephonyTransport, TelephonyCall} from "@connect/components/phone/phone/transports/telephony_transport"

/**
 * Wraps a Twilio Voice SDK Call (the object handed to us by Device#connect()
 * and by the Device 'incoming' event) behind the TelephonyCall contract.
 *
 * Twilio-specific incoming-call metadata (call.customParameters /
 * call.parameters) is normalized here so phone.js never reads a
 * Twilio-shaped object directly.
 */
export class TwilioCall extends TelephonyCall {
    constructor(twilioCall, transport = null) {
        super()
        this._call = twilioCall
        this._transport = transport
        this._call.on('accept', (...args) => this._emit('accept', ...args))
        this._call.on('disconnect', (...args) => this._emit('disconnect', ...args))
        this._call.on('cancel', (...args) => this._emit('cancel', ...args))
        this._call.on('reject', (...args) => this._emit('reject', ...args))
        // Once the call is over the microphone is let go. The deferral lets the
        // Device drop the call from its own list first.
        for (const ev of ['disconnect', 'cancel', 'reject']) {
            this._call.on(ev, () => setTimeout(() => this._transport?._releaseInput(), 0))
        }
        // Call quality — Twilio-only. Normalized into a single 'quality' event
        // so phone.js's quality monitor stays provider-agnostic; a transport
        // that can't measure quality (e.g. a SIP stack) simply never
        // emits it.
        this._call.on('sample', (sample) => this._emit('quality', {kind: 'sample', sample}))
        this._call.on('warning', (name) => this._emit('quality', {kind: 'warning', name}))
        this._call.on('warning-cleared', (name) => this._emit('quality', {kind: 'warning-cleared', name}))
    }

    // Live-read from the underlying Twilio Call every access — CallSid in
    // particular is not populated at construction time for outgoing calls,
    // only once the call has progressed, so this must never be cached.
    get params() {
        const customParam = (key) => {
            if (this._call.customParameters && this._call.customParameters.get) {
                return this._call.customParameters.get(key)
            }
            return undefined
        }
        return {
            From: customParam('From') || this._call.parameters.From,
            CallerName: customParam('CallerName'),
            Partner: customParam('Partner'),
            autoAnswer: customParam('autoAnswer'),
            Origin: customParam('Origin'),
            CallSid: this._call.parameters?.CallSid,
        }
    }

    accept() {
        const pending = this._transport?._useChosenInput()
        if (pending) {
            // A caller who cancelled while the microphone was opening leaves no live call: let it go again.
            pending.then(() => {
                this._call.accept()
                this._transport?._releaseInput()
            })
        } else {
            this._call.accept()
        }
    }

    reject() {
        this._call.reject()
    }

    ignore() {
        this._call.ignore()
    }

    disconnect() {
        this._call.disconnect()
    }

    mute(shouldMute) {
        this._call.mute(shouldMute)
    }

    sendDigits(digits) {
        this._call.sendDigits(digits)
    }
}

/**
 * TwilioTransport — ITelephonyTransport implementation over the Twilio Voice
 * SDK (Twilio.Device). This is a faithful, mechanical wrapper: every event
 * name, error object, and call sequence Device/Call ever produced is passed
 * through unchanged. All reconnect/backoff decisions stay in phone_line.js
 * exactly as before this extraction — this class does not make any of those
 * decisions, it only translates the Twilio SDK's shape into the
 * ITelephonyTransport shape, and names what its error codes mean.
 *
 * Precedent: connect/static/src/components/phone/phone/phone.js
 * initUserAgent() (pre-extraction) — this is that logic, moved verbatim.
 */
export class TwilioTransport extends TelephonyTransport {
    static choosesAudioDevices = true

    static async loadDependencies() {
        await loadJS('/connect/static/src/lib/twilio.min.js')
    }

    /** 31009 and 31005 are the SDK's codes for a dead signalling connection. */
    static classifyError(error) {
        if (error.code === 31009 || error.code === 31005) {
            return 'transport'
        }
        return super.classifyError(error)
    }

    // hasCredential()/buildConnData() use TelephonyTransport's defaults —
    // Twilio's get_client_token() response already matches the {token, edge}
    // base shape.

    constructor() {
        super()
        this._device = null
        this._audioChoice = {input: '', speaker: '', ringtone: ''}
    }

    /**
     * Synchronous by design (not declared async): `new Twilio.Device()`
     * throws synchronously on bad config, and phone_line.js's call site relies on
     * a plain try/catch (no await) to catch that, exactly as it did before
     * this extraction. See the note on TelephonyTransport#init().
     */
    init(connData) {
        this._device = new Twilio.Device(connData.token, {
            edge: connData.edge,
            logLevel: 4,
            codecPreferences: ["opus", "pcmu"],
            // Allow incoming audio even if AudioContext is suspended initially
            allowIncomingWhileBusy: true,
        })
        this._device.on('tokenWillExpire', () => this._emit('authWillExpire'))
        this._device.on('registered', () => this._emit('registered'))
        this._device.on('unregistered', () => this._emit('unregistered'))
        this._device.on('error', (error) => this._emit('error', error))
        this._device.on('incoming', (twilioCall) => this._emit('incoming', new TwilioCall(twilioCall, this)))
    }

    async register() {
        return this._device.register()
    }

    async unregister() {
        if (!this._device) return
        return this._device.unregister()
    }

    destroy() {
        if (this._device) {
            this._device.destroy()
        }
        this._device = null
    }

    /**
     * Synchronous for the same reason as init(): Device#updateToken() throws
     * synchronously when the transport is already dead, and phone_line.js's
     * updateToken() relies on catching that synchronously.
     */
    updateAuth(connData) {
        this._device.updateToken(connData.token)
    }

    get state() {
        return this._device ? this._device.state : 'destroyed'
    }

    async dial(params) {
        const pending = this._useChosenInput()
        if (pending) await pending
        let twilioCall
        try {
            twilioCall = await this._device.connect({params})
        } catch (error) {
            this._releaseInput()
            throw error
        }
        return new TwilioCall(twilioCall, this)
    }

    setAudioDevices(choice) {
        this._audioChoice = {input: choice?.input || '', speaker: choice?.speaker || '', ringtone: choice?.ringtone || ''}
        const audio = this._device?.audio
        if (!audio) return
        if (audio.isOutputSelectionSupported) {
            this._setOutput(audio.speakerDevices, this._audioChoice.speaker)
            this._setOutput(audio.ringtoneDevices, this._audioChoice.ringtone)
        }
        // The microphone opens only for a call; a live call moves to the new one now.
        if (this._device.isBusy) this._useChosenInput()
    }

    _setOutput(collection, id) {
        const available = this._device.audio.availableOutputDevices
        const target = id && available.has(id) ? id : 'default'
        if (!available.has(target)) return
        collection.set(target).catch((error) => console.debug('Connect: output device not applied:', error))
    }

    /** Opens the chosen microphone for the call about to start: null when there is nothing to do, else a promise that never rejects. */
    _useChosenInput() {
        const audio = this._device?.audio
        if (!audio) return null
        const id = this._audioChoice.input
        const target = id && audio.availableInputDevices.has(id) ? id
            : (audio.inputDevice && audio.availableInputDevices.has('default') ? 'default' : null)
        if (!target) return null
        return audio.setInputDevice(target).catch((error) => console.debug('Connect: input device not applied:', error))
    }

    /** Lets the microphone go once no call needs it, so the browser stops showing it in use. */
    _releaseInput() {
        const audio = this._device?.audio
        if (!audio?.inputDevice || this._device.isBusy || this._device.calls?.length) return
        audio.unsetInputDevice().catch(() => {})
    }

    setIncomingAudio(enabled) {
        if (this._device && this._device.audio) {
            this._device.audio.incoming(enabled)
        }
    }
}

// Self-registers into the shared transport registry at load time — phone.js
// looks providers up here rather than importing a specific transport file,
// so a provider module can add its own entry without
// core ever referencing it, and vice versa: core doesn't need to know this
// file exists for a third provider to work.
registry.category("connect.telephony_transports").add("twilio", TwilioTransport)
