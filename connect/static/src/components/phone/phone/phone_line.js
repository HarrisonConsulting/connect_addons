/** @odoo-module **/
"use strict"

import {registry} from "@web/core/registry"
import {ServerOriginateTransport} from "@connect/components/phone/phone/transports/server_originate_transport"

export const LINE_UNAVAILABLE = 'This phone line is not available: it has no telephony provider. Ask your administrator to check your Connect user.'

/**
 * One line of the phone: one transport, its credential, its reconnect and token
 * refresh cycles, and the call in the foreground on it.
 *
 * What the person sees stays on Phone, which is why a line writes through to its
 * phone for panel state, presence and connection status. Every transport handler
 * is a closure over the line that created the transport, so an event can only
 * ever act on that line.
 *
 * Two things are decided across all lines, so they read through the phone: whether
 * any line already has a foreground call (Phone#session) and the one waiting slot
 * (Phone#waitingSession). A call that finds both taken is refused, whichever line
 * it rang on.
 */
export class PhoneLine {
    /**
     * @param {Phone} phone
     * @param {Object} params
     * @param {string} params.key - identifies the line among the phone's lines
     * @param {string|false} params.provider - key in connect.telephony_transports;
     *     empty means the server could not resolve one, and the line is unavailable
     * @param {string} [params.media] - 'browser' or 'bridge'
     * @param {number|false} [params.toolId] - the connect.tool this line is
     * @param {Object} [params.capabilities] - {action: bool} from the server;
     *     an action that is absent is one the line cannot do
     * @param {string} [params.label] - the tool or plan name the person knows it by
     * @param {string[]} [params.callerIds] - numbers this line may call from
     * @param {Object} params.tokenData - the raw get_client_token() response
     */
    constructor(phone, {key, provider, media, toolId, capabilities = {}, label = '', callerIds = [], tokenData}) {
        this.phone = phone
        this.key = key
        this.provider = provider
        this.media = media
        this.toolId = toolId
        this.capabilities = capabilities
        this.label = label
        this.callerIds = callerIds
        this.tokenData = tokenData
        this.transport = null
        this.session = null
        this.registered = false
        this._reconnecting = false
        this._needsTokenRefresh = false
        this._calls = new WeakSet()
    }

    /**
     * Extension point: providers register their transport class into the
     * "connect.telephony_transports" registry (see twilio_transport.js's
     * own registry.category(...).add(...) call) —
     * nothing here, or anywhere else in this file, needs to change to add a
     * further provider. A provider module that isn't installed simply never
     * registers, and is never imported by this file either. A bridge line has no
     * transport of its own: its calls are the server's.
     */
    get _transportClass() {
        if (this.media === 'bridge') {
            return ServerOriginateTransport
        }
        const TransportClass = registry.category("connect.telephony_transports").get(this.provider, null)
        if (!TransportClass) {
            throw new Error(`Connect: no telephony transport implemented for provider "${this.provider}"`)
        }
        return TransportClass
    }

    /** Whether this line's calls have their audio in the browser. */
    get needsBrowserMedia() {
        return !!this.provider && this._transportClass.needsBrowserMedia
    }

    async loadDependencies() {
        if (this.provider) {
            await this._transportClass.loadDependencies()
        }
    }

    /** The number this line calls from unless the person picks another. */
    get defaultCallerId() {
        return this.callerIds[0] || this.tokenData.outgoing_callerid || ''
    }

    /** Whether a dial on this line has a transport to go through. */
    get hasLiveTransport() {
        return !!this.transport && this.transport.state !== 'destroyed'
    }

    /** A new credential. With one line this is the call it has always been. */
    _fetchToken() {
        const phone = this.phone
        const kwargs = {nonce: phone.browserSessionNonce}
        if (phone.lines.length >= 2) {
            kwargs.context = {connect_tool_id: this.toolId}
        }
        return phone.orm.call('connect.user', 'get_client_token', [], kwargs)
    }

    /** Whether this call rang on, or was placed from, this line. */
    owns(call) {
        return !!call && this._calls.has(call)
    }

    /**
     * Places a call on this line's transport. The caller decides whether it
     * becomes the foreground call, through Phone#session.
     */
    async dial(params) {
        const call = await this.transport.dial(params)
        this._calls.add(call)
        return call
    }

    setIncomingVolume() {
        try {
            if (this.transport) {
                this.transport.setIncomingAudio(!this.phone.state.isSoundMute)
            }
        } catch (e) {
            console.warn('Connect: Could not set incoming volume:', e)
        }
    }

    /**
     * The browser may have closed IDB connections and the transport's websocket
     * while the tab was hidden, so re-initialize if needed.
     */
    resume() {
        if (this._needsTokenRefresh) {
            this._needsTokenRefresh = false
            this.updateToken()
        }
        if (!this.transport || this.transport.state === 'destroyed') {
            // Transport was destroyed while tab was hidden — full re-init
            console.debug('Connect: transport was destroyed while tab was hidden, re-initializing')
            this.initTransport()
        } else if (this.transport.state === 'unregistered') {
            // Transport lost registration while tab was hidden — refresh token and re-register
            console.debug('Connect: transport unregistered while tab was hidden, re-registering')
            this.updateToken()
        }
    }

    async reconnect() {
        const phone = this.phone
        if (!this.provider) {
            phone._setConnectionStatus('error', LINE_UNAVAILABLE)
            return
        }
        // Re-entry guard: cascading device errors (dead transport → AccessTokenInvalid → AccessTokenExpired)
        // can all schedule reconnects through per-device closures. Gate at the line level so
        // the whole cascade collapses into a single reconnect cycle.
        if (this._reconnecting) {
            return
        }
        this._reconnecting = true
        phone._setConnectionStatus('connecting')
        phone.bus.trigger('busTraySetException', {exception: null})

        // Destroy existing transport if present
        if (this.transport) {
            try {
                this.transport.destroy()
            } catch (e) {
                console.warn('Connect: Error destroying existing transport:', e)
            }
            this.transport = null
        }

        // Fetch a new token and re-initialize
        try {
            const token_data = await this._fetchToken()
            if (this._transportClass.hasCredential(token_data)) {
                this.tokenData = token_data
                this.initTransport()
            } else {
                phone._failReconnect('Could not obtain a phone token.')
            }
        } catch (e) {
            console.error('Connect: Reconnect failed:', e)
            phone._failReconnect(e.message || 'Reconnection failed.')
        } finally {
            this._reconnecting = false
        }
    }

    async updateToken() {
        const phone = this.phone
        // Guard: skip if transport is unavailable or a reconnect is already in progress
        if (!this.transport || this.transport.state === 'destroyed') {
            return
        }
        if (phone.state.connectionStatus === 'connecting') {
            return
        }
        try {
            const token_data = await this._fetchToken()
            // Re-check transport state after async call — reconnect may have destroyed it
            if (!this._transportClass.hasCredential(token_data) || !this.transport || this.transport.state === 'destroyed') {
                return
            }
            try {
                this.transport.updateAuth(this._transportClass.buildConnData(token_data))
            } catch (e) {
                // updateAuth() throws synchronously if transport is dead
                console.warn('Connect: transport.updateAuth() threw:', e?.message || e)
                return
            }
            this.tokenData = token_data
            // Re-register only if transport is unregistered (e.g. after disconnect)
            // Calling register() on an already-registered transport throws InvalidStateError
            if (this.transport.state === 'unregistered') {
                try {
                    await this.transport.register()
                } catch (regErr) {
                    console.warn('Connect: Re-registration after token refresh failed:', regErr?.message || regErr)
                }
            }
        } catch (e) {
            console.warn('Connect: Token refresh failed, will retry when tab is active:', e?.message || e)
            this._needsTokenRefresh = true
        }
    }

    initTransport() {
        const phone = this.phone
        if (!phone.state.isActive) {
            return
        }

        if (!this.provider) {
            phone._setConnectionStatus('error', LINE_UNAVAILABLE)
            return
        }

        // Check WebRTC support before initializing the transport
        if (this.needsBrowserMedia && (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)) {
            console.error('Connect: WebRTC not supported in this browser')
            phone.state.isActive = false
            phone._setConnectionStatus('error', 'This browser does not support WebRTC.')
            phone.bus.trigger('busTraySetException', {exception: 'NotSupported'})
            return
        }

        this.transport = new this._transportClass(this)

        try {
            this.transport.init(this._transportClass.buildConnData(this.tokenData))
        } catch (error) {
            console.error('Connect: Failed to create transport:', error)
            phone.state.isActive = false
            phone._setConnectionStatus('error', error.message || 'Phone service failed to start.')
            phone.bus.trigger('busTraySetException', {exception: error.name || 'InitFailed'})
            return
        }

        // Try to set incoming volume, but don't fail if AudioContext is blocked
        this.setIncomingVolume()

        this.transport.on('authWillExpire', () => {
            console.debug('Connect: Token expiring, refreshing')
            this.updateToken()
        })

        this.transport.on('registered', () => {
            phone._setConnectionStatus('ready')
            this.registered = true
            phone._updatePresence('available')
        })

        this.transport.on('unregistered', () => {
            this.registered = false
            // If not in a call, attempt silent auto-recovery instead of staying offline
            if (!phone.state.inCall && phone.state.isActive) {
                phone._setConnectionStatus('connecting')
                console.debug('Connect: Transport unregistered, attempting silent re-registration')
                setTimeout(() => {
                    // Skip soft recovery if a transport error already triggered full reconnect
                    if (errorRecoveryPending) return
                    if (this.transport && this.transport.state === 'unregistered') {
                        this.updateToken()
                    }
                }, 3000)
            } else {
                phone._setConnectionStatus('offline', 'Lost the connection to the phone service.')
                phone._updatePresence('offline')
            }
        })

        // Debounce error handling to prevent cascading loops: a dead transport fires
        // the same error on every pending operation (register, setToken, etc.)
        let errorRecoveryPending = false

        this.transport.on('error', (error) => {
            console.error('Connect: Transport error:', error.message || error)

            const kind = this._transportClass.classifyError(error)
            const isTransportError = kind === 'transport'
            const isTokenError = kind === 'auth'

            if (isTransportError || isTokenError) {
                // Dead transport or rejected token — full teardown+rebuild with fresh token.
                // Per-device debounce + line-level _reconnecting guard prevent both intra-device
                // cascades (dead transport → AccessTokenInvalid on the same corpse) and cross-device cascades
                // (each new transport firing its own error before the prior reconnect completes).
                if (errorRecoveryPending || this._reconnecting) return
                errorRecoveryPending = true
                phone._setConnectionStatus('connecting')
                phone._updatePresence('offline')
                setTimeout(() => {
                    errorRecoveryPending = false
                    this.reconnect()
                }, isTransportError ? 5000 : 2000)
            } else if (kind === 'unsupported') {
                console.error('Connect: Browser does not support required features:', error.message)
                phone.state.isActive = false
                phone._setConnectionStatus('error', 'This browser is missing features the phone needs.')
                phone._updatePresence('offline')
                phone.bus.trigger('busTraySetException', {exception: 'NotSupported'})
            } else {
                phone._setConnectionStatus('error', error.message || 'Phone service error.')
                phone._updatePresence('offline')
            }
        })

        this.transport.on("incoming", (call) => this._onIncoming(call))

        // Register with exponential backoff retry (3 attempts: 2s, 4s, 8s)
        const registerWithRetry = async (attempt = 0) => {
            const maxAttempts = 3
            try {
                await this.transport.register()
            } catch (e) {
                if (attempt < maxAttempts - 1) {
                    const delay = Math.pow(2, attempt + 1) * 1000
                    console.warn(`Connect: Registration attempt ${attempt + 1} failed, retrying in ${delay}ms`)
                    setTimeout(() => registerWithRetry(attempt + 1), delay)
                } else {
                    console.error('Connect: Registration failed after all retries')
                    phone._setConnectionStatus('error', 'Phone registration failed.')
                }
            }
        }
        registerWithRetry()
    }

    async _onIncoming(call) {
        const phone = this.phone
        this._calls.add(call)
        phone.state.isContactList = false
        let phoneNumber = call.params.From
        if (phoneNumber.startsWith("whatsapp:")) {
            phoneNumber = phoneNumber.replace(/^whatsapp:/, "")
            phone.state.isWhatsapp = true
        }
        const callCallerName = call.params.CallerName
        const callPartnerId = call.params.Partner
        const autoAnswer = call.params.autoAnswer
        const origin = call.params.Origin

        // A click-to-call rings every device signed in as this user. Only
        // the tab that placed it answers; the others ignore, never reject,
        // because a reject would end the ring on the answering device too.
        if (autoAnswer === 'yes' && origin && origin !== phone.browserSessionNonce) {
            call.ignore()
            return
        }

        if (phone.session === null) {
            phone.session = call
            phone.sipSessions.push(phone.id)
            const params = {id: phone.id, action: 'push'}
            phone.bc.postMessage({event: 'tbcSipSession', params})
        } else {
            // During attended transfer, incoming call may be the consult leg — don't reject
            if (phone.state.isAttendedTransfer) {
                phone.waitingSession = call
                phone.state.hasWaitingCall = true
                phone.state.waitingCallerId = {
                    phoneNumber,
                    partnerName: callCallerName || '',
                    partnerId: callPartnerId !== 'false' ? callPartnerId : false,
                }
                phone.notify('Consult call from ' + (callCallerName || phone.displayNumber(phoneNumber)), {type: 'info', sticky: true})
                phone.bc.postMessage({event: "tbcWaitingCall", params: {
                    hasWaitingCall: true,
                    waitingCallerId: {...phone.state.waitingCallerId},
                }})
                phone._syncIncomingRing()
                return
            }
            // Call waiting: store the incoming call instead of rejecting
            if (phone.waitingSession) {
                // Already have a waiting call, on this line or another — reject the third one
                call.reject()
                return
            }
            phone.waitingSession = call
            phone.state.hasWaitingCall = true

            // Extract caller info for display
            phone.state.waitingCallerId = {
                phoneNumber,
                partnerName: callCallerName || '',
                partnerId: callPartnerId !== 'false' ? callPartnerId : false,
            }

            // Notify user and sync waiting call state to other tabs
            phone.notify('Incoming call from ' + (callCallerName || phone.displayNumber(phoneNumber)), {type: 'info', sticky: true})
            phone.bc.postMessage({event: "tbcWaitingCall", params: {
                hasWaitingCall: true,
                waitingCallerId: {...phone.state.waitingCallerId},
            }})
            phone._syncIncomingRing()

            // Set up handlers for the waiting call. Once it is promoted
            // to the foreground panel these listeners no longer own it.
            call.on('cancel', () => {
                if (phone.waitingSession !== call) return
                phone.waitingSession = null
                phone.state.hasWaitingCall = false
                phone.state.waitingCallerId = {}
                phone._syncIncomingRing()
                phone.notify('Waiting call ended', {type: 'info'})
                phone.bc.postMessage({event: "tbcWaitingCall", params: {
                    hasWaitingCall: false,
                    waitingCallerId: {},
                }})
            })

            call.on('disconnect', () => {
                if (phone.waitingSession !== call) return
                phone.waitingSession = null
                phone.state.hasWaitingCall = false
                phone.state.waitingCallerId = {}
                phone._syncIncomingRing()
                phone.bc.postMessage({event: "tbcWaitingCall", params: {
                    hasWaitingCall: false,
                    waitingCallerId: {},
                }})
            })

            return
        }

        phone.state.callPhoneNumber = phoneNumber

        if (callPartnerId !== 'false') {
            phone.state.isPartner = true
            phone.state.callerId = {
                partnerId: parseInt(callPartnerId),
                partnerName: callCallerName,
                partnerIconUrl: phone.computePartnerIconUrl(callPartnerId),
                partnerUrl: phone.computePartnerUrl(callPartnerId),
                phoneNumber,
            }
        } else {
            phone.state.callerId = {phoneNumber}
            await phone.searchPartner(phoneNumber)
        }

        phone.state.isDisplayLastState = phone.state.isDisplay
        if (!phone.state.isDisplay) {
            phone.toggleDisplay()
        }
        const params = phone.getJsonCallData()
        phone.bc.postMessage({event: "tbcStartCall", params})

        phone.state.inIncoming = true
        phone.state.isDialingPanel = true
        phone.startCall()
        phone._attachIncomingCall(call, phoneNumber)

        if (autoAnswer === 'yes') {
            call.accept()
            phone.state.phone_status = phone.status.accepted
            phone.state.inIncoming = false
            phone.startCall()
        }
        phone._syncIncomingRing()
    }
}
