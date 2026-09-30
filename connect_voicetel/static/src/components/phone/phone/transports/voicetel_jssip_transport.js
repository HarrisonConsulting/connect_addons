/** @odoo-module **/
"use strict"

import {loadJS} from "@web/core/assets"
import {registry} from "@web/core/registry"
import {TelephonyTransport, TelephonyCall} from "@connect/components/phone/phone/transports/telephony_transport"

/**
 * Wraps a JsSIP 3.10 RTCSession behind TelephonyCall.
 *
 * The UA options follow the VoiceTel softphone that registers a JsSIP user
 * agent against the OpenSIPS WSS edge: one socket, the user's SIP AOR, the
 * browser credential as authorization_user, and the domain as the auth realm.
 * Incoming sessions are handed to phone.js. This transport does not answer
 * them itself.
 */
export class VoiceTelJsSIPCall extends TelephonyCall {
    constructor(session) {
        super()
        this._session = session
        this._terminated = false
        this._answered = false
        this._remoteAudioEl = null
        this._wireSession()
    }

    _wireSession() {
        const session = this._session
        session.on('accepted', () => {
            this._answered = true
            this._attachRemoteAudio()
            this._emit('accept')
        })
        session.on('confirmed', () => this._attachRemoteAudio())
        session.on('peerconnection', () => {
            const pc = session.connection
            if (pc) {
                pc.addEventListener('track', () => this._attachRemoteAudio())
            }
        })
        session.on('ended', () => this._terminate(this._answered ? 'disconnect' : 'cancel'))
        session.on('failed', (data) => {
            const cause = data && data.cause
            if (!this._answered && (cause === 'Rejected' || cause === 'Canceled')) {
                this._terminate(cause === 'Rejected' ? 'reject' : 'cancel')
                return
            }
            this._terminate(this._answered ? 'disconnect' : 'cancel')
        })
    }

    _terminate(eventName) {
        if (this._terminated) return
        this._terminated = true
        this._teardownRemoteAudio()
        this._emit(eventName)
    }

    _safeSessionOp(op, terminalEvent) {
        if (this._terminated) return
        try {
            op()
        } catch (e) {
            console.warn('Connect: VoiceTelJsSIPTransport: session op on ended session, normalizing:', e)
            this._terminate(terminalEvent)
        }
    }

    get params() {
        const identity = this._session.remote_identity
        const uri = identity && identity.uri
        const request = this._session.request || this._session._request
        return {
            From: (uri && uri.user) || '',
            CallerName: (identity && identity.display_name) || undefined,
            Partner: 'false',
            autoAnswer: undefined,
            CallSid: request && request.call_id,
        }
    }

    accept() {
        this._safeSessionOp(() => this._session.answer({
            mediaConstraints: {audio: true, video: false},
        }), 'cancel')
    }

    reject() {
        this._safeSessionOp(() => {
            this._terminate('reject')
            this._session.terminate({status_code: 486, reason_phrase: 'Busy Here'})
        }, 'reject')
    }

    disconnect() {
        const eventName = this._answered ? 'disconnect' : 'cancel'
        this._safeSessionOp(() => {
            this._terminate(eventName)
            this._session.terminate()
        }, eventName)
    }

    mute(shouldMute) {
        if (this._terminated || !this._session) return
        if (shouldMute) {
            this._session.mute({audio: true})
        } else {
            this._session.unmute({audio: true})
        }
    }

    sendDigits(digits) {
        if (this._terminated) return
        this._session.sendDTMF(String(digits), {duration: 100, interToneGap: 70})
    }

    _attachRemoteAudio() {
        const pc = this._session.connection
        if (!pc) return
        let remoteStream = null
        if (typeof pc.getRemoteStreams === 'function' && pc.getRemoteStreams().length) {
            remoteStream = pc.getRemoteStreams()[0]
        } else if (typeof pc.getReceivers === 'function') {
            remoteStream = new MediaStream()
            pc.getReceivers().forEach((receiver) => {
                if (receiver.track) remoteStream.addTrack(receiver.track)
            })
        }
        if (!remoteStream) return
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

const ICE_SERVERS = [
    {urls: "stun:stun.l.google.com:19302"},
    {urls: "stun:stun1.l.google.com:19302"},
]

/**
 * VoiceTelJsSIPTransport — ITelephonyTransport over JsSIP 3.10.
 *
 * connData is the same object VoiceTelTransport receives from
 * get_client_token(): {sip_uri, username, password, wss_server, display_name}.
 * sip_uri is the user's own AOR. username/password are the per-tab browser
 * credential. The account setting voicetel_softphone decides which of the
 * two classes phone.js constructs.
 */
export class VoiceTelJsSIPTransport extends TelephonyTransport {
    static async loadDependencies() {
        await loadJS('/connect_voicetel/static/src/lib/jssip.min.js')
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
        if (typeof JsSIP === "undefined") {
            throw new Error('VoiceTelJsSIPTransport: JsSIP is not loaded')
        }
        this._connData = connData
        const socket = new JsSIP.WebSocketInterface(connData.wss_server)
        this._ua = new JsSIP.UA({
            sockets: [socket],
            uri: 'sip:' + connData.sip_uri,
            authorization_user: connData.username,
            password: connData.password,
            realm: this._domain(connData),
            display_name: connData.display_name,
            register: true,
            register_expires: 300,
            user_agent: 'ConnectVoiceTel/1.0 JsSIP',
            pcConfig: {iceServers: ICE_SERVERS},
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
        this._ua.on('newRTCSession', ({session, originator}) => {
            if (originator !== 'remote') return
            this._emit('incoming', this._trackCall(new VoiceTelJsSIPCall(session)))
        })
    }

    init(connData) {
        this._buildUA(connData)
    }

    async register() {
        if (!this._ua) {
            throw new Error('VoiceTelJsSIPTransport: not initialized')
        }
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
                console.warn('Connect: VoiceTelJsSIPTransport: error stopping UA:', e)
            }
        }
        this._ua = null
        this._started = false
        this._sipState = 'destroyed'
        this._hasActiveCall = false
        this._pendingConnData = null
    }

    updateAuth(connData) {
        if (!this._ua) {
            throw new Error('VoiceTelJsSIPTransport: not initialized')
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
            console.warn('Connect: VoiceTelJsSIPTransport: error stopping UA during credential rotation:', e)
        }
        this._buildUA(connData)
        if (wasRegistered) {
            this.register().catch((e) => console.warn(
                'Connect: VoiceTelJsSIPTransport: re-register after credential rotation failed:', e))
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
        const session = this._ua.call('sip:' + params.To + '@' + domain, {
            mediaConstraints: {audio: true, video: false},
            pcConfig: {iceServers: ICE_SERVERS},
        })
        return this._trackCall(new VoiceTelJsSIPCall(session))
    }

    setIncomingAudio(enabled) {
        this._incomingAudioEnabled = enabled
    }
}

registry.category("connect.telephony_transports").add("voicetel_jssip", VoiceTelJsSIPTransport)
