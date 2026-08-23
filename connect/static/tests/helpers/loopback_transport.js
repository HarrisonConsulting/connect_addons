/** @odoo-module **/
"use strict"
/**
 * A telephony transport that registers instantly and never leaves the browser.
 *
 * Every tour that needs the softphone's own DOM has to make get_client_token()
 * mint a credential, and a test credential is by definition one the provider
 * rejects. That rejection is not free: the SDK reaches sdk.twilio.com and the
 * signalling edge, fails, and logs. HttpCase sets had_failure on ANY browser
 * console error BEFORE consulting error_checker, so from the first rejection
 * the tour cannot pass however far it gets — which is why phone-mounting tours
 * used to be written short enough to finish before the errors arrived, and why
 * the longer ones were red no matter what they proved.
 *
 * Registered under the real provider key from web.assets_tests, so it exists
 * only in a test bundle and every tour that mounts the phone gets a transport
 * that reaches registered state with no network at all.
 */

import { registry } from "@web/core/registry"
import {
    TelephonyCall,
    TelephonyTransport,
} from "@connect/components/phone/phone/transports/telephony_transport"

export class LoopbackCall extends TelephonyCall {
    constructor(params = {}) {
        super()
        this._params = params
    }

    get params() {
        return {
            From: this._params.To || "",
            CallerName: "",
            Partner: undefined,
            autoAnswer: undefined,
            CallSid: "CA" + "0".repeat(32),
            ...this._params,
        }
    }

    accept() {
        this._emit("accept")
    }

    reject() {
        this._emit("reject")
    }

    disconnect() {
        this._emit("disconnect")
    }

    mute() {}

    sendDigits() {}
}

export class LoopbackTransport extends TelephonyTransport {
    static async loadDependencies() {}

    init() {
        this._state = "unregistered"
    }

    async register() {
        this._state = "registered"
        this._emit("registered")
    }

    async unregister() {
        this._state = "unregistered"
        this._emit("unregistered")
    }

    destroy() {
        this._state = "destroyed"
    }

    updateAuth() {}

    get state() {
        return this._state || "destroyed"
    }

    async dial(params) {
        const call = new LoopbackCall(params)
        // The far end "answers" as soon as the caller lets go of the stack, so
        // a tour can drive an in-call dialer without a second actor.
        Promise.resolve().then(() => call._emit("accept"))
        return call
    }

    setIncomingAudio() {}
}

registry.category("connect.telephony_transports").add("twilio", LoopbackTransport, {
    force: true,
})
