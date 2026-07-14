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
    constructor(twilioCall) {
        super()
        this._call = twilioCall
        this._call.on('accept', (...args) => this._emit('accept', ...args))
        this._call.on('disconnect', (...args) => this._emit('disconnect', ...args))
        this._call.on('cancel', (...args) => this._emit('cancel', ...args))
        this._call.on('reject', (...args) => this._emit('reject', ...args))
        // Call quality — Twilio-only. Normalized into a single 'quality' event
        // so phone.js's quality monitor stays provider-agnostic; a transport
        // that can't measure quality (e.g. VoiceTel/SIP.js) simply never
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
            CallSid: this._call.parameters?.CallSid,
        }
    }

    accept() {
        this._call.accept()
    }

    reject() {
        this._call.reject()
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
 * through unchanged. All reconnect/backoff/error-classification decisions
 * stay in phone.js exactly as before this extraction — this class does not
 * make any of those decisions, it only translates the Twilio SDK's shape
 * into the ITelephonyTransport shape.
 *
 * Precedent: connect/static/src/components/phone/phone/phone.js
 * initUserAgent() (pre-extraction) — this is that logic, moved verbatim.
 */
export class TwilioTransport extends TelephonyTransport {
    static async loadDependencies() {
        await loadJS('/connect/static/src/lib/twilio.min.js')
    }

    // hasCredential()/buildConnData() use TelephonyTransport's defaults —
    // Twilio's get_client_token() response already matches the {token, edge}
    // base shape.

    constructor() {
        super()
        this._device = null
    }

    /**
     * Synchronous by design (not declared async): `new Twilio.Device()`
     * throws synchronously on bad config, and phone.js's call site relies on
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
        this._device.on('incoming', (twilioCall) => this._emit('incoming', new TwilioCall(twilioCall)))
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
     * synchronously when the transport is already dead, and phone.js's
     * updateToken() relies on catching that synchronously.
     */
    updateAuth(connData) {
        this._device.updateToken(connData.token)
    }

    get state() {
        return this._device ? this._device.state : 'destroyed'
    }

    async dial(params) {
        const twilioCall = await this._device.connect({params})
        return new TwilioCall(twilioCall)
    }

    setIncomingAudio(enabled) {
        if (this._device && this._device.audio) {
            this._device.audio.incoming(enabled)
        }
    }
}

// Self-registers into the shared transport registry at load time — phone.js
// looks providers up here rather than importing a specific transport file,
// so a provider module (e.g. connect_voicetel) can add its own entry without
// core ever referencing it, and vice versa: core doesn't need to know this
// file exists for a third provider to work.
registry.category("connect.telephony_transports").add("twilio", TwilioTransport)
