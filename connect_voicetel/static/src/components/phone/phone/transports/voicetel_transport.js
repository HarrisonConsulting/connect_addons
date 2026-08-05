/** @odoo-module **/
"use strict"

import {loadJS} from "@web/core/assets"
import {registry} from "@web/core/registry"
import {TelephonyTransport, TelephonyCall} from "@connect/components/phone/phone/transports/telephony_transport"

/**
 * Wraps a SIP.js 0.15.11 legacy Session (the object handed to us by
 * SIP.UA#invite() and by the UA 'invite' event) behind the TelephonyCall
 * contract.
 *
 * Precedent: VoiceTel's own reference implementation (github.com/voicetel/phone,
 * phone.html) — accept/reject/hangup/mute/dtmf/remote-audio calls below are
 * transcribed from it verbatim; only the event-name normalization into the
 * accept/disconnect/cancel/reject surface is new (that reference is a
 * single-page app with its own ad-hoc UI wiring, not a reusable transport).
 */
export class VoiceTelCall extends TelephonyCall {
    constructor(sipSession) {
        super()
        this._session = sipSession
        this._terminated = false
        this._remoteAudioEl = null
        this._wireSession()
    }

    _wireSession() {
        const session = this._session

        session.on('accepted', () => {
            this._attachRemoteAudio()
            this._emit('accept')
        })
        session.on('trackAdded', () => this._attachRemoteAudio())

        // 'bye'/'rejected'/'failed' each fire for their specific termination
        // path; SIP.js also fires the catch-all 'terminated' after any of
        // them. Guard so we only ever emit one of accept/disconnect/cancel/
        // reject per call. Precedent for the event *names* themselves is the
        // VoiceTel reference's own documented session-event list; the
        // classification into TelephonyCall's four termination events below
        // is this transport's own normalization, unverified against a live
        // account.
        session.on('bye', () => this._terminate('disconnect'))
        session.on('rejected', () => this._terminate('reject'))
        // No answer yet when the session dies == the caller/far end gave up
        // before we (or they) connected — normalize to 'cancel', matching
        // Twilio's own accept-vs-cancel split (see TwilioCall).
        session.on('failed', () => this._terminate(session.hasAnswer ? 'disconnect' : 'cancel'))
        session.on('terminated', () => this._terminate(session.hasAnswer ? 'disconnect' : 'cancel'))
    }

    _terminate(eventName) {
        if (this._terminated) return
        this._terminated = true
        this._teardownRemoteAudio()
        this._emit(eventName)
    }

    /* SIP.js (0.15 legacy API) throws INVALID_STATE_ERROR when bye/cancel/
     * reject/accept is invoked on a session that already ended — which
     * happens whenever remote termination (a rejection like 403, or the far
     * end hanging up first) races the user's button click. Already-ended IS
     * the desired outcome of ending a call: normalize it to termination
     * instead of letting the throw abort the caller's whole cleanup
     * sequence (that abort was the meeting-observed cascade: invalid state
     * error -> stuck call UI -> follow-on status/bus errors). */
    _safeSessionOp(op, terminalEvent) {
        if (this._terminated) return
        try {
            op()
        } catch (e) {
            console.warn('Connect: VoiceTelTransport: session op on ended session, normalizing:', e)
            this._terminate(terminalEvent)
        }
    }

    // Live-read from the underlying SIP.js session every access, same
    // rationale as TwilioCall#params. Partner/autoAnswer have no SIP
    // equivalent here: those are populated from Twilio TwiML <Parameter>
    // elements set by render_client(), and render_sip() (the VoiceTel
    // inbound-routing path, per the locked design) has no analogous
    // mechanism — so there's never a real value for a VoiceTel call.
    get params() {
        const identity = this._session.remoteIdentity
        return {
            From: (identity && identity.uri && identity.uri.user) || '',
            CallerName: (identity && identity.displayName) || undefined,
            // phone.js checks `callPartnerId !== 'false'` (Twilio sends the
            // literal string 'false' for "no partner match", not JS
            // undefined) to decide whether to run its own searchPartner()
            // caller-ID lookup — must match that exact string convention or
            // every VoiceTel call is wrongly treated as already-matched with
            // a NaN partnerId and never looked up.
            Partner: 'false',
            autoAnswer: undefined,
            CallSid: this._session.request?.call_id,
        }
    }

    accept() {
        // A dead session can't be accepted; the caller is gone — cancel.
        this._safeSessionOp(() => this._session.accept(), 'cancel')
    }

    reject() {
        this._safeSessionOp(
            () => this._session.reject({statusCode: 486, reasonPhrase: "Busy Here"}),
            'reject')
    }

    disconnect() {
        this._safeSessionOp(() => {
            if (this._session.hasAnswer) {
                this._session.bye()
            } else {
                this._session.cancel()
            }
        }, this._session.hasAnswer ? 'disconnect' : 'cancel')
    }

    mute(shouldMute) {
        const pc = this._session.sessionDescriptionHandler?.peerConnection
        if (!pc) return
        pc.getSenders().forEach((sender) => {
            if (sender.track && sender.track.kind === 'audio') {
                sender.track.enabled = !shouldMute
            }
        })
    }

    sendDigits(digits) {
        for (const digit of String(digits)) {
            this._session.dtmf(digit, {duration: 100, interToneGap: 70})
        }
    }

    _attachRemoteAudio() {
        const pc = this._session.sessionDescriptionHandler?.peerConnection
        if (!pc) return
        const remoteStream = new MediaStream()
        pc.getReceivers().forEach((receiver) => {
            if (receiver.track) remoteStream.addTrack(receiver.track)
        })
        // SIP.js has no built-in remote-audio element the way Twilio.Device
        // does — the reference implementation attaches the stream to a page
        // <audio> element itself, so this transport owns one instead of
        // requiring phone.js to know about it.
        if (!this._remoteAudioEl) {
            this._remoteAudioEl = document.createElement('audio')
            this._remoteAudioEl.autoplay = true
            this._remoteAudioEl.style.display = 'none'
            document.body.appendChild(this._remoteAudioEl)
        }
        this._remoteAudioEl.srcObject = remoteStream
    }

    _teardownRemoteAudio() {
        if (this._remoteAudioEl) {
            this._remoteAudioEl.srcObject = null
            this._remoteAudioEl.remove()
            this._remoteAudioEl = null
        }
    }
}

/**
 * VoiceTelTransport — ITelephonyTransport implementation over SIP.js
 * 0.15.11's legacy SIP.UA API, mirroring VoiceTel's own reference
 * implementation (github.com/voicetel/phone, phone.html) precisely for the
 * config shape and call-control primitives. Never emits 'quality' — VoiceTel/
 * SIP.js has no call-quality signal equivalent to Twilio's sample/warning
 * events (interface handles this gracefully, see TelephonyCall doc).
 *
 * connData shape (from get_client_token()'s 'voicetel' response, see
 * VocetelUser._get_voicetel_client_token() in connect_voicetel/models/user.py):
 *   {sip_uri, username, password, wss_server, display_name}
 * sip_uri is this connect.user's OWN SIP AOR (bare "username@domain", same
 * one render_sip() dials to ring a desk phone — see connect_uri/uri on
 * connect.user) — NOT the browser-session username. This transport
 * registers AS that AOR (uri) while authenticating with the separate
 * browser-session credential (authorizationUser/password), so inbound calls
 * routed via the existing unmodified render_sip() reach the browser exactly
 * as they would a desk phone. This assumes the SIP registrar accepts a
 * registration whose AOR differs from its authenticating username (a common
 * multi-device-per-extension PBX pattern) — NOT verified against a live
 * VoiceTel account; this is the single biggest unverified assumption in
 * this transport.
 */
export class VoiceTelTransport extends TelephonyTransport {
    static async loadDependencies() {
        await loadJS('/connect_voicetel/static/src/lib/sip.min.js')
    }

    static hasCredential(tokenData) {
        return !!tokenData.password
    }

    static buildConnData(tokenData) {
        return {
            sip_uri: tokenData.sip_uri,
            username: tokenData.username,
            password: tokenData.password,
            wss_server: tokenData.wss_server,
            display_name: tokenData.display_name,
        }
    }

    constructor() {
        super()
        this._ua = null
        this._started = false
        this._sipState = 'destroyed'
        this._hasActiveCall = false
        this._pendingConnData = null
    }

    _domain(connData) {
        const sipUri = connData.sip_uri || ''
        const at = sipUri.indexOf('@')
        return at >= 0 ? sipUri.slice(at + 1) : sipUri
    }

    _buildUA(connData) {
        this._connData = connData
        const domain = this._domain(connData)
        this._ua = new SIP.UA({
            uri: 'sip:' + connData.sip_uri,
            transportOptions: {
                wsServers: [connData.wss_server],
                traceSip: true,
                wsServerMaxReconnectionAttempts: 5,
                wsServerReconnectionTimeout: 4,
            },
            authorizationUser: connData.username,
            password: connData.password,
            displayName: connData.display_name,
            register: true,
            registerOptions: {
                registrar: 'sip:' + domain,
                expires: 300,
            },
            sessionDescriptionHandlerFactoryOptions: {
                constraints: {audio: true, video: false},
                peerConnectionOptions: {
                    rtcConfiguration: {
                        iceServers: [
                            {urls: "stun:stun.l.google.com:19302"},
                            {urls: "stun:stun1.l.google.com:19302"},
                        ],
                    },
                },
            },
            hackWssInTransport: false,
            hackIpInContact: true,
            dtmfType: "rtp",
            userAgentString: 'ConnectVoiceTel/1.0',
        })
        this._sipState = 'unregistered'
        this._started = false

        this._ua.on('registered', () => {
            this._sipState = 'registered'
            this._emit('registered')
        })
        this._ua.on('unregistered', () => {
            this._sipState = 'unregistered'
            this._emit('unregistered')
        })
        this._ua.on('registrationFailed', (error) => {
            this._sipState = 'unregistered'
            this._emit('error', error)
        })
        this._ua.on('invite', (session) => this._emit('incoming', this._trackCall(new VoiceTelCall(session))))
    }

    /** Synchronous by design — see ITelephonyTransport#init() note. */
    init(connData) {
        this._buildUA(connData)
    }

    async register() {
        if (!this._ua) {
            throw new Error('VoiceTelTransport: not initialized')
        }
        // SIP.js legacy UA.start()/register() are event-driven, not
        // promise-based — this resolves once the call is made, not once
        // registration succeeds/fails; outcome surfaces via the
        // 'registered'/'error' events, per ITelephonyTransport's allowance
        // for inherently-async transports.
        if (!this._started) {
            this._started = true
            this._ua.start()
        } else {
            this._ua.register()
        }
    }

    async unregister() {
        if (!this._ua) return
        this._ua.unregister()
    }

    destroy() {
        if (this._ua) {
            try {
                this._ua.stop()
            } catch (e) {
                console.warn('Connect: VoiceTelTransport: error stopping UA:', e)
            }
        }
        this._ua = null
        this._started = false
        this._sipState = 'destroyed'
        this._hasActiveCall = false
        this._pendingConnData = null
    }

    /**
     * Synchronous by design — see ITelephonyTransport#updateAuth() note.
     *
     * SIP.js 0.15's legacy UA has no in-place credential-update API (unlike
     * Twilio's Device#updateToken()), so a rotated password can only be
     * applied by rebuilding the UA. Rebuilding tears down the transport
     * (UA#stop()), which would drop any call bound to it — so a rotation
     * that arrives while a call is active is deferred until that call ends
     * instead of applied immediately. Not verified against a live account.
     */
    updateAuth(connData) {
        if (!this._ua) {
            throw new Error('VoiceTelTransport: not initialized')
        }
        if (this._hasActiveCall) {
            this._pendingConnData = connData
            return
        }
        this._rebuildUA(connData)
    }

    _rebuildUA(connData) {
        const wasRegistered = this._sipState === 'registered'
        try {
            this._ua.stop()
        } catch (e) {
            console.warn('Connect: VoiceTelTransport: error stopping UA during credential rotation:', e)
        }
        this._buildUA(connData)
        if (wasRegistered) {
            this.register().catch((e) => console.warn('Connect: VoiceTelTransport: re-register after credential rotation failed:', e))
        }
    }

    _trackCall(call) {
        this._hasActiveCall = true
        const clearActiveCall = () => {
            this._hasActiveCall = false
            if (this._pendingConnData) {
                const pending = this._pendingConnData
                this._pendingConnData = null
                this._rebuildUA(pending)
            }
        }
        call.on('disconnect', clearActiveCall)
        call.on('cancel', clearActiveCall)
        call.on('reject', clearActiveCall)
        return call
    }

    get state() {
        return this._sipState
    }

    async dial(params) {
        const domain = this._domain(this._connData)
        const session = this._ua.invite('sip:' + params.To + '@' + domain)
        return this._trackCall(new VoiceTelCall(session))
    }

    setIncomingAudio(enabled) {
        // SIP.js has no built-in incoming-ring sound the way
        // Twilio.Device.audio does. Stored for potential future use;
        // currently a no-op — VoiceTel calls have no ring audio of their
        // own yet.
        this._incomingAudioEnabled = enabled
    }
}

// Self-registers into the shared transport registry at load time — the same
// mechanism TwilioTransport uses. phone.js never imports this file directly;
// simply being installed (and therefore bundled) is what makes "voicetel" a
// valid provider.
registry.category("connect.telephony_transports").add("voicetel", VoiceTelTransport)
