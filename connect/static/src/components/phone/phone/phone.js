/** @odoo-module **/
"use strict"
import {loadJS} from "@web/core/assets"
import {useService} from "@web/core/utils/hooks"
import {Calls} from "@connect/components/phone/calls/calls"
import {Favorites} from "@connect/components/phone/favorites/favorites"
import {Contacts} from "@connect/components/phone/contacts/contacts"
import {dialTone, setFocus} from "@connect/js/utils"
import {Component, useState, useRef, onWillStart, onMounted, onWillUnmount} from "@odoo/owl"
import {useDebounced} from "@web/core/utils/timing"
import {user} from "@web/core/user"

const uid = user.userId

// Audio unlock handler for browser autoplay restrictions
let audioUnlocked = false
let audioUnlockListenersAdded = false

function setupAudioUnlock() {
    if (audioUnlockListenersAdded) return
    audioUnlockListenersAdded = true

    const unlock = () => {
        if (audioUnlocked) return
        // Create and resume an AudioContext to unlock audio
        const AudioContext = window.AudioContext || window.webkitAudioContext
        if (AudioContext) {
            try {
                const ctx = new AudioContext()
                if (ctx.state === 'suspended') {
                    ctx.resume().then(() => {
                        ctx.close()
                        audioUnlocked = true
                        console.debug('Connect: Audio unlocked after user interaction')
                    }).catch(() => ctx.close())
                } else {
                    ctx.close()
                    audioUnlocked = true
                }
            } catch (e) {
                console.warn('Connect: Could not create AudioContext:', e)
            }
        }
        // Remove listeners after first successful interaction
        document.removeEventListener('click', unlock)
        document.removeEventListener('touchstart', unlock)
        document.removeEventListener('keydown', unlock)
    }

    // Listen for user interaction to unlock audio
    document.addEventListener('click', unlock, {passive: true})
    document.addEventListener('touchstart', unlock, {passive: true})
    document.addEventListener('keydown', unlock, {passive: true})
}

export class Phone extends Component {
    static template = 'connect.phone'
    static props = {
        bus: Object,
        token_data: Object
    }

    static components = {Calls, Contacts, Favorites}

    constructor() {
        super(...arguments)
        this.bus = this.props.bus
        this.token = this.props.token_data.token
        this.edge = this.props.token_data.edge
        this.callStatus = {
            NoAnswer: 'noanswer',
            Busy: 'busy',
            Rejected: 'busy',
            Answered: 'answered',
            Terminated: 'hangup',
            Canceled: 'canceled',
            Failed: 'failed'
        }
        this.tabs = {
            phone: 'phone',
            contacts: 'contacts',
            calls: 'calls',
            favorites: 'favorites',
        }
        this.status = {
            incoming: 'incoming',
            outgoing: 'outgoing',
            connecting: 'connecting',
            accepted: 'accepted',
            ended: 'ended'
        }
        this.title = 'Connect Phone'
        this.state = useState({
            isActive: true,
            isDisplay: false,
            isDisplayLastState: false,
            isCollapsed: false,  // New: collapsed to floating icon
            isMicrophoneMute: false,
            isSoundMute: localStorage.getItem('connect_is_sound_mute') === 'true',
            isKeypad: true,
            isContacts: false,
            isFavorites: false,
            isCalls: false,
            isPartner: false,
            isTransfer: false,
            isOnHold: false,
            holdInProgress: false,
            isAddParticipant: false,
            isAttendedTransfer: false,
            showTransferChoice: false,
            pendingTransferNumber: '',
            isDialingPanel: false,
            inCall: false,
            inIncoming: false,
            isContactList: false,
            isWhatsapp: false,
            phoneNumber: '',
            callPhoneNumber: '',
            contact_search_query: '',
            user_search_query: '',
            partnerName: '',
            partnerId: '',
            partnerUrl: '',
            partnerIconUrl: '',
            users: [],
            activeTab: this.tabs.phone,
            callDurationTime: '',
            callerId: {},
            hasWaitingCall: false,
            waitingCallerId: {},  // {phoneNumber, partnerName, partnerId}
            xTransferTo: '',
            xTransferInfo: '',
            xTransferPartner: false,
            phone_status: this.status.ended,
            calls: [],
            connectionStatus: 'connecting',  // 'connecting', 'ready', 'error', 'offline'
        })
        // Phone dimensions for drag constraints (golden ratio)
        this.phoneWidth = 300
        this.phoneHeight = 486
        this.collapsedSize = 56
        // Offset from collapsed icon center to dialog top-left (for position restore)
        this.collapseOffsetX = this.phoneWidth - 40  // Approximate minimize button X offset
        this.collapseOffsetY = 18  // Header center Y offset
        this.callDuration = 0
        this.callDurationTimerInstance = null
        this.phoneInput = useRef('connect-phone-input')

        this.user = uid
        this.sipRegistered = false
        this.lastActiveTab = this.tabs.phone
        this.session = null
        this.waitingSession = null  // Incoming call waiting while user is in active call
        this.heldSession = null
        this.heldCallSid = null
        this.heldCallId = null
        this.heldCallerId = null
        this.userAgent = null
        this.call_id = null
        this.call_sid = null  // Twilio CallSid for the current call
        this.call_popup_is_enabled = false
        this.call_popup_is_sticky = false
        this.phone_ring_volume = 70
        this.attended_transfer_sequence = '*7'
        this.disconnect_call_sequence = '**'
        // Move Phone
        this.mousePosition = {}
        this.offset = [0, 0]
        this.isDown = false
        this.wasDragged = false  // Track if drag occurred to prevent expand on drag end
        this.phoneRoot = useRef("phone-root")
        this.phoneHeader = useRef("phone-header")
        this.collapsedIcon = useRef("collapsed-icon")
        // BroadcastChannel
        this.bc = new BroadcastChannel("connect")
        this.contactSearch = 'all'
        this.id = Math.floor(Math.random() * 1000000)
        this.windows = [this.id]
        this.sipSessions = []
        this.suppressBroadcastChannel = false
    }

    setup() {
        super.setup()
        this.orm = useService('orm')
        this.action = useService('action')
        this.notification = useService("notification")

        this.notify = (message, {title = 'Connect', sticky = null, type = 'info'}) => {
            if (sticky === null) {
                sticky = this.call_popup_is_sticky
            }
            if (this.call_popup_is_enabled) {
                this.notification.add(message, {title, sticky, type})
            }
        }

        this.debounceEnterPhoneNumber = useDebounced((ev) => {
            this._onEnterPhoneNumber(ev)
        }, 400)

        onWillStart(async () => {
            await loadJS('/connect/static/src/lib/twilio.min.js')

            // EVENTS
            this.bus.addEventListener('busPhoneMakeCall', ({detail}) => this.prepareCall(detail))

            this.bus.addEventListener('busPhoneMakeTransfer', ({detail}) => this._busPhoneMakeTransfer(detail))

            this.bus.addEventListener('busPhoneAddParticipant', ({detail}) => this._busPhoneAddParticipant(detail))

            this.bus.addEventListener('busPhoneToggleDisplay', ({detail}) => this._busPhoneToggleDisplay(detail))

            this.bus.addEventListener('busPhoneHangUp', ({detail}) => this._busPhoneHangUp(detail))

            window.addEventListener("beforeunload", (event) => {
                if (this.session) {
                    event = event || window.event
                    const message = "You're in call! Are you sure you want to close?"
                    if (event) {
                        event.returnValue = message
                    }
                    return message
                }
            })

            window.addEventListener("unload", (event) => {
                // Best-effort presence offline on tab close
                this._updatePresence('offline')
                if (this.session) {
                    const params = {id: this.id, action: 'pop'}
                    this.bc.postMessage({event: 'tbcSipSession', params})
                    this.bc.postMessage({event: "tbcCloseTab", params: {id: this.id}})
                    this.session.disconnect()
                }
            })
        })

        onMounted(() => {
            // Suppress AbortError from Twilio SDK audio play/pause race conditions
            this._abortErrorHandler = (event) => {
                const error = event.reason
                if (error instanceof DOMException && error.name === 'AbortError') {
                    event.preventDefault()
                }
            }
            window.addEventListener('unhandledrejection', this._abortErrorHandler)

            // Setup audio unlock handler for browser autoplay restrictions
            setupAudioUnlock()

            // Handle tab visibility changes: when user returns to an idle tab,
            // the browser may have closed IDB connections and Twilio websocket.
            // Re-initialize the device if needed.
            this._visibilityHandler = () => {
                if (!document.hidden && this.state.isActive) {
                    if (this._needsTokenRefresh) {
                        this._needsTokenRefresh = false
                        this.updateToken()
                    }
                    // If Twilio device lost connection while tab was hidden, re-register
                    if (this.userAgent && this.userAgent.state === 'destroyed') {
                        console.debug('Connect: Twilio device was destroyed while tab was hidden, re-initializing')
                        this.initUserAgent()
                    }
                }
            }
            document.addEventListener('visibilitychange', this._visibilityHandler)

            this.initUserAgent()

            const self = this
            const phoneRoot = this.phoneRoot.el
            const startDrag = (e) => {
                self.isDown = true
                self.offset = [
                    phoneRoot.offsetLeft - e.clientX,
                    phoneRoot.offsetTop - e.clientY
                ]
            }

            // Drag from header when expanded
            this.phoneHeader.el.addEventListener("mousedown", startDrag, true)

            // Drag from collapsed icon when collapsed
            this.collapsedIcon.el.addEventListener("mousedown", (e) => {
                if (self.state.isCollapsed) {
                    e.stopPropagation()  // Prevent expand on drag
                    startDrag(e)
                }
            }, true)

            this._mouseUpHandler = function () {
                // Reset drag state after a short delay to allow click handler to check
                setTimeout(() => {
                    self.wasDragged = false
                }, 50)
                self.isDown = false
            }
            document.addEventListener("mouseup", this._mouseUpHandler, true)

            this._mouseMoveHandler = function (event) {
                if (self.isDown) {
                    event.preventDefault()
                    self.wasDragged = true  // Mark that a drag occurred
                    self.mousePosition = {
                        x: event.clientX,
                        y: event.clientY
                    }
                    const px = self.mousePosition.x + self.offset[0]
                    const py = self.mousePosition.y + self.offset[1]
                    const cx = document.documentElement.clientWidth
                    const cy = document.documentElement.clientHeight

                    // Get current dimensions based on collapsed state
                    const currentWidth = self.state.isCollapsed ? self.collapsedSize : self.phoneWidth
                    const currentHeight = self.state.isCollapsed ? self.collapsedSize : self.phoneHeight

                    // Allow docking at edges with no margin
                    let left = px < 0 ? 0 : px
                    left = left + currentWidth > cx ? cx - currentWidth : left
                    let top = py < 0 ? 0 : py
                    // Allow phone to dock at the very bottom with no gap
                    top = top + currentHeight > cy ? cy - currentHeight : top

                    phoneRoot.style.left = left + "px"
                    phoneRoot.style.top = top + "px"
                    // Remove bottom style when manually positioned
                    phoneRoot.style.bottom = "auto"
                }
            }
            document.addEventListener("mousemove", this._mouseMoveHandler, true)
            // BroadcastChannel Events
            this.bc.onmessage = ({data: {event, params}}) => {
                const localStartCall = () => {
                    if (self.session) return
                    const {callerId, isPartner} = params
                    self.state.isPartner = isPartner
                    self.state.callerId = callerId

                    self.state.inIncoming = true
                    self.state.isDialingPanel = true
                    self.startCall()
                }
                if (event === 'tbcStartCall') {
                    if (!self.session && !self.state.inIncoming) {
                        self.state.isDisplayLastState = self.state.isDisplay
                    }
                    localStartCall()
                    if (self.id === self.windows.at(-1) && !self.session) {
                        const ringParams = {id: self.sipSessions[0]}
                        self.bc.postMessage({event: "tbcRing", params: ringParams})
                    }
                } else if (event === 'tbcAnswerCall') {
                    if (self.session && params.id === self.id) {
                        self.session.accept()
                    }
                    localStartCall()
                    self.state.inIncoming = false
                    self.state.phone_status = self.status.accepted
                    if (self.session) {
                        setTimeout(() => {
                            localStartCall()
                            self.state.inIncoming = false
                            self.state.phone_status = self.status.accepted
                        }, 500)
                    }

                } else if (event === "tbcEndCall") {
                    if (self.session) {
                        self.suppressBroadcastChannel = true
                        self.session.disconnect()
                    }
                    self.state.phone_status = self.status.ended
                    self.endCall().then()
                } else if (event === 'tbcNewTab') {
                    self.windows.push(params.id)
                    if (self.session) {
                        const syncParams = self.getJsonCallData()
                        self.bc.postMessage({event: "tbcSync", params: syncParams})
                    }
                } else if (event === 'tbcCloseTab') {
                    const index = self.windows.indexOf(params.id)
                    if (index > -1) {
                        self.windows.splice(index, 1)
                        if (self.id === self.windows.at(-1) && self.userAgent && self.userAgent.state !== 'destroyed') {
                            self.userAgent.register()
                        }
                    }
                } else if (event === 'tbcDtmf') {
                    if (self.session) {
                        self.sendDTMF(params.key)
                    }
                } else if (event === 'tbcTransfer') {
                    // Transfer is handled by backend via Twilio API, no SIP action needed
                } else if (event === 'tbcHold') {
                    // Sync hold state from another tab (display only, no Twilio SDK call)
                    self.state.isOnHold = params.isOnHold
                    self.state.holdInProgress = false
                } else if (event === 'tbcWaitingCall') {
                    // Sync call waiting state from the tab that owns the session
                    self.state.hasWaitingCall = params.hasWaitingCall
                    self.state.waitingCallerId = params.waitingCallerId || {}
                } else if (event === 'tbcMicrophoneMute') {
                    if (self.session) {
                        if (params.mute === true) {
                            self.session.mute()
                        } else {
                            self.session.unmute()
                        }
                    }
                    self.state.isMicrophoneMute = params.mute
                } else if (event === 'tbcSoundMute') {
                    self.state.isSoundMute = params.mute
                    self.setIncomingVolume()
                } else if (event === 'tbcSync') {
                    if (self.state.inCall === false) {
                        self.state.callerId = params.callerId
                        self.state.isPartner = params.isPartner
                        self.state.inCall = true
                        self.state.phone_status = params.phoneStatus
                        self.startCall()
                    }
                } else if (event === 'tbcSipSession') {
                    const {action} = params
                    if (action === 'push') {
                        self.sipSessions.push(params.id)
                    } else if (action === 'clear') {
                        self.sipSessions = []
                    } else if (action === 'pop') {
                        const index = self.sipSessions.indexOf(params.id)
                        if (index > -1) {
                            self.sipSessions.splice(index, 1)
                        }
                        if (self.sipSessions.length === 0) {
                            self.state.phone_status = self.status.ended
                            self.endCall().then()
                        }
                    }
                } else if (event === 'tbcRing') {
                    // Ring handled by Twilio SDK on the tab that owns the session
                }
            }
            this.bc.postMessage({event: "tbcNewTab", params: {id: this.id}})
        })

        onWillUnmount(() => {
            if (this._abortErrorHandler) {
                window.removeEventListener('unhandledrejection', this._abortErrorHandler)
            }
            if (this._visibilityHandler) {
                document.removeEventListener('visibilitychange', this._visibilityHandler)
            }
            if (this._mouseUpHandler) {
                document.removeEventListener('mouseup', this._mouseUpHandler, true)
            }
            if (this._mouseMoveHandler) {
                document.removeEventListener('mousemove', this._mouseMoveHandler, true)
            }
            this.destroyCallCounter()
            this.bc.close()
        })
    }

    _busPhoneToggleDisplay() {
        this.state.isDisplayLastState = !this.state.isDisplay
        this.toggleDisplay()
    }

    async _busPhoneHangUp() {
        await this._onClickEndCall()
    }

    async _busPhoneMakeTransfer({phoneNumber} = {}) {
        // Show transfer choice dialog (Blind vs Attended)
        this.state.pendingTransferNumber = phoneNumber
        this.state.showTransferChoice = true
        this.state.isContacts = false
        this.state.isTransfer = false
        this.state.isDialingPanel = true
        this.state.xTransferTo = phoneNumber
    }

    async _onClickBlindTransfer() {
        const phoneNumber = this.state.pendingTransferNumber
        this.state.showTransferChoice = false
        this.state.pendingTransferNumber = ''
        if (this.session) {
            try {
                const result = await this.orm.call('connect.transfer_wizard', 'execute_transfer', [
                    phoneNumber,
                    'blind',
                    this.call_id,
                    this.session.parameters.CallSid
                ])
                if (result.success) {
                    this.notify(result.message, {sticky: false, type: 'success'})
                } else {
                    this.notify(result.error || 'Transfer failed', {sticky: false, type: 'warning'})
                }
            } catch (error) {
                console.error('Transfer error:', error)
                this.notify('Transfer failed', {sticky: false, type: 'warning'})
            }
        }
        this.bc.postMessage({event: "tbcTransfer", params: {phoneNumber}})
        this.endCall()
    }

    async _onClickAttendedTransfer() {
        const phoneNumber = this.state.pendingTransferNumber
        this.state.showTransferChoice = false
        this.state.pendingTransferNumber = ''
        const callSid = this.session?.parameters?.CallSid || this.call_sid
        if (!callSid) {
            this.notify('No active call', {type: 'warning'})
            return
        }
        try {
            const result = await this.orm.call('connect.call', 'initiate_attended_transfer', [callSid])
            if (result.success) {
                this.state.isAttendedTransfer = true
                this.state.isOnHold = true
                this.state.xTransferTo = phoneNumber
                this.state.xTransferInfo = 'Caller on hold — dial ' + phoneNumber + ' to consult'
                this.notify('Caller on hold. Dial the consult target.', {type: 'info'})
            } else {
                this.notify(result.error || 'Failed to hold caller', {type: 'warning'})
            }
        } catch (e) {
            console.error('Attended transfer error:', e)
            this.notify('Failed to initiate attended transfer', {type: 'warning'})
        }
    }

    async _onClickCompleteTransfer() {
        const callSid = this.session?.parameters?.CallSid || this.call_sid
        const consultTarget = this.state.xTransferTo
        if (!callSid || !consultTarget) {
            this.notify('Missing call or transfer target', {type: 'warning'})
            return
        }
        try {
            const result = await this.orm.call('connect.call', 'complete_attended_transfer', [callSid, consultTarget])
            if (result.success) {
                this.notify('Transfer completed', {type: 'success'})
                this.bc.postMessage({event: "tbcTransfer", params: {phoneNumber: consultTarget}})
                this.endCall()
            } else {
                this.notify(result.error || 'Transfer completion failed', {type: 'warning'})
            }
        } catch (e) {
            console.error('Complete transfer error:', e)
            this.notify('Transfer completion failed', {type: 'warning'})
        }
    }

    async _onClickCancelTransfer() {
        const callSid = this.session?.parameters?.CallSid || this.call_sid
        if (!callSid) return
        try {
            const result = await this.orm.call('connect.call', 'cancel_attended_transfer', [callSid])
            if (result.success) {
                this.state.isAttendedTransfer = false
                this.state.isOnHold = false
                this.state.xTransferTo = ''
                this.state.xTransferInfo = ''
                this.notify('Transfer cancelled, caller resumed', {type: 'info'})
            } else {
                this.notify(result.error || 'Cancel failed', {type: 'warning'})
            }
        } catch (e) {
            console.error('Cancel transfer error:', e)
            this.notify('Cancel transfer failed', {type: 'warning'})
        }
    }

    async _busPhoneAddParticipant({phoneNumber} = {}) {
        const callSid = this.session?.parameters?.CallSid || this.call_sid
        if (!callSid) {
            this.notify('No active call', {type: 'warning'})
            return
        }
        try {
            const result = await this.orm.call('connect.call', 'add_conference_participant', [callSid, phoneNumber])
            if (result.success) {
                this.notify('Adding participant...', {type: 'info'})
            } else {
                this.notify(result.error || 'Failed to add participant', {type: 'warning'})
            }
        } catch (e) {
            console.error('Add participant error:', e)
            this.notify('Failed to add participant', {type: 'warning'})
        }
        this.state.isAddParticipant = false
        this.state.isContacts = false
        this.state.isDialingPanel = true
    }

    async prepareCall(props) {
        if (!this.state.inCall) {
            this.state.isContactList = false
            this.state.callPhoneNumber = props.phone
            await this.searchPartner(props.phone)
            this.makeCall(props)
        }
    }

    async setCallStatus(status) {
        const currentCallStatus = this.callStatus[status] ? this.callStatus[status] : this.callStatus.Failed
        this.notify(currentCallStatus.toUpperCase(), {sticky: false})
    }

    async updateToken() {
        try {
            const {token} = await this.orm.call('connect.user', 'get_client_token')
            if (token) {
                this.userAgent.updateToken(token)
                this.token = token
                // Re-register after token update to ensure device stays connected
                if (this.userAgent.state !== 'destroyed') {
                    try {
                        this.userAgent.register()
                    } catch (regErr) {
                        console.warn('Connect: Re-registration after token refresh failed:', regErr.message)
                    }
                }
            }
        } catch (e) {
            console.warn('Connect: Token refresh failed, will retry when tab is active:', e.message)
            this._needsTokenRefresh = true
        }
    }

    initUserAgent() {
        const self = this
        if (!self.state.isActive) {
            return
        }

        // Check WebRTC support before initializing Twilio
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            console.error('Connect: WebRTC not supported in this browser')
            self.state.isActive = false
            self.bus.trigger('busTraySetException', {exception: 'NotSupported'})
            return
        }

        try {
            self.userAgent = new Twilio.Device(self.token, {
                edge: self.edge,
                logLevel: 4,
                codecPreferences: ["opus", "pcmu"],
                // Allow incoming audio even if AudioContext is suspended initially
                allowIncomingWhileBusy: true
            })
        } catch (error) {
            console.error('Connect: Failed to create Twilio Device:', error)
            self.state.isActive = false
            self.bus.trigger('busTraySetException', {exception: error.name || 'InitFailed'})
            return
        }

        // Try to set incoming volume, but don't fail if AudioContext is blocked
        try {
            this.setIncomingVolume()
        } catch (e) {
            console.warn('Connect: Could not set incoming volume (AudioContext may be blocked):', e)
        }
        self.userAgent.on('tokenWillExpire', () => {
            console.debug('Connect: Token expiring, refreshing')
            self.updateToken().then()
        })

        self.userAgent.on('registered', () => {
            self.state.connectionStatus = 'ready'
            self.sipRegistered = true
            self._updatePresence('available')
        })

        self.userAgent.on('unregistered', () => {
            self.state.connectionStatus = 'offline'
            self.sipRegistered = false
            self._updatePresence('offline')
        })

        self.userAgent.on('error', (error) => {
            console.error('Connect: Device error:', error.message || error)
            self.state.connectionStatus = 'error'
            self._updatePresence('offline')
            if (error.code === 31009 || error.code === 31005) {
                // Transport/connection error — try to re-register after 5 seconds
                setTimeout(() => {
                    try {
                        self.userAgent.register()
                    } catch (e) {
                        console.error('Connect: Re-registration failed:', e)
                    }
                }, 5000)
            } else if (error.name === 'AccessTokenExpired') {
                self.updateToken().then()
            } else if (error.name === 'AccessTokenInvalid') {
                self.bus.trigger('busTraySetException', {exception: error.name})
            } else if (error.name === 'NotSupportedError') {
                console.error('Connect: Browser does not support required features:', error.message)
                self.state.isActive = false
                self.bus.trigger('busTraySetException', {exception: 'NotSupported'})
            }
        })
        let lastTime = (new Date()).getTime()
        // HANDLE RTCSession
        self.userAgent.on("incoming", async function (session) {
            self.state.isContactList = false
            let phoneNumber = session.customParameters.get('From')
            phoneNumber = phoneNumber ? phoneNumber : session.parameters.From
            if (phoneNumber.startsWith("whatsapp:")) {
                phoneNumber = phoneNumber.replace(/^whatsapp:/, "")
                self.state.isWhatsapp = true
            }
            const callCallerName = session.customParameters.get('CallerName')
            const callPartnerId = session.customParameters.get('Partner')
            const autoAnswer = session.customParameters.get('autoAnswer')

            if (self.session === null) {
                self.session = session
                self.sipSessions.push(self.id)
                const params = {id: self.id, action: 'push'}
                self.bc.postMessage({event: 'tbcSipSession', params})
            } else {
                // During attended transfer, incoming call may be the consult leg — don't reject
                if (self.state.isAttendedTransfer) {
                    self.waitingSession = session
                    self.state.hasWaitingCall = true
                    self.state.waitingCallerId = {
                        phoneNumber,
                        partnerName: callCallerName || '',
                        partnerId: callPartnerId !== 'false' ? callPartnerId : false,
                    }
                    self.notify('Consult call from ' + (callCallerName || phoneNumber), {type: 'info', sticky: true})
                    self.bc.postMessage({event: "tbcWaitingCall", params: {
                        hasWaitingCall: true,
                        waitingCallerId: {...self.state.waitingCallerId},
                    }})
                    return
                }
                // Call waiting: store the incoming session instead of rejecting
                if (self.waitingSession) {
                    // Already have a waiting call — reject the third one
                    session.reject()
                    return
                }
                self.waitingSession = session
                self.state.hasWaitingCall = true

                // Extract caller info for display
                self.state.waitingCallerId = {
                    phoneNumber,
                    partnerName: callCallerName || '',
                    partnerId: callPartnerId !== 'false' ? callPartnerId : false,
                }

                // Notify user and sync waiting call state to other tabs
                self.notify('Incoming call from ' + (callCallerName || phoneNumber), {type: 'info', sticky: true})
                self.bc.postMessage({event: "tbcWaitingCall", params: {
                    hasWaitingCall: true,
                    waitingCallerId: {...self.state.waitingCallerId},
                }})

                // Set up handlers for the waiting session
                session.on('cancel', () => {
                    // Caller hung up before we answered
                    self.waitingSession = null
                    self.state.hasWaitingCall = false
                    self.state.waitingCallerId = {}
                    self.notify('Waiting call ended', {type: 'info'})
                    self.bc.postMessage({event: "tbcWaitingCall", params: {
                        hasWaitingCall: false,
                        waitingCallerId: {},
                    }})
                })

                session.on('disconnect', () => {
                    if (self.waitingSession === session) {
                        self.waitingSession = null
                        self.state.hasWaitingCall = false
                        self.state.waitingCallerId = {}
                        self.bc.postMessage({event: "tbcWaitingCall", params: {
                            hasWaitingCall: false,
                            waitingCallerId: {},
                        }})
                    }
                })

                return
            }

            self.state.callPhoneNumber = phoneNumber

            if (callPartnerId !== 'false') {
                self.state.isPartner = true
                self.state.callerId = {
                    partnerId: parseInt(callPartnerId),
                    partnerName: callCallerName,
                    partnerIconUrl: self.computePartnerIconUrl(callPartnerId),
                    partnerUrl: self.computePartnerUrl(callPartnerId),
                    phoneNumber,
                }
            } else {
                self.state.callerId = {phoneNumber}
                await self.searchPartner(phoneNumber)
            }

            self.state.isDisplayLastState = self.state.isDisplay
            if (!self.state.isDisplay) {
                self.toggleDisplay()
            }
            const params = self.getJsonCallData()
            self.bc.postMessage({event: "tbcStartCall", params})

            self.state.inIncoming = true
            self.state.isDialingPanel = true
            self.startCall()
            // incoming call here
            session.on("accept", async function (data) {
                // console.log('incoming -> accept: ', data)
                // Store CallSid for forward functionality
                self.call_sid = session.parameters?.CallSid || null
                self.createCallCounter(phoneNumber)
                self.state.phone_status = self.status.accepted
                self._updatePresence('on_call')
                await self.setCallStatus("Answered")
            })
            session.on("disconnect", async function (data) {
                // console.log('incoming -> ended: ', data)
                self.state.phone_status = self.status.ended
                await self.setCallStatus("Canceled")
                await self.endCall()
                self.session = null
                if (self.suppressBroadcastChannel) {
                    self.suppressBroadcastChannel = false
                } else {
                    self.bc.postMessage({event: "tbcEndCall"})
                }
            })
            session.on("cancel", async function (data) {
                // console.log('incoming -> failed: ', data)
                self.state.phone_status = self.status.ended
                await self.setCallStatus("Canceled")
                const index = self.sipSessions.indexOf(self.id)
                self.sipSessions.splice(index, 1)
                const params = {id: self.id, action: 'pop'}
                self.bc.postMessage({event: 'tbcSipSession', params})
                self.session = null
                await self.endCall()
            })
            session.on("reject", async function (data) {
                // console.log('incoming -> reject')
                self.state.phone_status = self.status.ended
                await self.setCallStatus("Rejected")
                const index = self.sipSessions.indexOf(self.id)
                self.sipSessions.splice(index, 1)
                const params = {id: self.id, action: 'pop'}
                self.bc.postMessage({event: 'tbcSipSession', params})
                self.session = null
                await self.endCall()
            })

            if (autoAnswer === 'yes') {
                // console.log('Auto Answer')
                session.accept()
                self.state.phone_status = self.status.accepted
                self.state.inIncoming = false
                self.startCall()
            }
        })

        // Register with exponential backoff retry (3 attempts: 2s, 4s, 8s)
        const registerWithRetry = async (attempt = 0) => {
            const maxAttempts = 3
            try {
                await self.userAgent.register()
            } catch (e) {
                if (attempt < maxAttempts - 1) {
                    const delay = Math.pow(2, attempt + 1) * 1000
                    console.warn(`Connect: Registration attempt ${attempt + 1} failed, retrying in ${delay}ms`)
                    setTimeout(() => registerWithRetry(attempt + 1), delay)
                } else {
                    console.error('Connect: Registration failed after all retries')
                    self.notify('Phone registration failed. Try refreshing the page.', {type: 'warning', sticky: true})
                }
            }
        }
        registerWithRetry()
    }

    setIncomingVolume() {
        try {
            if (this.userAgent && this.userAgent.audio) {
                this.userAgent.audio.incoming(!this.state.isSoundMute)
            }
        } catch (e) {
            console.warn('Connect: Could not set incoming volume:', e)
        }
    }

    async _updatePresence(status) {
        try {
            await this.orm.call('connect.user', 'update_presence', [status])
        } catch (e) {
            // Don't let presence failures affect call operations
            console.warn('Connect: Presence update failed:', e)
        }
    }

    getJsonCallData() {
        return {
            id: this.session ? this.id : this.sipSessions[0],
            isPartner: this.state.isPartner,
            phoneStatus: this.state.phone_status,
            callerId: JSON.parse(JSON.stringify(this.state.callerId)),
        }
    }

    async makeCall(props) {
        const self = this
        let phoneNumber = props.phone
        self.startCall()

        const syncParams = self.getJsonCallData()
        self.bc.postMessage({event: "tbcSync", params: syncParams})

        const params = {
            To: phoneNumber,
            Called: phoneNumber,
        }

        self.session = await self.userAgent.connect({params})

        self.session.on("accept", async function () {
            // console.log('outgoing -> accepted: ', data)
            // Store CallSid for forward functionality
            self.call_sid = self.session.parameters?.CallSid || null
            self.createCallCounter(phoneNumber)
            self.state.phone_status = self.status.accepted
            self._updatePresence('on_call')
            await self.setCallStatus("Answered")
            const params = self.getJsonCallData()
            self.bc.postMessage({event: "tbcAnswerCall", params})
        })
        self.session.on("disconnect", async function () {
            // console.log('outgoing -> ended: ', data)
            self.state.phone_status = self.status.ended
            await self.setCallStatus('Disconnect')
            await self.endCall()
            self.session = null
            if (self.suppressBroadcastChannel) {
                self.suppressBroadcastChannel = false
            } else {
                self.bc.postMessage({event: "tbcEndCall"})
            }
        })
        self.session.on("cancel", async function () {
            // console.log('outgoing -> ended: ', data)
            self.state.phone_status = self.status.ended
            await self.setCallStatus('Cancel')
            await self.endCall()
            self.session = null
            if (self.suppressBroadcastChannel) {
                self.suppressBroadcastChannel = false
            } else {
                self.bc.postMessage({event: "tbcEndCall"})
            }
        })
    }

    startCall() {
        this.state.inCall = true
        this.state.isDialingPanel = true
        this.state.isContacts = false
        this.state.isFavorites = false
        this.state.isCalls = false
        this.state.isDisplay = true
        this.state.isCollapsed = false  // Expand if collapsed when call comes in
        this.state.isKeypad = false
        this.bus.trigger('busTrayState', {isDisplay: this.state.isDisplay, inCall: this.state.inCall})
        // Ensure phone is visible within viewport (defer until DOM updates)
        requestAnimationFrame(() => this._ensureWithinViewport())
    }

    async endCall() {
        this._updatePresence(this.sipRegistered ? 'available' : 'offline')
        this.call_sid = null  // Clear call SID
        this.state.isDisplay = this.state.isDisplayLastState
        this.state.isContactList = false
        this.state.isDialingPanel = false
        this.state.inIncoming = false
        this.state.isKeypad = this.lastActiveTab === this.tabs.phone
        this.state.isContacts = this.lastActiveTab === this.tabs.contacts
        this.state.isFavorites = this.lastActiveTab === this.tabs.favorites
        this.state.isCalls = this.lastActiveTab === this.tabs.calls
        this.state.isTransfer = false
        this.state.isOnHold = false
        this.state.holdInProgress = false
        this.state.isAddParticipant = false
        this.state.isAttendedTransfer = false
        this.state.showTransferChoice = false
        this.state.pendingTransferNumber = ''
        this.state.isMicrophoneMute = false
        this.state.isPartner = false
        this.state.isWhatsapp = false
        this.state.callerId = {}
        this.state.phoneNumber = ''
        this.state.xPhoneInfoDisplay = ''
        this.phoneInput.el.value = this.state.phoneNumber
        this.bus.trigger('busTrayState', {isDisplay: this.state.isDisplay, inCall: this.state.inCall})
        this.state.activeTab = this.lastActiveTab
        if (this.lastActiveTab === this.tabs.calls) {
            this.getCalls()
        }
        const self = this
        setTimeout(() => self.state.inCall = false, 100)

        this.destroyCallCounter()
        this.sipSessions = []
        this.state.xTransferTo = ''
        this.state.xTransferInfo = ''
        this.state.xTransferPartner = false
        // Clear call waiting state
        this.waitingSession = null
        this.state.hasWaitingCall = false
        this.state.waitingCallerId = {}
        this.heldSession = null
        this.heldCallSid = null
        this.heldCallId = null
        this.heldCallerId = null
    }

    _openPartner(id) {
        this.action.doAction({
            res_id: id,
            res_model: 'res.partner',
            target: 'current',
            type: 'ir.actions.act_window',
            views: [[false, 'form']],
        })
    }

    async searchPartner(phoneNumber) {
        const partner = await this.getPartner(phoneNumber)
        if (partner) {
            this.state.isPartner = true
            this.state.callerId = this.computePartnerData(partner, phoneNumber)
        } else {
            this.state.isPartner = false
            const pbxUser = await this.getUser(phoneNumber)
            if (pbxUser) {
                this.state.callerId = this.computeUserData(pbxUser, phoneNumber)
            } else {
                this.state.callerId = {phoneNumber}
            }
        }
        return partner
    }

    async getPartner(phoneNumber) {
        const partner = await this.orm.call("res.partner", 'api_get_partner', [phoneNumber])
        return partner.id ? partner : false
    }

    computePartnerData(partner, phoneNumber) {
        return {
            partnerId: partner.id,
            partnerName: partner.name,
            partnerIconUrl: this.computePartnerIconUrl(partner.id),
            partnerUrl: this.computePartnerUrl(partner.id),
            phoneNumber: phoneNumber,
        }
    }

    computePartnerUrl(partnerId) {
        return `/web#id=${partnerId}&model=res.partner&view_type=form`
    }

    computePartnerIconUrl(partnerId) {
        return `/web/image?model=res.partner&field=avatar_128&id=${partnerId}`
    }

    async getUser(phoneNumber) {
        return await this.orm.call("connect.user", 'get_user_by_exten_number', [phoneNumber])
    }

    computeUserData(user, phoneNumber) {
        return {
            partnerId: user.id,
            partnerName: user.name,
            partnerIconUrl: this.computeUserIconUrl(user.user[0]),
            phoneNumber: phoneNumber,
        }
    }

    computeUserIconUrl(userId) {
        return `/web/image?model=res.users&field=avatar_128&id=${userId}`
    }

    createCallCounter(phoneNumber) {
        const self = this
        self.callDuration = 0
        self.state.callDurationTime = '00:00:00'
        self.callDurationTimerInstance = setInterval(() => {
            self.callDuration += 1
            if (self.state.callerId.phoneNumber === phoneNumber) {
                self.state.callDurationTime = new Date((self.callDuration) * 1000).toISOString().substring(11, 19)
            }
        }, 1000)
    }

    destroyCallCounter() {
        const self = this
        self.state.callDurationTime = ''
        clearInterval(self.callDurationTimerInstance)
    }

    setLastActiveTab() {
        this.lastActiveTab = this.state.activeTab
    }

    toggleDisplay() {
        if (this.state.isActive) {
            this.state.isDisplay = !this.state.isDisplay
            // Reset collapsed state when toggling via systray
            this.state.isCollapsed = false
            if (this.state.inCall) {
                this.state.isKeypad = false
                this.state.isDialingPanel = true
                this.state.isContacts = false
                this.state.isCalls = false
                this.state.activeTab = this.tabs.phone
                this.bus.trigger('busTraySetState', {isDisplay: this.state.isDisplay, inCall: this.state.inCall})
            } else {
                setFocus(this.phoneInput.el)
            }
            // When showing, ensure phone is within viewport bounds
            // Double rAF to ensure DOM is fully updated after OWL render
            if (this.state.isDisplay) {
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => this._ensureWithinViewport())
                })
            }
        } else {
            this.notify('Missing configs! Check "User / Preferences"!', {sticky: false})
        }
    }

    // Ensure phone dialog is within viewport bounds
    _ensureWithinViewport() {
        const phoneRoot = this.phoneRoot.el
        if (!phoneRoot) return

        const cx = document.documentElement.clientWidth
        const cy = document.documentElement.clientHeight
        const width = this.state.isCollapsed ? this.collapsedSize : this.phoneWidth
        const height = this.state.isCollapsed ? this.collapsedSize : this.phoneHeight

        // Use getBoundingClientRect for accurate position of fixed element
        const rect = phoneRoot.getBoundingClientRect()
        let newLeft = rect.left
        let newTop = rect.top

        // Fallback: if rect returns zeros (element was hidden), parse from inline style
        if (rect.width === 0 && rect.height === 0) {
            newLeft = parseFloat(phoneRoot.style.left) || 0
            newTop = parseFloat(phoneRoot.style.top) || 0
        }

        // Clamp to viewport bounds
        // Right edge: ensure dialog right edge doesn't exceed viewport
        if (newLeft + width > cx) {
            newLeft = cx - width
        }
        // Left edge
        if (newLeft < 0) {
            newLeft = 0
        }
        // Bottom edge: ensure dialog bottom doesn't exceed viewport
        if (newTop + height > cy) {
            newTop = cy - height
        }
        // Top edge
        if (newTop < 0) {
            newTop = 0
        }

        // Always apply the clamped position
        phoneRoot.style.left = newLeft + "px"
        phoneRoot.style.top = newTop + "px"
        phoneRoot.style.bottom = "auto"
    }

    getCalls() {
        this.bus.trigger('busCallsGetCalls')
    }

    _onClickMakeCall(ev) {
        if (this.state.phoneNumber) {
            this.state.callPhoneNumber = this.state.phoneNumber.replace(/\(|\)|-| /gm, '')
            this.state.phoneNumber = ''
            this.phoneInput.el.value = this.state.phoneNumber
            this.prepareCall({phone: this.state.callPhoneNumber})
        } else {
            this.notify("The phone call has no number!", {sticky: false})
        }
    }

    _onClickContactCall(phoneNumber) {
        this.prepareCall({phone: phoneNumber})
    }

    _onClickPhone(ev) {
        this.state.activeTab = this.tabs.phone
        this.setLastActiveTab()
        if (this.state.inCall) {
            this.state.isKeypad = false
            this.state.isDialingPanel = true
        } else {
            this.state.isKeypad = true
            this.state.isDialingPanel = false
        }
        this.state.isContacts = false
        this.state.isCalls = false
        this.state.isFavorites = false
        setFocus(this.phoneInput.el)
    }

    _onClickContacts(ev) {
        this.state.activeTab = this.tabs.contacts
        this.setLastActiveTab()
        this.bus.trigger('busContactSetState', {isContact: true, isContactMode: true})
        this.state.isKeypad = false
        this.state.isContacts = true
        this.state.isContactList = false
        this.state.isFavorites = false
        this.state.isCalls = false
        this.state.isDialingPanel = false
    }

    _onClickFavorites(ev) {
        this.state.activeTab = this.tabs.favorites
        this.setLastActiveTab()
        this.state.isKeypad = false
        this.state.isContacts = false
        this.state.isContactList = false
        this.state.isFavorites = true
        this.state.isCalls = false
        this.state.isDialingPanel = false
    }

    _onClickHistory(ev) {
        this.state.activeTab = this.tabs.calls
        this.setLastActiveTab()
        this.state.isKeypad = false
        this.state.isContacts = false
        this.state.isContactList = false
        this.state.isFavorites = false
        this.state.isCalls = true
        this.state.isDialingPanel = false
        this.getCalls()
    }

    _onClickDialingPanel(ev) {
        this.state.activeTab = this.tabs.phone
        this.state.isContacts = false
        this.state.isTransfer = false
        this.state.isAddParticipant = false
        this.state.isCalls = false
        this.state.isKeypad = false
        this.state.isDialingPanel = true
    }

    _onClickKeypad(ev) {
        this.state.activeTab = this.tabs.phone
        this.state.isContacts = false
        this.state.isTransfer = false
        this.state.isAddParticipant = false
        this.state.isCalls = false
        this.state.isKeypad = true
        this.state.isDialingPanel = false
        setFocus(this.phoneInput.el)
    }

    async _onClickHold(ev) {
        if (this.state.holdInProgress) return
        this.state.holdInProgress = true
        const callSid = this.session?.parameters?.CallSid || this.call_sid
        if (!callSid) {
            this.state.holdInProgress = false
            this.notify('No active call', {type: 'warning'})
            return
        }
        try {
            if (this.state.isOnHold) {
                const result = await this.orm.call('connect.call', 'resume_call', [callSid])
                if (result.success) {
                    this.state.isOnHold = false
                    this._updatePresence('on_call')
                    this.notify('Call resumed', {type: 'info'})
                    this.bc.postMessage({event: "tbcHold", params: {isOnHold: false}})
                } else {
                    this.notify(result.error || 'Resume failed', {type: 'warning'})
                }
            } else {
                const result = await this.orm.call('connect.call', 'hold_call', [callSid])
                if (result.success) {
                    this.state.isOnHold = true
                    this._updatePresence('on_hold')
                    this.notify('Call on hold', {type: 'info'})
                    this.bc.postMessage({event: "tbcHold", params: {isOnHold: true}})
                } else {
                    this.notify(result.error || 'Hold failed', {type: 'warning'})
                }
            }
        } catch (e) {
            console.error('Hold error:', e)
            this.notify('Hold operation failed', {type: 'warning'})
        } finally {
            this.state.holdInProgress = false
        }
    }

    _onClickTransfer(ev) {
        if (this.state.isTransfer) return
        this.state.isAddParticipant = false
        this.state.isKeypad = false
        this.state.isDialingPanel = false
        this.state.isTransfer = true
        this.state.isContacts = true
        this.bus.trigger('busContactSetState', {isTransfer: true, isContactMode: true})
    }

    _onClickAddParticipant(ev) {
        if (this.state.isAddParticipant) return
        this.state.isTransfer = false
        this.state.isKeypad = false
        this.state.isDialingPanel = false
        this.state.isAddParticipant = true
        this.state.isContacts = true
        this.bus.trigger('busContactSetState', {isAddParticipant: true, isContactMode: true})
    }

    _onClickMicrophoneMute(ev) {
        if (this.session) {
            if (this.state.isMicrophoneMute) {
                this.session.mute(false)
            } else {
                this.session.mute(true)
            }
        }
        this.state.isMicrophoneMute = !this.state.isMicrophoneMute
        this.bc.postMessage({event: "tbcMicrophoneMute", params: {mute: this.state.isMicrophoneMute}})
    }

    _onClickSoundMute(ev) {
        this.state.isSoundMute = !this.state.isSoundMute
        localStorage.setItem('connect_is_sound_mute', `${this.state.isSoundMute}`)
        this.bc.postMessage({event: "tbcSoundMute", params: {mute: this.state.isSoundMute}})
        this.setIncomingVolume()
    }


    async _onClickEndCall(ev) {
        if (this.session) {
            this.suppressBroadcastChannel = true
            this.session.disconnect()
        }
        this.bc.postMessage({event: "tbcEndCall"})
        this.state.phone_status = this.status.ended
        await this.endCall()
        if (this.lastActiveTab === this.tabs.phone) {
            setFocus(this.phoneInput.el)
        }
    }

    _onClickAcceptIncoming(ev) {
        if (this.session) {
            this.session.accept()
        }
        const params = this.getJsonCallData()
        this.bc.postMessage({event: "tbcAnswerCall", params})
        this.state.phone_status = this.status.accepted
        this.state.inIncoming = false
        this.startCall()
    }

    async _onClickRejectIncoming(ev) {
        if (this.session) {
            this.suppressBroadcastChannel = true
            this.session.reject()
        }
        this.bc.postMessage({event: "tbcEndCall"})
        this.state.inIncoming = false
        await this.endCall()
        if (this.lastActiveTab === this.tabs.phone) {
            setFocus(this.phoneInput.el)
        }
    }

    async _onClickAcceptWaiting() {
        if (!this.waitingSession) return

        // Put current call on hold first
        const callSid = this.session?.parameters?.CallSid || this.call_sid
        if (callSid) {
            try {
                await this.orm.call('connect.call', 'hold_call', [callSid])
                this.state.isOnHold = true
            } catch (e) {
                console.error('Connect: Failed to hold current call:', e)
            }
        }

        // Store current session info for potential swap-back
        this.heldSession = this.session
        this.heldCallSid = this.call_sid
        this.heldCallId = this.call_id
        this.heldCallerId = {...this.state.callerId}

        // Accept the waiting call
        this.session = this.waitingSession
        this.waitingSession = null
        this.state.hasWaitingCall = false
        this.state.waitingCallerId = {}

        this.session.accept()
        this.state.phone_status = this.status.accepted
        this.state.inIncoming = false
        this.bc.postMessage({event: "tbcWaitingCall", params: {hasWaitingCall: false, waitingCallerId: {}}})
        this.bc.postMessage({event: "tbcHold", params: {isOnHold: true}})
    }

    _onClickRejectWaiting() {
        if (!this.waitingSession) return
        this.waitingSession.reject()
        this.waitingSession = null
        this.state.hasWaitingCall = false
        this.state.waitingCallerId = {}
        this.bc.postMessage({event: "tbcWaitingCall", params: {hasWaitingCall: false, waitingCallerId: {}}})
    }

    // Hide phone entirely (to systray)
    _onClickHide(ev) {
        ev.stopPropagation()
        this.state.isDisplayLastState = !this.state.isDisplay
        this.state.isCollapsed = false  // Reset collapsed state when hiding
        this.toggleDisplay()
    }

    _onClickKeypadButton(ev) {
        if (this.state.inCall) {
            if (this.session) {
                this.sendDTMF(ev.target.textContent)
            } else {
                this.bc.postMessage({event: "tbcDtmf", params: {key: ev.target.textContent}})
            }
        } else {
            this.state.phoneNumber += ev.target.textContent
            this.phoneInput.el.value = this.state.phoneNumber
        }
        this.phoneInput.el.focus()
    }

    _onClickBackSpace(ev) {
        setFocus(this.phoneInput.el)
        this.state.phoneNumber = this.state.phoneNumber.slice(0, -1)
        this.phoneInput.el.value = this.state.phoneNumber
        if (this.state.isContactList) {
            this.bus.trigger('busContactSearchQuery', {searchQuery: this.phoneInput.el.value})
        }
        if (this.state.phoneNumber === '') this.state.isContactList = false
    }

    sendDTMF(key) {
        const validDTMF = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '*', '#']
        if (validDTMF.includes(key)) {
            // dialTone(key)
            this.session.sendDigits(key)
        }
    }


    _onEnterPhoneNumber(ev) {
        if (this.state.inCall) {
            this.sendDTMF(ev.key)
        } else {
            if (ev.key === "Enter") {
                this._onClickMakeCall()
            } else {
                this.state.phoneNumber = this.phoneInput.el.value
                this.state.isContactList = this.state.phoneNumber !== ''
                this.bus.trigger('busContactSetState', {isContact: true})
                this.bus.trigger('busContactSearchQuery', {searchQuery: this.phoneInput.el.value})
            }
        }
    }

    _createPartner() {
        const context = {
            default_phone: this.state.callerId.phoneNumber,
            call_id: this.call_id,
            default_name: `Partner ${this.state.callerId.phoneNumber}`
        }
        this.action.doAction({
            context,
            res_model: 'res.partner',
            target: 'new',
            type: 'ir.actions.act_window',
            views: [[false, 'form']],
        })
    }

    // Collapse to floating phone icon at the minimize button's position
    _onClickCollapse(ev) {
        ev.stopPropagation()

        const phoneRoot = this.phoneRoot.el
        const buttonRect = ev.target.getBoundingClientRect()

        // Calculate position to center the collapsed icon (56px) on the button
        const collapsedX = buttonRect.left + (buttonRect.width / 2) - (this.collapsedSize / 2)
        const collapsedY = buttonRect.top + (buttonRect.height / 2) - (this.collapsedSize / 2)

        // Store the offset from the collapsed icon center to the dialog's top-left
        // This is used to restore position when expanding
        this.collapseOffsetX = buttonRect.left + (buttonRect.width / 2) - phoneRoot.offsetLeft
        this.collapseOffsetY = buttonRect.top + (buttonRect.height / 2) - phoneRoot.offsetTop

        // Position the collapsed icon
        phoneRoot.style.left = collapsedX + "px"
        phoneRoot.style.top = collapsedY + "px"
        phoneRoot.style.bottom = "auto"

        this.state.isCollapsed = true
    }

    // Expand from collapsed icon (only if not dragging)
    _onClickCollapsedIcon(ev) {
        if (this.state.isCollapsed && !this.wasDragged) {
            ev.stopPropagation()

            const phoneRoot = this.phoneRoot.el
            const currentLeft = phoneRoot.offsetLeft
            const currentTop = phoneRoot.offsetTop
            const iconCenterX = currentLeft + (this.collapsedSize / 2)
            const iconCenterY = currentTop + (this.collapsedSize / 2)

            // Calculate ideal position so the minimize button appears where the icon was
            const newLeft = iconCenterX - this.collapseOffsetX
            const newTop = iconCenterY - this.collapseOffsetY

            phoneRoot.style.left = newLeft + "px"
            phoneRoot.style.top = newTop + "px"

            this.state.isCollapsed = false
            // Ensure within viewport after expanding
            this._ensureWithinViewport()
        }
    }

    // Double-click on header also collapses (use center of header as reference)
    _onHeaderDoubleClick(ev) {
        ev.stopPropagation()
        if (this.state.isCollapsed) {
            this._onClickCollapsedIcon(ev)
        } else {
            // Simulate clicking the minimize button position
            const header = this.phoneHeader.el
            const headerRect = header.getBoundingClientRect()
            // Use position similar to where minimize button would be
            this.collapseOffsetX = headerRect.right - 40 - this.phoneRoot.el.offsetLeft
            this.collapseOffsetY = headerRect.top + (headerRect.height / 2) - this.phoneRoot.el.offsetTop

            const collapsedX = headerRect.right - 40 - (this.collapsedSize / 2)
            const collapsedY = headerRect.top + (headerRect.height / 2) - (this.collapsedSize / 2)

            this.phoneRoot.el.style.left = collapsedX + "px"
            this.phoneRoot.el.style.top = collapsedY + "px"
            this.phoneRoot.el.style.bottom = "auto"

            this.state.isCollapsed = true
        }
    }
}
