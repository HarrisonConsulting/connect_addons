/** @odoo-module **/
"use strict"
import {useBus, useService} from "@web/core/utils/hooks"
import {Calls} from "@connect/components/phone/calls/calls"
import {Favorites} from "@connect/components/phone/favorites/favorites"
import {LINE_UNAVAILABLE, PhoneLine} from "@connect/components/phone/phone/phone_line"
import {callUnconfirmedWords} from "@connect/components/phone/phone/transports/server_originate_transport"
import {LineSelector, howLineConnects, loadSelectedLine, saveSelectedLine} from "@connect/components/phone/line_selector/line_selector"
import {Contacts} from "@connect/components/phone/contacts/contacts"
import {dialTone, maskNumber, setFocus} from "@connect/js/utils"
import {Component, status, useState, useRef, useEffect, onWillStart, onMounted, onWillUnmount} from "@odoo/owl"
import {useDebounced} from "@web/core/utils/timing"
import {user} from "@web/core/user"
import {ConfirmationDialog} from "@web/core/confirmation_dialog/confirmation_dialog"
import {_t} from "@web/core/l10n/translation"
import {registry} from "@web/core/registry"

const uid = user.userId

// A failed presence update waits this long before its next try, doubling up to
// the cap. A server that keeps refusing must not be asked in a tight loop.
const PRESENCE_RETRY_BASE_MS = 1000
const PRESENCE_RETRY_MAX_MS = 60000

// What the From row and the To label add to the panel; phone.scss raises
// --phone-height by the same number under .o_phone_multi_line.
const LINE_ROW_HEIGHT = 86

// What another tab says about its call, which means nothing to a tab whose own
// call is on another line.
const LINE_BOUND_EVENTS = new Set(['tbcAnswerCall', 'tbcEndCall', 'tbcDtmf', 'tbcMicrophoneMute', 'tbcHold'])

// How long the panel keeps saying how a call on the person's own phone ended.
const CALL_ENDING_WORDS_MS = 10000

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
        lines: Array,
        browserSessionNonce: {type: String, optional: true},
        // What the server said while minting, when no line carries it as its reason.
        mintError: {type: [String, Boolean], optional: true},
    }

    static components = {Calls, Contacts, Favorites, LineSelector}

    constructor() {
        super(...arguments)
        this.bus = this.props.bus
        // Identifies this tab across every get_client_token() call it makes
        // (initial mount in main.js, plus PhoneLine's reconnect/updateToken), for
        // a provider whose credential is stateful per tab. Unused on the
        // Twilio path (JWTs are stateless — no cross-tab collision).
        this.browserSessionNonce = this.props.browserSessionNonce
        // Last-write-wins queue for connect.user.update_presence. The
        // method is async but every call site is fire-and-forget; concurrent
        // UPDATEs of the same connect_user row under REPEATABLE READ
        // serialisation-fail and occupy HTTP workers on retry.
        this._presenceDesired = null
        this._presenceSent = null
        this._presenceInFlight = false
        this._presenceFailures = 0
        this._presenceRetryTimer = null
        this.lines = this.props.lines.map((line, index) => new PhoneLine(this, {
            key: line.key,
            provider: line.provider,
            media: line.media,
            toolId: line.tool_id,
            mintToolId: line.mint_tool_id,
            unavailable: line.unavailable,
            capabilities: line.capabilities,
            label: line.label || _t("Line %s", index + 1),
            callerIds: line.caller_ids,
            tokenData: line.token_data,
        }))
        // Click-to-call reads these from the bus at the click, so they follow the
        // From selection.
        Object.defineProperties(this.bus, {
            clickToCallLocal: {configurable: true, get: () => this.clickToCallLocal},
            clickToCallToolId: {configurable: true, get: () => this.clickToCallToolId},
        })
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
            connectionError: '',  // Human-readable reason for the current error/offline status
            // What each line last reported, by key: {status, error}. The two above
            // are the line on the panel's.
            lineStatuses: {},
            lastOperationError: false,
            // Call quality metrics (from Twilio RTCStats samples)
            callQuality: 'unknown',  // 'excellent', 'good', 'fair', 'poor', 'unknown'
            callQualityMos: 0,
            callQualityJitter: 0,
            callQualityRtt: 0,
            callQualityPacketLoss: 0,
            callQualityWarnings: [],
            showQualityDetails: false,
            isAudioSettings: false,
            audioEnabled: localStorage.getItem('connect_audio_enabled') !== 'false',
            audioVolume: parseFloat(localStorage.getItem('connect_audio_volume') || '0.3'),
            isRecording: false,
            isPaused: false,
            isRecordingStopped: false,
            recordingLoading: false,
            // The line the next call goes out on. Only the person changes it.
            selectedLineKey: this._initialLineKey(),
            // The line with this tab's foreground call, or none.
            foregroundLineKey: null,
            // The line of a call another tab owns and this tab is showing.
            syncedLineKey: null,
            // The caller ID picked per line key.
            pickedCallerIds: {},
            // What the panel says about a call nothing in this browser can hear.
            callStateText: '',
        })
        // Phone dimensions for drag constraints (golden ratio)
        this.phoneWidth = 300
        this.phoneHeight = this.lines.length >= 2 ? 486 + LINE_ROW_HEIGHT : 486
        this.collapsedSize = 56
        // Offset from collapsed icon center to dialog top-left (for position restore)
        this.collapseOffsetX = this.phoneWidth - 40  // Approximate minimize button X offset
        this.collapseOffsetY = 18  // Header center Y offset
        this.callDuration = 0
        this.callDurationTimerInstance = null
        this.phoneInput = useRef('connect-phone-input')

        this.user = uid
        this.lastActiveTab = this.tabs.phone
        this.waitingSession = null  // Incoming call waiting while user is in active call
        this.heldSession = null
        this.heldCallSid = null
        this.heldCallId = null
        this.heldCallerId = null
        this.call_id = null
        this.call_sid = null  // Twilio CallSid for the current call
        this.recording_ref = null        // The provider's reference for the active recording
        this.recording_path = null       // Where the provider writes it, for those that need it to stop
        this._recordingControls = 0      // Presses so far, so an older state answer can be ignored
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
        this._callLineKey = null  // The line of the latest call, for what is said after it ends
        const post = this.bc.postMessage.bind(this.bc)
        this.bc.postMessage = (message) => post(this.isMultiLine ? {
            ...message,
            params: {...message.params, lineKey: this._callLineKey ?? this.activeLine.key},
        } : message)
        this.contactSearch = 'all'
        this.id = Math.floor(Math.random() * 1000000)
        this.windows = [this.id]
        this.sipSessions = []
        this.suppressBroadcastChannel = false
    }

    /**
     * The line the panel is showing: the one with the call on it, otherwise the
     * selected one. At most one line has a foreground call, because the session
     * setter below is the only writer. A call on another line shows that line
     * and leaves the selection alone, so the panel returns to it afterwards.
     */
    get activeLine() {
        const key = this.state.foregroundLineKey ?? this.state.syncedLineKey ?? this.state.selectedLineKey
        return this.lines.find((line) => line.key === key) || this.lines[0]
    }

    get isMultiLine() {
        return this.lines.length >= 2
    }

    /** The line the next call goes out on, whatever the panel is showing now. */
    get selectedLine() {
        return this.lines.find((line) => line.key === this.state.selectedLineKey) || this.lines[0]
    }

    /**
     * Whether a click-to-call goes through the panel's dial, on the selected line.
     * A bridge line and any selection but the first line always do, and so does a
     * first line that could not mint, which then refuses in words. With two or more
     * lines, so does any click the panel would refuse, because the server knows
     * nothing of the call that stands in its way. Otherwise it goes by the line's
     * first callflow: in this browser when that is the web client, and from the
     * server when it is not.
     */
    get clickToCallLocal() {
        const line = this.selectedLine
        if (line.media === 'bridge') {
            return true
        }
        if (this.isMultiLine && (line !== this.lines[0] || line.unavailable || this._isDialBusy() || this._dialRefusal)) {
            return true
        }
        return line.tokenData.first_callflow === 'client'
    }

    /** The tool a click-to-call the server places has to go out on, if it must name one. */
    get clickToCallToolId() {
        const line = this.selectedLine
        return (this.isMultiLine || line.mintToolId) && line.toolId || false
    }

    /** A line this browser chose earlier, if it is still one of this person's lines. */
    _initialLineKey() {
        const stored = this.isMultiLine ? loadSelectedLine() : null
        const line = this.lines.find((candidate) => candidate.key === stored) || this.lines[0]
        return line?.key
    }

    selectLine(key) {
        if (this.state.inCall || this.state.inIncoming || !this.lines.some((line) => line.key === key)) {
            return
        }
        this.state.selectedLineKey = key
        saveSelectedLine(key)
        this._showLineStatus()
    }

    callerIdOf(line) {
        return this.state.pickedCallerIds[line.key] || line.defaultCallerId
    }

    pickCallerId(number) {
        const line = this.activeLine
        if (!this.state.inCall && !this.state.inIncoming && line.callerIds.includes(number)) {
            this.state.pickedCallerIds[line.key] = number
        }
    }

    howLineConnects(line) {
        return howLineConnects(line)
    }

    /** Follows a call another tab owns, if that tab said which line it is on. */
    _followLine({lineKey} = {}) {
        if (lineKey !== undefined) {
            this.state.syncedLineKey = lineKey
            this._callLineKey = lineKey
            this._showLineStatus()
        }
    }

    get transport() {
        return this.activeLine.transport
    }

    get provider() {
        return this.activeLine.provider
    }

    // Optional preferences a provider may put on a line's credential. Absent means
    // the default, so a credential that has none keeps the phone as it always was.
    get call_popup_is_enabled() {
        return this.activeLine.tokenData.call_popup_is_enabled ?? false
    }

    get call_popup_is_sticky() {
        return this.activeLine.tokenData.call_popup_is_sticky ?? false
    }

    get phone_ring_volume() {
        return this.activeLine.tokenData.phone_ring_volume ?? 70
    }

    get isMaskCallNumber() {
        return this.activeLine.tokenData.mask_call_number ?? false
    }

    /** A phone number as the panel shows it. */
    displayNumber(phoneNumber) {
        return phoneNumber && this.isMaskCallNumber ? maskNumber(phoneNumber) : phoneNumber
    }

    get sipRegistered() {
        return this.lines.some((line) => line.registered)
    }

    /** The foreground call, whichever line it is on. */
    get session() {
        return this.lines.find((line) => line.session)?.session ?? null
    }

    /** Makes `call` the foreground call of the line that owns it, and of no other. */
    set session(call) {
        let foreground = null
        for (const line of this.lines) {
            line.session = line.owns(call) ? call : null
            foreground = line.session ? line : foreground
        }
        this.state.foregroundLineKey = foreground ? foreground.key : null
        if (foreground) {
            this._callLineKey = foreground.key
        }
        this._showLineStatus()
    }

    setup() {
        super.setup()
        this.orm = useService('orm')
        this.action = useService('action')
        this.notification = useService("notification")
        this.dialog = useService("dialog")
        this.audioNotification = this.env.services.connect_audio
        this.busService = useService('bus_service')

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

        // The systray button IS the phone's status light: it renders connecting /
        // offline / error / ringing / in-call, so the user never needs a toast to
        // learn the phone's state. Pushing that from an effect (rather than a
        // bus.trigger at each mutation site) means the tray cannot drift out of
        // sync -- every state change that matters re-runs this by construction.
        // Precedent for useEffect with an explicit dependency getter:
        // /mnt/19/odoo/addons/mail/static/src/core/common/message.js
        useEffect(
            () => this._syncTray(),
            () => [
                this.state.isDisplay,
                this.state.inCall,
                this.state.inIncoming,
                this.state.connectionStatus,
                this.state.connectionError,
                this.state.isActive,
            ],
        )

        useBus(this.bus, 'busPhoneMakeCall', ({detail}) => this.prepareCall(detail))
        useBus(this.bus, 'busPhoneMakeTransfer', ({detail}) => this._busPhoneMakeTransfer(detail))
        useBus(this.bus, 'busPhoneAddParticipant', ({detail}) => this._busPhoneAddParticipant(detail))
        useBus(this.bus, 'busPhoneToggleDisplay', ({detail}) => this._busPhoneToggleDisplay(detail))
        useBus(this.bus, 'busPhoneHangUp', ({detail}) => this._busPhoneHangUp(detail))
        useBus(this.bus, 'busPhoneReconnect', () => this._reconnect())
        useBus(this.bus, 'busPhoneCallState', ({detail}) => {
            for (const line of this.lines) {
                line.transport?.receiveCallState(detail)
            }
        })

        onWillStart(async () => {
            // Each line answers for its own: a failure here stops only that line.
            await Promise.all(this.lines.map((line) => line.loadDependencies()))

            this._beforeUnloadHandler = (event) => {
                if (this.session) {
                    event = event || window.event
                    const message = "You're in call! Are you sure you want to close?"
                    if (event) {
                        event.returnValue = message
                    }
                    return message
                }
            }
            window.addEventListener("beforeunload", this._beforeUnloadHandler)

            this._unloadHandler = () => {
                // The page is going away, so every live dialog has to end.
                // An unanswered incoming dialog is declined; a waiting dialog
                // is declined on its own and is not promoted into the panel.
                this._unloading = true
                this._stopIncomingRing()
                this._updatePresence('offline')
                const waiting = this.waitingSession
                if (waiting) {
                    this.waitingSession = null
                    try {
                        waiting.reject()
                    } catch (e) {
                        console.warn('Connect: reject waiting call on unload failed:', e)
                    }
                }
                if (this.session) {
                    const params = {id: this.id, action: 'pop'}
                    this.bc.postMessage({event: 'tbcSipSession', params})
                    this.bc.postMessage({event: "tbcCloseTab", params: {id: this.id}})
                    // A call on the person's own phone does not need this tab.
                    if (this.lines.find((line) => line.owns(this.session))?.needsBrowserMedia) {
                        this.session.disconnect()
                    }
                }
            }
            window.addEventListener("unload", this._unloadHandler)
        })

        onMounted(() => {
            // Suppress benign unhandled rejections from the vendored Twilio SDK:
            //   - AbortError: audio play/pause race conditions
            //   - InvalidArgumentError "Device not found": AudioHelper._updateDevices()
            //     fires setTimeout(_setInputDevice('default')) with no .catch() when a
            //     devicechange leaves no 'default' input device enumerated.
            this._twilioRejectionHandler = (event) => {
                const error = event.reason
                if (error instanceof DOMException && error.name === 'AbortError') {
                    event.preventDefault()
                    return
                }
                if (error?.name === 'InvalidArgumentError'
                        && error.message?.includes('Device not found')) {
                    event.preventDefault()
                }
            }
            window.addEventListener('unhandledrejection', this._twilioRejectionHandler)

            // Setup audio unlock handler for browser autoplay restrictions
            setupAudioUnlock()

            // Subscribe to bus events for park slot notifications
            this._reloadViewHandler = (payload) => {
                if (payload && payload.model === "connect.park_slot") {
                    this.audioNotification.play("park")
                }
            }
            this.busService.subscribe("reload_view", this._reloadViewHandler)

            // When an auto-recording starts (record-from-answer-dual via TwiML), Twilio
            // sends an in-progress callback that we relay here. Refresh state immediately
            // so the UI shows "Recording" without waiting for retry polling to succeed.
            this._recordingStartedHandler = () => {
                if (this.call_sid) {
                    this._fetchRecordingState()
                }
            }
            this.busService.subscribe("recording_started", this._recordingStartedHandler)

            // Handle tab visibility changes: when user returns to an idle tab,
            // the browser may have closed IDB connections and Twilio websocket.
            // Re-initialize the device if needed.
            this._visibilityHandler = () => {
                if (!document.hidden && this.state.isActive) {
                    for (const line of this.lines) {
                        line.resume()
                    }
                }
            }
            document.addEventListener('visibilitychange', this._visibilityHandler)

            for (const line of this.lines) {
                line.initTransport()
            }
            this._sayWhatTheMountFound()

            // Proactive token refresh every 50 minutes (token TTL is 3600s/60min).
            // Prevents silent expiry on idle tabs that miss error-driven refresh.
            this._tokenRefreshInterval = setInterval(() => {
                if (this.state.isActive) {
                    for (const line of this.lines) {
                        // updateToken() itself skips a line with no live transport,
                        // or one that is already connecting.
                        line.updateToken()
                    }
                }
            }, 50 * 60 * 1000)

            // Network connectivity monitoring
            this._onlineHandler = () => {
                if (Object.values(this.state.lineStatuses).some(({status}) => status === 'offline')) {
                    this._reconnect()
                }
            }
            this._offlineHandler = () => {
                for (const line of this.lines) {
                    this._setLineStatus(line, 'offline', 'This device has no network connection.')
                }
            }
            window.addEventListener('online', this._onlineHandler)
            window.addEventListener('offline', this._offlineHandler)

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
                const own = self.state.foregroundLineKey
                if (LINE_BOUND_EVENTS.has(event) && params?.lineKey !== undefined && own !== null && params.lineKey !== own) {
                    return
                }
                const localStartCall = () => {
                    if (self.session) return
                    self._followLine(params)
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
                        if (self.id === self.windows.at(-1)) {
                            for (const line of self.lines) {
                                if (line.transport && line.transport.state === 'unregistered') {
                                    line.transport.register().catch((e) => {
                                        console.warn('Connect: Re-registration on tab close failed:', e?.message || e)
                                    })
                                }
                            }
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
                        // Call.mute(bool) is the only mute primitive the transport interface
                        // exposes; Twilio's Call never defines .unmute(), so calling it throws.
                        self.session.mute(params.mute === true)
                    }
                    self.state.isMicrophoneMute = params.mute
                } else if (event === 'tbcSoundMute') {
                    self.state.isSoundMute = params.mute
                    self.setIncomingVolume()
                    self._syncIncomingRing()
                } else if (event === 'tbcSync') {
                    if (self.state.inCall === false) {
                        self._followLine(params)
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
            this._stopIncomingRing()
            window.removeEventListener("beforeunload", this._beforeUnloadHandler)
            window.removeEventListener("unload", this._unloadHandler)
            if (this._reloadViewHandler) {
                this.busService.unsubscribe("reload_view", this._reloadViewHandler)
            }
            if (this._recordingStartedHandler) {
                this.busService.unsubscribe("recording_started", this._recordingStartedHandler)
            }
            if (this._twilioRejectionHandler) {
                window.removeEventListener('unhandledrejection', this._twilioRejectionHandler)
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
            if (this._onlineHandler) {
                window.removeEventListener('online', this._onlineHandler)
            }
            if (this._offlineHandler) {
                window.removeEventListener('offline', this._offlineHandler)
            }
            if (this._errorTimeout) {
                clearTimeout(this._errorTimeout)
            }
            clearTimeout(this._presenceRetryTimer)
            this._presenceRetryTimer = null
            if (this._tokenRefreshInterval) {
                clearInterval(this._tokenRefreshInterval)
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
        const confirmed = await new Promise(resolve => {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Blind Transfer"),
                body: _t("This will immediately transfer the call. The caller cannot be retrieved. Continue?"),
                confirm: () => resolve(true),
                cancel: () => resolve(false),
            })
        })
        if (!confirmed) return
        const phoneNumber = this.state.pendingTransferNumber
        this.state.showTransferChoice = false
        this.state.pendingTransferNumber = ''
        const call = this.session
        const callSid = call?.params?.CallSid || this.call_sid
        // The call can end while the question is open, which clears the number.
        if (!callSid || !phoneNumber) {
            this._transferFailed({}, _t("That call has already ended, so there is nothing to transfer."))
            return
        }
        try {
            // A refusal arrives as an error. An answer is the provider having
            // moved the other party, and it need not carry a success key.
            const result = await this.orm.call('connect.channel', 'forward_softphone_call', [
                this._callControlPayload(callSid, {number: phoneNumber})])
            if (result?.success === false) {
                this._transferFailed(result)
                return
            }
        } catch (error) {
            console.warn('Connect: transfer failed:', error)
            this._transferFailed(error)
            return
        }
        this.notify(_t("Transferring the call to %s.", phoneNumber), {sticky: false, type: 'success'})
        this.audioNotification.play('transfer_complete')
        this.bc.postMessage({event: "tbcTransfer", params: {phoneNumber}})
        // If the leg dropped during the wait, the panel may be on another call by now.
        if (this.session === call) {
            this.endCall()
        }
    }

    /** The call stays up, and the person is told it did not move. */
    _transferFailed(error, fallback = _t("The call could not be transferred. You are still on it.")) {
        this.state.xTransferTo = ''
        this._callControlFailed(error, fallback)
        this.audioNotification.play('transfer_failed')
    }

    async _onClickAttendedTransfer() {
        const phoneNumber = this.state.pendingTransferNumber
        this.state.showTransferChoice = false
        this.state.pendingTransferNumber = ''
        const callSid = this.session?.params?.CallSid || this.call_sid
        if (!callSid) {
            this.notify('No active call', {type: 'warning'})
            return
        }
        try {
            const result = await this.orm.call('connect.channel', 'attended_transfer_softphone_call', [
                this._callControlPayload(callSid, {step: 'start'})])
            if (result.success) {
                this.state.isAttendedTransfer = true
                this.state.isOnHold = true
                this.state.xTransferTo = phoneNumber
                this.state.xTransferInfo = 'Caller on hold — dial ' + phoneNumber + ' to consult'
                this.notify('Caller on hold. Dial the consult target.', {type: 'info'})
            } else {
                this._callControlFailed(result, 'Failed to hold caller')
            }
        } catch (e) {
            console.error('Attended transfer error:', e)
            this._callControlFailed(e, 'Failed to initiate attended transfer')
        }
    }

    async _onClickCompleteTransfer() {
        const callSid = this.session?.params?.CallSid || this.call_sid
        const consultTarget = this.state.xTransferTo
        if (!callSid || !consultTarget) {
            this.notify('Missing call or transfer target', {type: 'warning'})
            return
        }
        try {
            const result = await this.orm.call('connect.channel', 'attended_transfer_softphone_call', [
                this._callControlPayload(callSid, {step: 'complete', number: consultTarget})])
            if (result.success) {
                this.notify('Transfer completed', {type: 'success'})
                this.audioNotification.play('transfer_complete')
                this.bc.postMessage({event: "tbcTransfer", params: {phoneNumber: consultTarget}})
                this.endCall()
            } else {
                this._callControlFailed(result, 'Transfer completion failed')
                this.audioNotification.play('transfer_failed')
            }
        } catch (e) {
            console.error('Complete transfer error:', e)
            this._callControlFailed(e, 'Transfer completion failed')
            this.audioNotification.play('transfer_failed')
        }
    }

    async _onClickCancelTransfer() {
        const callSid = this.session?.params?.CallSid || this.call_sid
        if (!callSid) return
        try {
            const result = await this.orm.call('connect.channel', 'attended_transfer_softphone_call', [
                this._callControlPayload(callSid, {step: 'cancel'})])
            if (result.success) {
                this.state.isAttendedTransfer = false
                this.state.isOnHold = false
                this.state.xTransferTo = ''
                this.state.xTransferInfo = ''
                this.notify('Transfer cancelled, caller resumed', {type: 'info'})
            } else {
                this._callControlFailed(result, 'Cancel failed')
            }
        } catch (e) {
            console.error('Cancel transfer error:', e)
            this._callControlFailed(e, 'Cancel transfer failed')
        }
    }

    async _busPhoneAddParticipant({phoneNumber} = {}) {
        const callSid = this.session?.params?.CallSid || this.call_sid
        if (!callSid) {
            this.notify('No active call', {type: 'warning'})
            return
        }
        try {
            const result = await this.orm.call('connect.channel', 'add_softphone_participant', [
                this._callControlPayload(callSid, {number: phoneNumber})])
            if (result.success) {
                this.notify('Adding participant...', {type: 'info'})
            } else {
                this._callControlFailed(result, 'Failed to add participant')
            }
        } catch (e) {
            console.error('Add participant error:', e)
            this._callControlFailed(e, 'Failed to add participant')
        }
        this.state.isAddParticipant = false
        this.state.isContacts = false
        this.state.isDialingPanel = true
    }

    async _reconnect() {
        await Promise.all(this.lines.map((line) => line.reconnect()))
    }

    /**
     * Single writer for the connection lifecycle. Connectivity is ambient state,
     * not an event: it is rendered continuously by the systray button and the
     * in-panel banner rather than announced by toasts, which pile up during a
     * flapping network and obscure the very UI the user needs in order to click
     * Retry.
     */
    _setConnectionStatus(status, error = '') {
        this.state.connectionStatus = status
        this.state.connectionError = status === 'ready' ? '' : error
    }

    /** What a line reports. The banner and the tray speak for the line on the panel. */
    _setLineStatus(line, status, error = '') {
        this.state.lineStatuses[line.key] = {status, error}
        if (line === this.activeLine) {
            this._setConnectionStatus(status, error)
        }
    }

    /** The panel moved to another line, so it speaks for that one now. */
    _showLineStatus() {
        const reported = this.state.lineStatuses[this.activeLine.key]
        if (reported) {
            this._setConnectionStatus(reported.status, reported.error)
        }
    }

    /** What the From row says about each line that is down, by key. */
    get lineProblems() {
        const problems = {}
        for (const [key, {status, error}] of Object.entries(this.state.lineStatuses)) {
            if (status === 'error' || status === 'offline') {
                problems[key] = error || _t("This line is not connected.")
            }
        }
        return problems
    }

    _refreshIsActive() {
        this.state.isActive = this.lines.some((line) => !line.startFailed)
    }

    /**
     * Stays until closed, not on the usual timer: the panel may not be open yet.
     * A line that could not mint is not said here, because it says so itself.
     */
    _sayWhatTheMountFound() {
        const stored = this.isMultiLine ? loadSelectedLine() : null
        const mintError = this.props.mintError
        if (stored && !this.lines.some((line) => line.key === stored)) {
            this.state.lastOperationError = _t("The line you chose is no longer one of your lines. Calls now go from %s.", this.selectedLine.label)
        } else if (mintError && !this.lines.some((line) => line.unavailable === mintError)) {
            this.state.lastOperationError = mintError
        }
    }

    /** Push the phone's user-visible state to the systray button. */
    _syncTray() {
        this.bus.trigger('busTraySetState', {
            isDisplay: this.state.isDisplay,
            inCall: this.state.inCall,
            inIncoming: this.state.inIncoming,
            connectionStatus: this.state.connectionStatus,
            connectionError: this.state.connectionError,
            isActive: this.state.isActive,
        })
    }

    _onClickReconnect() {
        this._reconnect()
    }

    _setOperationError(message) {
        this.state.lastOperationError = message
        if (this._errorTimeout) clearTimeout(this._errorTimeout)
        this._errorTimeout = setTimeout(() => { this.state.lastOperationError = false }, 10000)
    }

    /**
     * Whether the line on the panel can do `action`. Mute and dtmf go by whether
     * its calls have browser media, everything else by its capabilities.
     */
    can(action) {
        const line = this.activeLine
        if (action === 'mute' || action === 'dtmf') {
            return line.needsBrowserMedia
        }
        return !!line.capabilities[action]
    }

    /** A caller put on hold has to be able to come back. */
    get canHold() {
        return this.can('hold') && this.can('resume')
    }

    /** An attended transfer that starts has to be able to finish or back out. */
    get canAttendedTransfer() {
        return this.can('attended_start') && this.can('attended_complete') && this.can('attended_cancel')
    }

    /** Control goes to the provider of the line the call is on, not the one showing. */
    _callControlPayload(callSid, extra = {}) {
        const call = [this.session, this.heldSession].find((candidate) => candidate?.params?.CallSid === callSid)
        const line = this.lines.find((candidate) => candidate.owns(call)) || this.activeLine
        return {provider: line.provider, channel_sid: callSid, ...extra}
    }

    /**
     * Surface a call-control failure in the server's words. A UserError
     * arrives as an RPC error carrying data.message; a {success: false}
     * result carries error. The fallback is for errors with neither.
     */
    _callControlFailed(error, fallback) {
        const message = error?.data?.message ?? error?.error ?? error?.message ?? fallback
        this._setOperationError(message)
        this.notify(message, {type: 'warning'})
    }

    /**
     * A call nobody can confirm may still ring the person's own phone, and that is
     * the phone any other line would ring too.
     */
    get _dialRefusal() {
        return this.lines.map((line) => line.transport?.dialRefusal).find(Boolean) || null
    }

    /** Whether another press has to wait: one call goes out at a time, across all lines. */
    _isDialBusy() {
        return this.state.inCall || this._preparingCall
    }

    /**
     * The second press of the same number is the same press. A different one was
     * meant, and with two lines may have been meant for another line, so it hears
     * why nothing happened.
     */
    _refuseBusyDial(props) {
        if (this.isMultiLine && props.phone !== this.state.callPhoneNumber) {
            this._refuseDial(_t("A call is already going out. End it before you place another."))
        }
    }

    /** Says why nothing was dialed, where the person can see it. */
    _refuseDial(words) {
        this._setOperationError(words)
        this.state.isDisplay = true
        this.state.isCollapsed = false
    }

    async prepareCall(props) {
        if (this._isDialBusy()) {
            this._refuseBusyDial(props)
            return
        }
        this._preparingCall = true
        try {
            this.state.isContactList = false
            this.state.callPhoneNumber = props.phone
            await this.searchPartner(props.phone)
        } finally {
            this._preparingCall = false
        }
        // A call may have rung, here or on another line, during that lookup.
        if (this.state.inCall) {
            this._refuseBusyDial(props)
            return
        }
        this.makeCall(props)
    }

    async setCallStatus(status) {
        const currentCallStatus = this.callStatus[status] ? this.callStatus[status] : this.callStatus.Failed
        this.notify(currentCallStatus.toUpperCase(), {sticky: false})
    }

    setIncomingVolume() {
        for (const line of this.lines) {
            line.setIncomingVolume()
        }
    }

    /**
     * Attach call quality monitoring to a Call. Listens for the transport's
     * normalized 'quality' event (Twilio-only today — see TwilioCall, which
     * maps its 'sample' (every ~1s with RTCStats)/'warning'/'warning-cleared'
     * events into this shape; a transport that can't measure quality simply
     * never emits it). Only updates state when quality level changes or
     * metrics shift meaningfully to avoid unnecessary re-renders.
     */
    _attachQualityMonitor(call) {
        const self = this

        call.on('quality', (evt) => {
            if (evt.kind === 'sample') {
                const sample = evt.sample
                const mos = sample.mos
                let quality = 'unknown'
                if (mos >= 4.2) quality = 'excellent'
                else if (mos >= 3.8) quality = 'good'
                else if (mos >= 3.2) quality = 'fair'
                else if (mos > 0) quality = 'poor'

                const jitter = sample.jitter ? Math.round(sample.jitter) : 0
                const rtt = sample.rtt ? Math.round(sample.rtt) : 0
                let packetLoss = 0
                if (sample.packetsReceived > 0) {
                    packetLoss = parseFloat(((sample.packetsLost / (sample.packetsReceived + sample.packetsLost)) * 100).toFixed(1))
                }

                // Only update state if quality level changed or metrics shifted meaningfully (>10%)
                const qualityChanged = quality !== self.state.callQuality
                const metricsChanged = (
                    Math.abs(jitter - self.state.callQualityJitter) > Math.max(1, self.state.callQualityJitter * 0.1) ||
                    Math.abs(rtt - self.state.callQualityRtt) > Math.max(1, self.state.callQualityRtt * 0.1) ||
                    Math.abs(packetLoss - self.state.callQualityPacketLoss) > 0.5
                )

                if (qualityChanged || metricsChanged) {
                    self.state.callQuality = quality
                    self.state.callQualityMos = mos ? mos.toFixed(1) : '—'
                    self.state.callQualityJitter = jitter
                    self.state.callQualityRtt = rtt
                    self.state.callQualityPacketLoss = packetLoss
                }
            } else if (evt.kind === 'warning') {
                if (!self.state.callQualityWarnings.includes(evt.name)) {
                    self.state.callQualityWarnings = [...self.state.callQualityWarnings, evt.name]
                }
            } else if (evt.kind === 'warning-cleared') {
                self.state.callQualityWarnings = self.state.callQualityWarnings.filter(w => w !== evt.name)
            }
        })
    }

    /**
     * Reset all call quality state to defaults.
     */
    _resetCallQuality() {
        this.state.callQuality = 'unknown'
        this.state.callQualityMos = 0
        this.state.callQualityJitter = 0
        this.state.callQualityRtt = 0
        this.state.callQualityPacketLoss = 0
        this.state.callQualityWarnings = []
        this.state.showQualityDetails = false
    }

    _onClickQualityIndicator() {
        this.state.showQualityDetails = !this.state.showQualityDetails
    }

    _updatePresence(presence) {
        this._presenceDesired = presence
        if (this._presenceInFlight || this._presenceRetryTimer) {
            return
        }
        this._flushPresence()
    }

    async _flushPresence() {
        const presence = this._presenceDesired
        if (presence === null || presence === this._presenceSent) {
            return
        }
        this._presenceInFlight = true
        let retryDelay = 0
        try {
            await this.orm.call('connect.user', 'update_presence', [presence])
            this._presenceSent = presence
            this._presenceFailures = 0
        } catch (e) {
            // Don't let presence failures affect call operations
            console.warn('Connect: Presence update failed:', e)
            retryDelay = Math.min(PRESENCE_RETRY_BASE_MS * 2 ** this._presenceFailures, PRESENCE_RETRY_MAX_MS)
            this._presenceFailures++
        } finally {
            this._presenceInFlight = false
            if (this._presenceDesired !== this._presenceSent) {
                if (!retryDelay) {
                    this._flushPresence()
                } else if (status(this) !== 'destroyed') {
                    this._presenceRetryTimer = setTimeout(() => {
                        this._presenceRetryTimer = null
                        this._flushPresence()
                    }, retryDelay)
                }
            }
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

    /**
     * The line a dial goes out on. A call that names a line gets that line or
     * none, never another in its place. One line has nowhere else to go.
     */
    _lineToDial(lineKey) {
        if (!this.isMultiLine || !lineKey) {
            return this.activeLine
        }
        return this.lines.find((line) => line.key === lineKey) || null
    }

    async makeCall(props) {
        const self = this
        let phoneNumber = props.phone
        const line = self._lineToDial(props.lineKey)
        if (!line) {
            self._refuseDial(_t("The line this call was on is no longer one of your lines, so nothing was dialed. Choose a line and dial from the keypad."))
            return
        }
        if (!line.provider) {
            self._refuseDial(LINE_UNAVAILABLE)
            return
        }
        // Never another line instead: the person chose this one.
        if (line.unavailable) {
            self._refuseDial(self.isMultiLine
                ? _t("%s is not available: %s Choose another line, or try again.", line.label, line.unavailable)
                : line.unavailable)
            return
        }
        if (self.isMultiLine && !line.hasLiveTransport) {
            self._refuseDial(_t("%s is not connected. Choose another line, or try again.", line.label))
            return
        }
        // A second press of the call already going out is the same press.
        if (line.transport?.liveCall) {
            return
        }
        const refusal = self._dialRefusal
        if (refusal) {
            self._refuseDial(refusal)
            return
        }
        self._callLineKey = line.key
        // The panel shows the line being dialed; the selection stays as it is.
        if (line !== self.activeLine) {
            self.state.foregroundLineKey = line.key
            self._showLineStatus()
        }
        self.startCall()

        const syncParams = self.getJsonCallData()
        self.bc.postMessage({event: "tbcSync", params: syncParams})

        const params = {
            To: phoneNumber,
            Called: phoneNumber,
        }
        if (self.isMultiLine && line.callerIds.length) {
            params.CallerId = self.callerIdOf(line)
        }

        try {
            self.session = await line.dial(params)
        } catch (error) {
            // The session setter never ran, so a one-time line has to be let go here.
            if (self.state.foregroundLineKey === line.key && !line.session) {
                self.state.foregroundLineKey = null
                self._showLineStatus()
            }
            if (!self.isMultiLine) {
                throw error
            }
            console.warn('Connect: The dial failed:', error?.message || error)
            await self.endCall()
            self.bc.postMessage({event: "tbcEndCall"})
            self._refuseDial(_t("%s could not place the call: %s", line.label, error?.message || _t("no reason given.")))
            return
        }
        const call = self.session

        self._saidCall = call
        self._sayCallState('')
        call.on("callState", (report) => {
            if (self._saidCall === call) {
                self._onCallState(report, phoneNumber)
            } else if (report.state === 'unknown') {
                // An earlier call went unconfirmed after the panel moved on from it.
                self._setOperationError(callUnconfirmedWords())
            }
        })
        call.on("accept", async function () {
            if (self.session !== call) return
            self.call_sid = call.params.CallSid || null
            self.createCallCounter(phoneNumber, call.elapsedSeconds)
            self.state.phone_status = self.status.accepted
            self._updatePresence('on_call')
            self._attachQualityMonitor(call)
            self.audioNotification.play('connected')
            await self.setCallStatus("Answered")
            const params = self.getJsonCallData()
            self.bc.postMessage({event: "tbcAnswerCall", params})
            self._fetchRecordingState()
        })
        call.on("disconnect", function () {
            if (self.heldSession === call) {
                self._releaseHeldCall()
                return
            }
            if (self.session !== call) return
            if (!self._claimCallEnd(call)) return
            const wasAnswered = self.state.phone_status === self.status.accepted
            ;(async () => {
                self.state.phone_status = self.status.ended
                await self.setCallStatus(wasAnswered ? "Terminated" : "Canceled")
                await self.endCall()
                if (self.session === call) self.session = null
                if (self.suppressBroadcastChannel) {
                    self.suppressBroadcastChannel = false
                } else {
                    self.bc.postMessage({event: "tbcEndCall"})
                }
            })()
        })
        call.on("cancel", function () {
            if (self.heldSession === call) {
                self._releaseHeldCall()
                return
            }
            if (self.session !== call) return
            if (!self._claimCallEnd(call)) return
            ;(async () => {
                self.state.phone_status = self.status.ended
                await self.setCallStatus("Canceled")
                await self.endCall()
                if (self.session === call) self.session = null
                if (self.suppressBroadcastChannel) {
                    self.suppressBroadcastChannel = false
                } else {
                    self.bc.postMessage({event: "tbcEndCall"})
                }
            })()
        })
        call.on("reject", function () {
            if (self.heldSession === call) {
                self._releaseHeldCall()
                return
            }
            if (self.session !== call) return
            if (!self._claimCallEnd(call)) return
            ;(async () => {
                self.state.phone_status = self.status.ended
                await self.setCallStatus("Rejected")
                await self.endCall()
                if (self.session === call) self.session = null
                if (self.suppressBroadcastChannel) {
                    self.suppressBroadcastChannel = false
                } else {
                    self.bc.postMessage({event: "tbcEndCall"})
                }
            })()
        })
    }

    _sayCallState(text, clearAfterMs = 0) {
        clearTimeout(this._callStateTimer)
        this.state.callStateText = text
        if (clearAfterMs) {
            this._callStateTimer = setTimeout(() => { this.state.callStateText = '' }, clearAfterMs)
        }
    }

    _onCallState({state, reason_code, detail}, phoneNumber) {
        const isEnd = state === 'ended' || state === 'failed'
        this._sayCallState(this._callStateWords(state, reason_code, detail, phoneNumber), isEnd ? CALL_ENDING_WORDS_MS : 0)
    }

    /** In words. The codes are neutral, so no carrier's vocabulary reaches the panel. */
    _callStateWords(state, reasonCode, detail, phoneNumber) {
        if (state === 'calling') {
            return _t("Calling your phone")
        }
        if (state === 'first_leg_answered' || state === 'connecting') {
            return _t("You picked up, connecting %s", this.displayNumber(phoneNumber))
        }
        if (state === 'in_call') {
            return reasonCode === 'far_end_unknown' ? _t("Connected from your phone") : _t("In call")
        }
        if (state === 'unknown') {
            return callUnconfirmedWords()
        }
        if (detail) {
            return detail
        }
        switch (reasonCode) {
            case 'no_answer':
                return _t("They did not answer")
            case 'busy':
                return _t("Line busy")
            case 'own_phone_no_answer':
                return _t("Your phone did not answer")
            case 'own_phone_may_ring':
                return _t("Your phone may ring until it times out. Answering will not connect.")
            case 'unconfirmed':
                return ''
        }
        return state === 'failed' ? _t("The call could not be placed.") : _t("Call ended")
    }

    /** A call on the person's own phone, up: nothing in this browser can touch its audio. */
    get isOnOwnPhone() {
        return this.state.phone_status === this.status.accepted && !this.can('mute')
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
        // Ensure phone is visible within viewport (defer until DOM updates)
        requestAnimationFrame(() => this._ensureWithinViewport())
    }

    _claimCallEnd(call) {
        if (!call) return true
        if (call._connectSettled) return false
        call._connectSettled = true
        return true
    }

    _ringsInApp() {
        // A call waiting behind the panel's call can be on another line.
        const waitingLine = !this.state.inIncoming && this.lines.find((line) => line.owns(this.waitingSession))
        const provider = (waitingLine || this.activeLine).provider
        const TransportClass = registry.category("connect.telephony_transports").get(provider, null)
        return !!(TransportClass && TransportClass.ringsInApp)
    }

    _stopIncomingRing() {
        if (this._incomingRingTimer) {
            clearInterval(this._incomingRingTimer)
            this._incomingRingTimer = null
        }
    }

    _syncIncomingRing() {
        const should = this._ringsInApp()
            && !this.state.isSoundMute
            && (this.state.inIncoming || this.state.hasWaitingCall)
        if (!should) {
            this._stopIncomingRing()
            return
        }
        if (this._incomingRingTimer) return
        const ring = () => {
            if (this.state.isSoundMute || (!this.state.inIncoming && !this.state.hasWaitingCall)) {
                this._stopIncomingRing()
                return
            }
            this.audioNotification.play('incoming')
        }
        ring()
        this._incomingRingTimer = setInterval(ring, 3000)
    }

    _attachIncomingCall(call, phoneNumber) {
        const self = this
        call.on("accept", async function () {
            if (self.session !== call) return
            self._stopIncomingRing()
            self.call_sid = call.params.CallSid || null
            self.createCallCounter(phoneNumber)
            self.state.phone_status = self.status.accepted
            self._updatePresence('on_call')
            self._attachQualityMonitor(call)
            await self.setCallStatus("Answered")
            self._fetchRecordingState()
        })
        call.on("disconnect", function () {
            if (self.heldSession === call) {
                self._releaseHeldCall()
                return
            }
            if (self.session !== call) return
            if (!self._claimCallEnd(call)) return
            const wasAnswered = self.state.phone_status === self.status.accepted
            ;(async () => {
                self._stopIncomingRing()
                self.state.phone_status = self.status.ended
                await self.setCallStatus(wasAnswered ? "Terminated" : "Canceled")
                await self.endCall()
                if (self.session === call) self.session = null
                if (self.suppressBroadcastChannel) {
                    self.suppressBroadcastChannel = false
                } else {
                    self.bc.postMessage({event: "tbcEndCall"})
                }
            })()
        })
        call.on("cancel", function () {
            if (self.heldSession === call) {
                self._releaseHeldCall()
                return
            }
            if (self.session !== call) return
            if (!self._claimCallEnd(call)) return
            ;(async () => {
                self._stopIncomingRing()
                self.state.phone_status = self.status.ended
                await self.setCallStatus("Canceled")
                const index = self.sipSessions.indexOf(self.id)
                if (index > -1) self.sipSessions.splice(index, 1)
                self.bc.postMessage({event: 'tbcSipSession', params: {id: self.id, action: 'pop'}})
                if (self.session === call) self.session = null
                await self.endCall()
            })()
        })
        call.on("reject", function () {
            if (self.heldSession === call) {
                self._releaseHeldCall()
                return
            }
            if (self.session !== call) return
            if (!self._claimCallEnd(call)) return
            ;(async () => {
                self._stopIncomingRing()
                self.state.phone_status = self.status.ended
                await self.setCallStatus("Rejected")
                const index = self.sipSessions.indexOf(self.id)
                if (index > -1) self.sipSessions.splice(index, 1)
                self.bc.postMessage({event: 'tbcSipSession', params: {id: self.id, action: 'pop'}})
                if (self.session === call) self.session = null
                await self.endCall()
            })()
        })
    }

    _releaseHeldCall() {
        this.heldSession = null
        this.heldCallSid = null
        this.heldCallId = null
        this.heldCallerId = null
        this.state.isOnHold = false
        this.notify('Held call ended', {type: 'info'})
        this.bc.postMessage({event: "tbcHold", params: {isOnHold: false}})
    }

    async _promoteWaitingCall(call) {
        const info = {...(this.state.waitingCallerId || {})}
        this.waitingSession = null
        this.state.hasWaitingCall = false
        this.state.waitingCallerId = {}
        this.session = call
        this.sipSessions = [this.id]
        const phoneNumber = info.phoneNumber || (call.params && call.params.From) || ''
        this.state.callPhoneNumber = phoneNumber
        this.state.phone_status = this.status.incoming
        this.state.inIncoming = true
        if (info.partnerId) {
            this.state.isPartner = true
            this.state.callerId = {
                partnerId: parseInt(info.partnerId),
                partnerName: info.partnerName,
                partnerIconUrl: this.computePartnerIconUrl(info.partnerId),
                partnerUrl: this.computePartnerUrl(info.partnerId),
                phoneNumber,
            }
        } else {
            this.state.isPartner = false
            this.state.callerId = {phoneNumber}
            this.searchPartner(phoneNumber)
        }
        this.startCall()
        this._attachIncomingCall(call, phoneNumber)
        this._syncIncomingRing()
        this.bc.postMessage({event: "tbcWaitingCall", params: {hasWaitingCall: false, waitingCallerId: {}}})
        this.bc.postMessage({event: "tbcStartCall", params: this.getJsonCallData()})
        this.notify('Incoming call from ' + (info.partnerName || phoneNumber), {type: 'info'})
    }

    async endCall() {
        if (this._endingCall) return
        this._endingCall = true
        const waiting = this._unloading ? null : this.waitingSession
        try {
        this._stopIncomingRing()
        this._updatePresence(this.sipRegistered ? 'available' : 'offline')
        this.call_sid = null
        this.recording_ref = null
        this.recording_path = null
        this.state.isRecording = false
        this.state.isPaused = false
        this.state.isRecordingStopped = false
        this.state.recordingLoading = false
        this._resetCallQuality()
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
        // lastOperationError is left alone: a hold that fails as the agent
        // leg drops lands right here, and the banner is the only place the
        // user learns the customer is still waiting. _setOperationError
        // clears it on its own timer.
        this.state.callerId = {}
        this.state.phoneNumber = ''
        this.state.xPhoneInfoDisplay = ''
        this.phoneInput.el.value = this.state.phoneNumber
        this.state.activeTab = this.lastActiveTab
        if (this.lastActiveTab === this.tabs.calls) {
            this.getCalls()
        }
        const self = this
        setTimeout(() => {
            if (!self.state.inIncoming) {
                self.state.inCall = false
            }
        }, 100)

        this.destroyCallCounter()
        this.sipSessions = []
        this.state.xTransferTo = ''
        this.state.xTransferInfo = ''
        this.state.xTransferPartner = false
        const promote = waiting && !this._unloading
        if (!promote) {
            this.waitingSession = null
            this.state.hasWaitingCall = false
            this.state.waitingCallerId = {}
        }
        this.heldSession = null
        this.heldCallSid = null
        this.heldCallId = null
        this.heldCallerId = null
        this.state.syncedLineKey = null
        this._showLineStatus()
        if (promote) {
            this._promoteWaitingCall(waiting)
        }
        } finally {
            this._endingCall = false
        }
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

    createCallCounter(phoneNumber, elapsedSeconds = 0) {
        const self = this
        self.callDuration = elapsedSeconds
        self.state.callDurationTime = new Date(elapsedSeconds * 1000).toISOString().substring(11, 19)
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
        this.state.isAudioSettings = false
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
        this.state.isAudioSettings = false
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
        this.state.isAudioSettings = false
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
        this.state.isAudioSettings = false
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
        const callSid = this.session?.params?.CallSid || this.call_sid
        if (!callSid) {
            this.state.holdInProgress = false
            this.notify('No active call', {type: 'warning'})
            return
        }
        try {
            if (this.state.isOnHold) {
                const result = await this.orm.call('connect.channel', 'resume_softphone_call', [
                    this._callControlPayload(callSid)])
                if (result.success) {
                    this.state.isOnHold = false
                    this._updatePresence('on_call')
                    this.notify('Call resumed', {type: 'info'})
                    this.audioNotification.play('unpark')
                    this.bc.postMessage({event: "tbcHold", params: {isOnHold: false}})
                } else {
                    this._callControlFailed(result, 'Resume failed')
                }
            } else {
                const result = await this.orm.call('connect.channel', 'hold_softphone_call', [
                    this._callControlPayload(callSid)])
                if (result.success) {
                    this.state.isOnHold = true
                    this._updatePresence('on_hold')
                    this.notify('Call on hold', {type: 'info'})
                    this.audioNotification.play('park')
                    this.bc.postMessage({event: "tbcHold", params: {isOnHold: true}})
                } else {
                    this._callControlFailed(result, 'Hold failed')
                }
            }
        } catch (e) {
            console.error('Hold error:', e)
            this._callControlFailed(e, 'Hold operation failed')
        } finally {
            this.state.holdInProgress = false
        }
    }

    /** Pausing is only offered where a paused recording can come back. */
    get canPauseRecording() {
        return this.can('recording_pause') && this.can('recording_resume')
    }

    /** What pressing the recording button does now, or null when it can do nothing. */
    get recordingToggleAction() {
        if (this.state.isRecordingStopped) return null
        if (this.state.isPaused) {
            if (this.can('recording_resume')) return 'resume'
            return this.can('recording_stop') ? 'stop' : null
        }
        if (this.state.isRecording) {
            if (this.canPauseRecording) return 'pause'
            return this.can('recording_stop') ? 'stop' : null
        }
        return this.can('recording_start') ? 'start' : null
    }

    get recordingToggleTitle() {
        if (this.state.isRecordingStopped) return 'Recording stays off for the rest of this call.'
        return {
            start: 'Start recording',
            pause: 'Pause recording',
            resume: 'Resume recording',
            stop: 'Stop recording',
        }[this.recordingToggleAction] || 'This line cannot change the recording.'
    }

    /** Stop beside the button, for lines where pressing the button itself pauses. */
    get showRecordingStop() {
        return (this.state.isRecording || this.state.isPaused)
            && this.recordingToggleAction !== 'stop'
            && this.can('recording_stop')
    }

    /**
     * Show what the server said. An error beside `paused` or `on` means the
     * provider refused the change, so the recording is still as it was.
     */
    _applyRecordingState(result) {
        const refused = !!result?.error
        const state = result?.state
        this.state.isRecording = (state === 'on' && !refused)
            || (state === 'paused' && refused) || state === 'starting'
        this.state.isPaused = (state === 'paused' && !refused) || (state === 'on' && refused)
        this.state.isRecordingStopped = state === 'stopped'
        this.recording_ref = result?.recording_ref || null
        this.recording_path = result?.recording_path || null
    }

    async _fetchRecordingState(retryAttempt = 0) {
        const callSid = this.call_sid
        if (!callSid || !this.can('recording_state')) return
        const asked = this._recordingControls
        try {
            const result = await this.orm.call('connect.channel', 'get_softphone_recording_state', [
                this._callControlPayload(callSid)])
            // The call ended or someone pressed the button, so this answer is old.
            if (this.call_sid !== callSid || this._recordingControls !== asked) return
            this._applyRecordingState(result)
            // A recording started at answer takes the provider a moment to list,
            // so look again after 2, 4 and 8 seconds.
            if (result?.state === 'off' && retryAttempt < 3) {
                setTimeout(() => {
                    if (this.call_sid === callSid && this._recordingControls === asked) {
                        this._fetchRecordingState(retryAttempt + 1)
                    }
                }, [2000, 4000, 8000][retryAttempt])
            }
        } catch (e) {
            console.warn('Connect: Could not fetch recording state:', e?.message || e)
        }
    }

    _onClickToggleRecording() {
        return this._controlRecording(this.recordingToggleAction)
    }

    _onClickStopRecording() {
        return this._controlRecording(this.showRecordingStop ? 'stop' : null)
    }

    async _controlRecording(action) {
        if (!action || this.state.recordingLoading || !this.can(`recording_${action}`)) return
        const callSid = this.session?.params?.CallSid || this.call_sid
        if (!callSid) {
            this.notify('No active call', {type: 'warning'})
            return
        }
        const words = {
            start: {done: 'Recording started', failed: 'The recording could not be started.'},
            pause: {done: 'Recording paused', failed: 'The recording could not be paused. It is still recording.'},
            resume: {done: 'Recording resumed', failed: 'The recording could not be resumed. It is still paused.'},
            stop: {done: 'Recording stopped', failed: 'The recording could not be confirmed as stopped. It may still be recording.'},
        }[action]
        const extra = {}
        if (this.recording_ref) extra.recording_ref = this.recording_ref
        if (this.recording_path) extra.recording_path = this.recording_path
        this._recordingControls++
        this.state.recordingLoading = true
        try {
            const result = await this.orm.call('connect.channel', `${action}_softphone_recording`, [
                this._callControlPayload(callSid, extra)])
            if (this.call_sid !== callSid && this.session?.params?.CallSid !== callSid) return
            if (result?.supported === false) {
                this._callControlFailed(result, words.failed)
                return
            }
            this._applyRecordingState(result)
            if (result?.error) {
                this._callControlFailed({error: words.failed}, words.failed)
            } else {
                this.notify(words.done, {type: 'info', sticky: false})
            }
        } catch (e) {
            console.warn('Connect: recording control failed:', e?.message || e)
            this._callControlFailed(e, words.failed)
        } finally {
            this.state.recordingLoading = false
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
        this._syncIncomingRing()
    }

    _onClickAudioSettings(ev) {
        const show = !this.state.isAudioSettings
        this.state.isAudioSettings = show
        if (show) {
            this.state.isKeypad = false
            this.state.isContacts = false
            this.state.isContactList = false
            this.state.isFavorites = false
            this.state.isCalls = false
            this.state.isDialingPanel = false
        }
    }

    _onChangeAudioEnabled(ev) {
        this.state.audioEnabled = ev.target.checked
        this.audioNotification.setEnabled(this.state.audioEnabled)
    }

    _onChangeAudioVolume(ev) {
        this.state.audioVolume = parseFloat(ev.target.value)
        this.audioNotification.setVolume(this.state.audioVolume)
    }

    _onClickPreviewSound(ev) {
        const soundKey = ev.target.dataset.sound || ev.target.closest('[data-sound]')?.dataset.sound
        if (soundKey) {
            this.audioNotification.preview(soundKey)
        }
    }

    async _onClickEndCall(ev) {
        const call = this.session
        const wasAnswered = this.state.phone_status === this.status.accepted
        const wasIncoming = this.state.inIncoming
        if (call) {
            this.suppressBroadcastChannel = true
            try {
                call.disconnect()
            } catch (e) {
                // An already-ended session is not a failed hang-up. The
                // cleanup below must run regardless, or the UI is left on a
                // dead call and the follow-on status/bus updates error out.
                console.warn('Connect: disconnect on ended session:', e)
            }
        }
        this._stopIncomingRing()
        this.bc.postMessage({event: "tbcEndCall"})
        this.state.phone_status = this.status.ended
        if (this._claimCallEnd(call)) {
            const label = wasAnswered ? "Terminated" : (wasIncoming ? "Rejected" : "Canceled")
            await this.setCallStatus(label)
            await this.endCall()
            if (this.lastActiveTab === this.tabs.phone) {
                setFocus(this.phoneInput.el)
            }
        }
    }

    _onClickAcceptIncoming(ev) {
        this._stopIncomingRing()
        if (this.session) {
            this.session.accept()
        }
        const params = this.getJsonCallData()
        this.bc.postMessage({event: "tbcAnswerCall", params})
        this.state.phone_status = this.status.accepted
        this.state.inIncoming = false
        this.audioNotification.play('connected')
        this.startCall()
    }

    async _onClickRejectIncoming(ev) {
        const call = this.session
        if (call) {
            this.suppressBroadcastChannel = true
            try {
                call.reject()
            } catch (e) {
                // Caller already gone == nothing left to reject; still run
                // the cleanup below.
                console.warn('Connect: reject on ended session:', e)
            }
        }
        this._stopIncomingRing()
        this.bc.postMessage({event: "tbcEndCall"})
        this.state.inIncoming = false
        if (this._claimCallEnd(call)) {
            await this.setCallStatus("Rejected")
            await this.endCall()
            if (this.lastActiveTab === this.tabs.phone) {
                setFocus(this.phoneInput.el)
            }
        }
    }

    async _onClickAcceptWaiting() {
        if (!this.waitingSession) return

        // Hold has to succeed before the second call is answered. A failed
        // hold used to be logged and then ignored, which left both dialogs live.
        const callSid = this.session?.params?.CallSid || this.call_sid
        if (!callSid) {
            this.notify('The current call cannot be held, so the new one keeps waiting.', {type: 'warning'})
            return
        }
        let held = false
        let holdError = null
        try {
            const result = await this.orm.call('connect.channel', 'hold_softphone_call', [
                this._callControlPayload(callSid)])
            held = result !== false && !(result && result.success === false)
            if (!held) holdError = result
        } catch (e) {
            console.error('Connect: Failed to hold current call:', e)
            holdError = e
        }
        if (!held) {
            this._callControlFailed(holdError, 'Could not hold the current call, so the new one keeps waiting.')
            return
        }
        if (!this.waitingSession) return
        this.state.isOnHold = true

        const info = {...(this.state.waitingCallerId || {})}
        const next = this.waitingSession
        this.heldSession = this.session
        this.heldCallSid = this.call_sid
        this.heldCallId = this.call_id
        this.heldCallerId = {...this.state.callerId}

        const phoneNumber = info.phoneNumber || (next.params && next.params.From) || ''
        this.session = next
        this.waitingSession = null
        this.state.hasWaitingCall = false
        this.state.waitingCallerId = {}
        this.state.callPhoneNumber = phoneNumber
        this.state.inIncoming = false
        if (info.partnerId) {
            this.state.isPartner = true
            this.state.callerId = {
                partnerId: parseInt(info.partnerId),
                partnerName: info.partnerName,
                partnerIconUrl: this.computePartnerIconUrl(info.partnerId),
                partnerUrl: this.computePartnerUrl(info.partnerId),
                phoneNumber,
            }
        } else {
            this.state.isPartner = false
            this.state.callerId = {phoneNumber}
            this.searchPartner(phoneNumber)
        }
        this.destroyCallCounter()
        this._attachIncomingCall(next, phoneNumber)
        this._syncIncomingRing()
        next.accept()
        this.state.phone_status = this.status.accepted
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
        if (this.can('dtmf') && validDTMF.includes(key)) {
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
