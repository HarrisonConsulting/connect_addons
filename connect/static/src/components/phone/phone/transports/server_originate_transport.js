/** @odoo-module **/
"use strict"

import {_t} from "@web/core/l10n/translation"
import {RPCError} from "@web/core/network/rpc"
import {TelephonyTransport, TelephonyCall} from "@connect/components/phone/phone/transports/telephony_transport"

// How long a call nobody can confirm keeps the phone from dialing again. It may
// still be ringing, so a redial could ring the same phone twice. [PROPOSAL]
export const UNKNOWN_CLEAR_MS = 120000
// How long a call being set up may go with no word from the server before it
// counts as one nobody can confirm. [PROPOSAL]
export const SETUP_SILENCE_MS = 90000

// What the server's answer means when it is an exception: these say the call was
// refused. Anything else, a lost connection included, says nothing about the call.
const REFUSALS = new Set([
    'odoo.exceptions.UserError',
    'odoo.exceptions.AccessError',
    'odoo.exceptions.ValidationError',
])

// A call only moves forward through these. A late event cannot move it back.
const SETUP_ORDER = ['calling', 'first_leg_answered', 'connecting', 'in_call']

export function callUnconfirmedWords() {
    return _t("We could not confirm this call. It may still ring. Do not redial yet.")
}

function newIdempotencyKey() {
    const bytes = crypto.getRandomValues(new Uint8Array(24))
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

/**
 * A call the server placed: the person's own phone rings first, then the far end.
 * Nothing about it happens in this browser, so it is driven only by what the
 * server publishes. Its ref is the idempotency key the press sent, which lets an
 * event find it even when the answer to the originate request was lost.
 *
 * Besides the usual lifecycle events it emits 'callState' with {state,
 * reason_code, detail} for what the panel says.
 */
export class ServerOriginateCall extends TelephonyCall {
    constructor(transport, number) {
        super()
        this._transport = transport
        this.ref = newIdempotencyKey()
        this.number = number
        this.state = null
        // Whether the panel has let go of this call. The server's last word on it
        // can still arrive, and the panel can still say it.
        this.closed = false
        this.terminal = false
        this.placed = Promise.resolve(false)
        this._accepted = false
        this._pickedUpAt = null
        this._timer = null
    }

    get params() {
        return {From: this.number, CallSid: this.ref}
    }

    get elapsedSeconds() {
        if (!this._pickedUpAt) {
            return 0
        }
        return Math.max(0, Math.floor((Date.now() - this._pickedUpAt) / 1000))
    }

    _notePickedUp(since) {
        if (!this._pickedUpAt) {
            const parsed = since ? Date.parse(`${since.replace(' ', 'T')}Z`) : NaN
            this._pickedUpAt = Number.isNaN(parsed) ? Date.now() : parsed
        }
    }

    _isStale(state) {
        if (state === 'unknown') {
            return false
        }
        if (this.closed) {
            return true
        }
        const at = SETUP_ORDER.indexOf(state)
        return at < 0 || (this.state !== 'unknown' && at <= SETUP_ORDER.indexOf(this.state))
    }

    _armTimer(state) {
        this.stopTimer()
        if (state === 'unknown') {
            this._timer = setTimeout(() => this.receive({state: 'failed', reason_code: 'unconfirmed'}), UNKNOWN_CLEAR_MS)
        } else if (SETUP_ORDER.includes(state) && state !== 'in_call') {
            this._timer = setTimeout(() => this.receive({state: 'unknown'}), SETUP_SILENCE_MS)
        }
    }

    stopTimer() {
        clearTimeout(this._timer)
        this._timer = null
    }

    /** One state for this call, from the server or from what this browser saw. */
    receive({state, reason_code = '', since = false, detail = ''}) {
        const isEnd = state === 'ended' || state === 'failed'
        if (this.terminal || (!isEnd && this._isStale(state))) {
            return
        }
        this.state = state
        this.terminal = isEnd
        if (isEnd) {
            this._transport.forget(this)
        }
        this._armTimer(state)
        if (state === 'first_leg_answered' || state === 'in_call') {
            this._notePickedUp(since)
        }
        this._emit('callState', {state, reason_code, detail})
        if (this.closed) {
            return
        }
        if (state === 'in_call' && !this._accepted) {
            this._accepted = true
            this._emit('accept')
        } else if (state === 'first_leg_answered' || state === 'connecting') {
            this._emit('progress')
        } else if (isEnd) {
            this.closed = true
            this._emit(state === 'ended' ? 'disconnect' : 'cancel')
        }
    }

    /** Hanging up: the panel lets go now, the server is told when it can be. */
    disconnect() {
        if (this.closed) {
            return
        }
        this.closed = true
        // A call nobody can confirm stays that way, and keeps the phone from redialing.
        if (this.state !== 'unknown') {
            this.stopTimer()
            this._emit('callState', {state: 'ended', reason_code: '', detail: ''})
            this.state = 'ended'
        }
        this._emit('disconnect')
        this._transport.hangUp(this)
    }
}

/**
 * The transport of every bridge line: a provider that rings the person's own phone
 * writes no JavaScript, only the server side of originate_call and
 * _softphone_hangup_<provider>, and publishes through connect.settings
 * _notify_call_state with the idempotency key it was given as call_ref.
 *
 * Chosen by the line's media, not through the provider registry.
 */
export class ServerOriginateTransport extends TelephonyTransport {
    static needsBrowserMedia = false

    static hasCredential(tokenData) {
        return tokenData.media === 'bridge'
    }

    static buildConnData() {
        return {}
    }

    /** @param {PhoneLine} line */
    constructor(line) {
        super()
        this._line = line
        this._state = 'unregistered'
        // The latest call, which the panel shows.
        this._call = null
        // Every call the server may still say something about, by ref. A quick
        // redial must not make an earlier call unreachable: its hang-up can still
        // fail, and it can still go unconfirmed.
        this._calls = new Map()
    }

    init() {}

    async register() {
        this._state = 'registered'
        this._emit('registered')
    }

    async unregister() {
        this._state = 'unregistered'
    }

    destroy() {
        this._state = 'destroyed'
        for (const call of this._calls.values()) {
            call.stopTimer()
        }
    }

    forget(call) {
        this._calls.delete(call.ref)
    }

    updateAuth() {}

    setIncomingAudio() {}

    get state() {
        return this._state
    }

    get liveCall() {
        return this._call && !this._call.closed ? this._call : null
    }

    get dialRefusal() {
        const unconfirmed = [...this._calls.values()].some((call) => call.state === 'unknown')
        return unconfirmed ? callUnconfirmedWords() : null
    }

    receiveCallState(payload) {
        this._calls.get(payload.call_ref)?.receive(payload)
    }

    get _rpcContext() {
        const toolId = this._line.toolId
        return toolId ? {context: {connect_tool_id: toolId}} : {}
    }

    /**
     * Returned before anything is sent, so that whoever dials can listen first.
     * Each press is its own call with its own key.
     */
    async dial({To, CallerId}) {
        const call = new ServerOriginateCall(this, To)
        call.placed = this._place(call, To, CallerId)
        this._call = call
        this._calls.set(call.ref, call)
        return call
    }

    /** @returns {Promise<boolean>} whether the call might exist on the server */
    async _place(call, number, callerId) {
        await new Promise((resolve) => setTimeout(resolve))
        if (call.closed) {
            return false
        }
        call.receive({state: 'calling'})
        const kwargs = {idempotency_key: call.ref, ...this._rpcContext}
        if (callerId) {
            kwargs.caller_id = callerId
        }
        try {
            await this._line.phone.orm.call('connect.settings', 'originate_call', [number], kwargs)
            return true
        } catch (error) {
            if (error instanceof RPCError && REFUSALS.has(error.exceptionName)) {
                call.receive({state: 'failed', reason_code: 'refused', detail: error.data?.message || error.message})
                return false
            }
            console.warn('Connect: no answer to the originate request:', error?.message || error)
            if (call.state === 'calling' || call.closed) {
                call.receive({state: 'unknown'})
            }
            return true
        }
    }

    async hangUp(call) {
        if (!await call.placed || call.terminal) {
            return
        }
        const payload = {provider: this._line.provider, call_ref: call.ref, line_key: this._line.key}
        try {
            await this._line.phone.orm.call('connect.channel', 'hangup_softphone_call', [payload], this._rpcContext)
        } catch (error) {
            console.warn('Connect: no hang-up for the call:', error?.message || error)
            call.receive({state: 'unknown'})
        }
    }
}
