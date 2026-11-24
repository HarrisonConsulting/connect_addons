/** @odoo-module **/
"use strict"

import {registry} from "@web/core/registry"
import {_t} from "@web/core/l10n/translation"
import {ServerOriginateTransport} from "@connect/components/phone/phone/transports/server_originate_transport"

export const LINE_UNAVAILABLE = 'This phone line is not available: it has no telephony provider. Ask your administrator to check your Connect user.'

// How long a line waits for a registration to conclude, one way or the other,
// before it says the phone service did not answer.
export const REGISTRATION_TIMEOUT_MS = 20000
// How many automatic attempts a line makes to get back, with no registration in
// between, before it waits for the person's Retry.
export const MAX_AUTO_ATTEMPTS = 3

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
 *
 * What goes wrong on a line stays on that line: its status is its own, and a line
 * that cannot start stops only itself. The phone stops when no line can work.
 *
 * A line never waits for ever. A registration that does not conclude in
 * REGISTRATION_TIMEOUT_MS, and automatic attempts that do not register within
 * MAX_AUTO_ATTEMPTS, leave the line in error, in words, until Retry.
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
     * @param {number|false} [params.mintToolId] - the tool its credential was minted
     *     under, which every later mint repeats; none means no context at all
     * @param {string} [params.unavailable] - why the server could not mint for this
     *     line, in words. It stays a line, so that it can refuse with the reason.
     * @param {Object} [params.capabilities] - {action: bool} from the server;
     *     an action that is absent is one the line cannot do
     * @param {string} [params.label] - the tool or plan name the person knows it by
     * @param {string[]} [params.callerIds] - numbers this line may call from
     * @param {Object} params.tokenData - the raw get_client_token() response
     */
    constructor(phone, {key, provider, media, toolId, mintToolId = false, unavailable = '', capabilities = {}, label = '', callerIds = [], tokenData}) {
        this.phone = phone
        this.key = key
        this.provider = provider
        this.media = media
        this.toolId = toolId
        this.mintToolId = mintToolId
        this.unavailable = unavailable
        this.startFailed = false
        this.capabilities = capabilities
        this.label = label
        this.callerIds = callerIds
        this.tokenData = tokenData
        this.transport = null
        this.session = null
        this.registered = false
        this._reconnecting = false
        this._autoAttempts = 0
        this._gaveUp = false
        this._destroyed = false
        this._registrationTimer = null
        this._reconnectTimer = null
        this._softRecoveryTimer = null
        this._registerRetryTimer = null
        this._needsTokenRefresh = false
        this._dependenciesLoaded = false
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
        const TransportClass = this._findTransportClass()
        if (!TransportClass) {
            throw new Error(`Connect: no telephony transport implemented for provider "${this.provider}"`)
        }
        return TransportClass
    }

    _findTransportClass() {
        if (this.media === 'bridge') {
            return ServerOriginateTransport
        }
        return registry.category("connect.telephony_transports").get(this.provider, null)
    }

    /** Whether this line's calls have their audio in the browser. */
    get needsBrowserMedia() {
        return !this.unavailable && !!this._findTransportClass()?.needsBrowserMedia
    }

    /** Whether this line's transport lets the person choose microphone and speaker. */
    get choosesAudioDevices() {
        return this.needsBrowserMedia && !!this._findTransportClass()?.choosesAudioDevices
    }

    async loadDependencies() {
        if (!this.provider || this.unavailable) {
            return
        }
        try {
            await this._transportClass.loadDependencies()
            this._dependenciesLoaded = true
        } catch (error) {
            console.error('Connect: Failed to load a phone line:', error)
            this._failStart(error.message || 'Phone service failed to start.', error.name || 'InitFailed')
        }
    }

    /** This line cannot start. The others are not told, and the phone stops only if none can work. */
    _failStart(words, exception) {
        const phone = this.phone
        this.startFailed = true
        phone._refreshIsActive()
        phone._setLineStatus(this, 'error', words)
        if (!phone.state.isActive) {
            phone.bus.trigger('busTraySetException', {exception})
        }
    }

    /** Starts the clock on a registration that has to conclude. */
    _watchRegistration() {
        clearTimeout(this._registrationTimer)
        this._registrationTimer = null
        if (this._gaveUp || this._destroyed) {
            return
        }
        this._registrationTimer = setTimeout(() => {
            this._registrationTimer = null
            // A lost network is not a service that did not answer.
            if (this._isOffline || navigator.onLine === false) {
                return
            }
            this._giveUp(_t("%s did not finish signing in: the phone service did not answer.", this.label))
        }, REGISTRATION_TIMEOUT_MS)
    }

    /** A registration being watched that fails for a known reason says that reason, not that the service was silent. */
    _giveUpWhileWatching(words) {
        if (this._registrationTimer) {
            this._giveUp(words)
        }
    }

    _stopWatchingRegistration() {
        clearTimeout(this._registrationTimer)
        this._registrationTimer = null
    }

    /** Spends one of the automatic attempts. False means they are used up. */
    _spendAutoAttempt() {
        if (this._autoAttempts >= MAX_AUTO_ATTEMPTS) {
            return false
        }
        this._autoAttempts++
        return true
    }

    /** Lets go of every timer, so nothing of this line acts on its own afterwards. */
    _clearTimers() {
        for (const timer of ['_registrationTimer', '_reconnectTimer', '_softRecoveryTimer', '_registerRetryTimer']) {
            clearTimeout(this[timer])
            this[timer] = null
        }
    }

    /** Whether the line was told the device has no network. */
    get _isOffline() {
        return this.phone.state.lineStatuses[this.key]?.status === 'offline'
    }

    /** The device has no network: the line says so and waits, with nothing of its own running. */
    goOffline(words) {
        this._clearTimers()
        this.phone._setLineStatus(this, 'offline', words)
    }

    /** The line is down and stays down, in words, until the person retries. */
    _giveUp(words) {
        if (this._destroyed) {
            return
        }
        this._gaveUp = true
        this._clearTimers()
        this.phone._setLineStatus(this, 'error', words)
        this._reportOffline()
    }

    /** The phone is going away. */
    destroy() {
        this._destroyed = true
        this._clearTimers()
    }

    /** The person's Retry: another round of automatic attempts, counted from none. */
    retry() {
        this._autoAttempts = 0
        this._gaveUp = false
        return this.reconnect()
    }

    /** Presence is the person's, so a line going down says offline only if it was the last. */
    _reportOffline() {
        if (!this.phone.lines.some((line) => line !== this && line.registered)) {
            this.phone._updatePresence('offline')
        }
    }

    /** The number this line calls from unless the person picks another. */
    get defaultCallerId() {
        return this.callerIds[0] || this.tokenData.outgoing_callerid || ''
    }

    /** Whether a dial on this line has a transport to go through. */
    get hasLiveTransport() {
        return !this.startFailed && !!this.transport && this.transport.state !== 'destroyed'
    }

    /**
     * A new credential, under the same tool as the first. A line minted with no
     * context, which is every line of a person with one, sends exactly the nonce.
     */
    _fetchToken() {
        const phone = this.phone
        const kwargs = {nonce: phone.browserSessionNonce}
        if (this.mintToolId) {
            kwargs.context = {connect_tool_id: this.mintToolId}
        }
        return phone.orm.call('connect.user', 'get_client_token', [], kwargs)
    }

    /** What a line that could not mint learns once it can. */
    _adoptCredential(tokenData) {
        this.tokenData = tokenData
        if (this.unavailable) {
            this.unavailable = ''
            this.callerIds = tokenData.caller_ids || []
            this.label = tokenData.label || this.label
        }
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
            phone._setLineStatus(this, 'error', LINE_UNAVAILABLE)
            return
        }
        // Nothing of a bridge line's is in this browser to rebuild, and its transport
        // holds its calls: rebuilding would lose a live call, and the refusal to
        // redial over one nobody can confirm.
        if (this.media === 'bridge' && this.hasLiveTransport) {
            phone._setLineStatus(this, 'ready')
            return
        }
        // Re-entry guard: cascading device errors (dead transport → AccessTokenInvalid → AccessTokenExpired)
        // can all schedule reconnects through per-device closures. Gate at the line level so
        // the whole cascade collapses into a single reconnect cycle.
        if (this._reconnecting) {
            return
        }
        this._reconnecting = true
        this._clearTimers()
        phone._setLineStatus(this, 'connecting')
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

        // A line that could not start gets another go, from loading its library up.
        if (this.startFailed) {
            this.startFailed = false
            phone._refreshIsActive()
            await this.loadDependencies()
            if (this.startFailed) {
                this._reconnecting = false
                return
            }
        }

        // Fetch a new token and re-initialize
        try {
            const token_data = await this._fetchToken()
            // A line that could not mint did not know how it connects until now.
            if (this.unavailable && token_data.media) {
                this.media = token_data.media
            }
            // A line that could not mint may not know yet how it connects, so a
            // refusal here can have no transport class to ask.
            if (this._findTransportClass()?.hasCredential(token_data)) {
                this._adoptCredential(token_data)
                // A line that could not mint never loaded its library.
                if (!this._dependenciesLoaded) {
                    await this.loadDependencies()
                }
                this.initTransport()
            } else {
                // One line that was working says what it always has.
                const reason = this.unavailable || phone.isMultiLine || this.mintToolId ? token_data.error : ''
                if (this.unavailable && reason) {
                    this.unavailable = reason
                }
                phone._setLineStatus(this, 'error', reason || 'Could not obtain a phone token.')
            }
        } catch (e) {
            console.error('Connect: Reconnect failed:', e)
            phone._setLineStatus(this, 'error', e.message || 'Reconnection failed.')
        } finally {
            this._reconnecting = false
        }
    }

    /**
     * @param {Object} [options]
     * @param {boolean} [options.recovering] - this is the line's own attempt to
     *     sign in again, which is made while its status says it is connecting
     */
    async updateToken({recovering = false} = {}) {
        const phone = this.phone
        // Guard: skip if transport is unavailable or a reconnect is already in progress
        if (!this.transport || this.transport.state === 'destroyed') {
            return
        }
        if (!recovering && phone.state.lineStatuses[this.key]?.status === 'connecting') {
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
                    this._watchRegistration()
                    await this.transport.register()
                } catch (regErr) {
                    console.warn('Connect: Re-registration after token refresh failed:', regErr?.message || regErr)
                    this._giveUpWhileWatching(_t("%s could not sign in again: %s", this.label, regErr?.message || regErr))
                }
            }
        } catch (e) {
            console.warn('Connect: Token refresh failed, will retry when tab is active:', e?.message || e)
            this._needsTokenRefresh = true
            this._giveUpWhileWatching(_t("%s could not get a new credential: %s", this.label, e?.message || e))
        }
    }

    initTransport() {
        const phone = this.phone
        if (this.startFailed) {
            return
        }

        if (this.unavailable) {
            phone._setLineStatus(this, 'error', this.unavailable)
            return
        }

        if (!this.provider) {
            phone._setLineStatus(this, 'error', LINE_UNAVAILABLE)
            return
        }

        // Check WebRTC support before initializing the transport
        if (this.needsBrowserMedia && (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)) {
            console.error('Connect: WebRTC not supported in this browser')
            this._failStart('This browser does not support WebRTC.', 'NotSupported')
            return
        }

        this._clearTimers()
        try {
            this.transport = new this._transportClass(this)
            this.transport.init(this._transportClass.buildConnData(this.tokenData))
        } catch (error) {
            console.error('Connect: Failed to create transport:', error)
            // A transport that never started is no transport to dial through.
            this.transport = null
            this._failStart(error.message || 'Phone service failed to start.', error.name || 'InitFailed')
            return
        }

        // Try to set incoming volume, but don't fail if AudioContext is blocked
        this.setIncomingVolume()

        // Debounce error handling to prevent cascading loops: a dead transport fires
        // the same error on every pending operation (register, setToken, etc.)
        let errorRecoveryPending = false

        this.transport.on('authWillExpire', () => {
            console.debug('Connect: Token expiring, refreshing')
            this.updateToken()
        })

        this.transport.on('registered', () => {
            // Re-applies the choice after every (re)register and rebuild.
            this.transport.setAudioDevices({...phone.state.audioDeviceChoice})
            this._stopWatchingRegistration()
            this._autoAttempts = 0
            this._gaveUp = false
            errorRecoveryPending = false
            phone._setLineStatus(this, 'ready')
            this.registered = true
            // With two or more lines this can happen during a call on any of them,
            // and the person is still on that call. Ending it sets presence again.
            if (!(phone.isMultiLine && phone.state.inCall)) {
                phone._updatePresence('available')
            }
        })

        this.transport.on('unregistered', () => {
            this.registered = false
            this._stopWatchingRegistration()
            // If not in a call, attempt silent auto-recovery instead of staying offline
            if (!phone.state.inCall && phone.state.isActive) {
                // A transport error already owns the recovery, the line has given up
                // on it, or the network is what is missing.
                if (errorRecoveryPending || this._gaveUp || this._isOffline) return
                if (!this._spendAutoAttempt()) {
                    this._giveUp(_t("%s could not stay signed in after %s tries.", this.label, MAX_AUTO_ATTEMPTS))
                    return
                }
                phone._setLineStatus(this, 'connecting')
                this._watchRegistration()
                console.debug('Connect: Transport unregistered, attempting silent re-registration')
                clearTimeout(this._softRecoveryTimer)
                this._softRecoveryTimer = setTimeout(() => {
                    this._softRecoveryTimer = null
                    if (this.transport && this.transport.state === 'unregistered') {
                        this.updateToken({recovering: true})
                    }
                }, 3000)
            } else {
                phone._setLineStatus(this, 'offline', 'Lost the connection to the phone service.')
                this._reportOffline()
            }
        })

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
                if (errorRecoveryPending || this._reconnecting || this._gaveUp || this._isOffline) return
                errorRecoveryPending = true
                this._stopWatchingRegistration()
                if (!this._spendAutoAttempt()) {
                    this._giveUp(_t("%s could not sign in after %s tries: %s", this.label, MAX_AUTO_ATTEMPTS, error.message || error))
                    return
                }
                phone._setLineStatus(this, 'connecting')
                this._reportOffline()
                this._reconnectTimer = setTimeout(() => {
                    this._reconnectTimer = null
                    errorRecoveryPending = false
                    this.reconnect()
                }, isTransportError ? 5000 : 2000)
            } else if (kind === 'unsupported') {
                this._stopWatchingRegistration()
                console.error('Connect: Browser does not support required features:', error.message)
                this._failStart('This browser is missing features the phone needs.', 'NotSupported')
                this._reportOffline()
            } else {
                this._stopWatchingRegistration()
                phone._setLineStatus(this, 'error', error.message || 'Phone service error.')
                this._reportOffline()
            }
        })

        this.transport.on("incoming", (call) => this._onIncoming(call))

        // Register with exponential backoff retry (3 attempts: 2s, 4s, 8s)
        const transport = this.transport
        const registerWithRetry = async (attempt = 0) => {
            const maxAttempts = 3
            try {
                await transport.register()
            } catch (e) {
                // This transport was replaced, or the line stopped, while it was registering.
                if (this._destroyed || this._gaveUp || this.transport !== transport) {
                    return
                }
                if (attempt < maxAttempts - 1) {
                    const delay = Math.pow(2, attempt + 1) * 1000
                    console.warn(`Connect: Registration attempt ${attempt + 1} failed, retrying in ${delay}ms`)
                    this._registerRetryTimer = setTimeout(() => registerWithRetry(attempt + 1), delay)
                } else {
                    console.error('Connect: Registration failed after all retries')
                    this._stopWatchingRegistration()
                    phone._setLineStatus(this, 'error', 'Phone registration failed.')
                }
            }
        }
        this._watchRegistration()
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
