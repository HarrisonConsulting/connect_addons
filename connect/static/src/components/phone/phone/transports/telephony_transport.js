/** @odoo-module **/
"use strict"

/**
 * Minimal synchronous event emitter shared by TelephonyTransport and
 * TelephonyCall. Not part of the public contract — an implementation detail
 * of these two base classes only.
 */
class Emitter {
    constructor() {
        this._listeners = {}
    }

    on(event, cb) {
        if (!this._listeners[event]) {
            this._listeners[event] = []
        }
        this._listeners[event].push(cb)
        return this
    }

    _emit(event, ...args) {
        for (const cb of this._listeners[event] || []) {
            cb(...args)
        }
    }
}

/**
 * ITelephonyTransport — the contract every browser-softphone provider
 * (Twilio, ...) implements so phone.js never touches a
 * provider-specific SDK object directly.
 *
 * Lifecycle: init() -> register() -> ...calls happen via dial()/'incoming'...
 * -> unregister()/destroy(). updateAuth() re-credentials an already-init'd
 * transport in place (JWT refresh for Twilio, password rotation for a
 * SIP provider) without a full teardown+rebuild.
 *
 * init()/updateAuth() are deliberately NOT required to be async: Twilio's
 * Device constructor and Device#updateToken() both throw synchronously on
 * bad input, and callers (phone_line.js) rely on a plain try/catch, with no
 * await, to catch that at the exact call site — declaring these `async`
 * in an implementation would silently turn that synchronous throw into an
 * unhandled promise rejection. Implementations that are inherently
 * asynchronous (e.g. a SIP.js registration that only reports success/failure
 * via events) should still return promptly from init()/updateAuth() and
 * signal outcome via the 'registered'/'unregistered'/'error' events.
 *
 * Events (via on(event, cb)):
 *   'registered'     - transport is registered and can place/receive calls
 *   'unregistered'   - transport lost or dropped registration
 *   'error'          - provider-specific error object, passed through
 *                      unnormalized; classifyError() says what it means
 *   'incoming'       - fired with a TelephonyCall for a new inbound call
 *   'authWillExpire' - the current credential is about to expire/need
 *                      rotation; caller should fetch a fresh one and call
 *                      updateAuth()
 */
export class TelephonyTransport extends Emitter {
    /**
     * Load this provider's SDK (loadJS) before the transport is constructed.
     * Default: no-op. Each provider's transport loads its own dependency —
     * phone.js never needs to know which file a given provider requires.
     */
    static async loadDependencies() {}

    /**
     * Whether phone.js plays the incoming ring itself. False when the SDK
     * rings on its own, as Twilio's Device does; a SIP stack that does not
     * ring sets this to true.
     */
    static ringsInApp = false

    /**
     * @param {Object} tokenData - the raw get_client_token() response.
     * @returns {boolean} whether tokenData carries a usable credential for
     *   this provider. Default matches Twilio's {token, ...} shape;
     *   providers with a differently-shaped response (e.g. a SIP
     *   {password, ...}, no 'token' key at all) must override this.
     */
    static hasCredential(tokenData) {
        return !!tokenData.token
    }

    /**
     * @param {Object} tokenData - the raw get_client_token() response.
     * @returns {Object} the connData shape this transport's init()/
     *   updateAuth() expect. Default matches Twilio's {token, edge}.
     */
    static buildConnData(tokenData) {
        return {token: tokenData.token, edge: tokenData.edge}
    }

    /**
     * Whether a call on this transport has its audio in the browser. Mute and
     * DTMF need that, so the phone offers them only when this is true. What the
     * server can do for a call comes from the line's capabilities, not from here.
     */
    static needsBrowserMedia = true

    /**
     * What an 'error' event's object means for the line's recovery. Override for
     * a provider's own codes and call super for the rest.
     *
     * @param {Object} error
     * @returns {'transport'|'auth'|'unsupported'|'other'} 'transport' is a dead
     *   connection and 'auth' a rejected credential, and both rebuild with a new
     *   credential. 'unsupported' stops the phone. 'other' is shown as it is.
     */
    static classifyError(error) {
        if (error.name === 'AccessTokenExpired' || error.name === 'AccessTokenInvalid') {
            return 'auth'
        }
        if (error.name === 'NotSupportedError') {
            return 'unsupported'
        }
        return 'other'
    }

    /**
     * @param {Object} connData - provider-specific connection data, e.g.
     *   {token, edge} for Twilio or {sip_uri, username, password, wss_server}
     *   for a SIP provider.
     */
    init(connData) {
        throw new Error(`${this.constructor.name}: init() not implemented`)
    }

    async register() {
        throw new Error(`${this.constructor.name}: register() not implemented`)
    }

    async unregister() {
        throw new Error(`${this.constructor.name}: unregister() not implemented`)
    }

    destroy() {
        throw new Error(`${this.constructor.name}: destroy() not implemented`)
    }

    /** @param {Object} connData - see init() */
    updateAuth(connData) {
        throw new Error(`${this.constructor.name}: updateAuth() not implemented`)
    }

    /** @returns {'destroyed'|'unregistered'|'registered'} */
    get state() {
        throw new Error(`${this.constructor.name}: state getter not implemented`)
    }

    /**
     * @param {Object} params - e.g. {To, Called}
     * @returns {Promise<TelephonyCall>}
     */
    async dial(params) {
        throw new Error(`${this.constructor.name}: dial() not implemented`)
    }

    /** @param {boolean} enabled - whether incoming-call ringtone/audio should be audible */
    setIncomingAudio(enabled) {
        throw new Error(`${this.constructor.name}: setIncomingAudio() not implemented`)
    }

    /**
     * The call a further press would only repeat, for a transport whose dial takes
     * long enough to be pressed twice.
     * @returns {TelephonyCall|null}
     */
    get liveCall() {
        return null
    }

    /** @returns {string|null} why this transport must not dial right now, in words */
    get dialRefusal() {
        return null
    }

    /**
     * What the server published about a call, for a transport whose calls the
     * server drives. See connect.settings _notify_call_state.
     * @param {Object} payload
     */
    receiveCallState(payload) {}
}

/**
 * TelephonyCall — one active/pending call, normalized across providers.
 *
 * `params` (a getter, always live-read from the underlying provider object —
 * never a one-time snapshot, since call sites in phone.js read fields like
 * CallSid at different points in the call lifecycle and expect the current
 * value each time) carries whatever incoming-call metadata the provider
 * exposes, normalized to a common set of keys so phone.js never reads a
 * provider-shaped object (e.g. Twilio's session.customParameters /
 * session.parameters) directly:
 *   {From, CallerName, Partner, autoAnswer, Origin, CallSid}
 * This is an extension beyond the locked accept/reject/disconnect/mute/
 * sendDigits/on surface — it exists to carry the incoming-call metadata
 * phone.js already depended on before this transport-seam extraction.
 *
 * Events (via on(event, cb)):
 *   'accept'     - call was answered (local accept() for incoming, far end
 *                  answering for outgoing)
 *   'disconnect' - call ended normally
 *   'cancel'     - caller hung up before answer / call was cancelled
 *   'reject'     - call was rejected
 *   'quality'    - OPTIONAL, Twilio-only. {kind: 'sample'|'warning'|
 *                  'warning-cleared', ...}. Transports that can't measure
 *                  quality (e.g. a SIP stack) simply never emit it; callers
 *                  must not assume it ever fires.
 *   'progress'   - OPTIONAL. The call is being set up at the far end, before
 *                  'accept'. Transports that cannot tell never emit it.
 *   'callState'  - OPTIONAL. {state, reason_code, detail}, for a call whose
 *                  progress the panel has to put in words because none of it is
 *                  heard in the browser.
 *
 * OPTIONAL method, deliberately not defined here so that its presence can be
 * tested: hold(on), for a transport that holds its own media. The Hold button
 * goes by the line's capabilities either way.
 */
export class TelephonyCall extends Emitter {
    /** @returns {{From, CallerName, Partner, autoAnswer, Origin, CallSid}} */
    get params() {
        throw new Error(`${this.constructor.name}: params getter not implemented`)
    }

    /** How long the call has already been up when 'accept' fires. */
    get elapsedSeconds() {
        return 0
    }

    accept() {
        throw new Error(`${this.constructor.name}: accept() not implemented`)
    }

    reject() {
        throw new Error(`${this.constructor.name}: reject() not implemented`)
    }

    /** Stop ringing here without answering or ending the call for other devices. */
    ignore() {
        throw new Error(`${this.constructor.name}: ignore() not implemented`)
    }

    disconnect() {
        throw new Error(`${this.constructor.name}: disconnect() not implemented`)
    }

    /** @param {boolean} shouldMute */
    mute(shouldMute) {
        throw new Error(`${this.constructor.name}: mute() not implemented`)
    }

    /** @param {string} digits */
    sendDigits(digits) {
        throw new Error(`${this.constructor.name}: sendDigits() not implemented`)
    }
}
