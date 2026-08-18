/** @odoo-module **/
"use strict"
import {useService} from "@web/core/utils/hooks"
import {browser} from "@connect/js/utils"
import {Component, useState, onMounted, onWillStart, markup} from "@odoo/owl"

// Connection state is ambient, so it is rendered by the systray button itself
// rather than announced with toasts. Each entry drives the button's modifier
// class, its icon, and the text used for both the tooltip and the screen-reader
// live region -- one table so the three can never disagree.
//
// Order matters where states overlap: a live call outranks a stale
// 'connecting', and a hard fault outranks everything.
const PHONE_STATES = {
    unavailable: {icon: 'fa fa-lg fa-phone-square', label: 'Phone unavailable'},
    error: {icon: 'fa fa-lg fa-exclamation-triangle', label: 'Phone disconnected'},
    offline: {icon: 'fa fa-lg fa-plug', label: 'Phone offline'},
    connecting: {icon: 'fa fa-lg fa-circle-o-notch fa-spin', label: 'Connecting phone'},
    ringing: {icon: 'fa fa-lg fa-bell', label: 'Incoming call'},
    busy: {icon: 'fa fa-lg icon-call', label: 'On a call'},
    available: {icon: 'fa fa-lg icon-call', label: 'Phone ready'},
}

export class PhoneSysTray extends Component {
    static template = 'connect.menu'
    static props = {
        bus: Object
    }

    constructor() {
        super(...arguments)
        this.bus = this.props.bus
        this.state = useState({
            isDisplay: false,
            inCall: false,
            inIncoming: false,
            isActive: true,
            connectionStatus: 'connecting',
            connectionError: '',
            exception: null,
        })
        this.sound = false
        this.microphone = false
        this.message = 'For better user experience grant permission for: '
        this.browser = navigator.userAgent.includes("Firefox") ? browser.firefox : browser.chrome
    }

    setup() {
        super.setup()
        this.notification = useService("notification")
        // Disable Permission Check
        // this.permissionsChecked = localStorage.getItem('connect_permissions_checked')
        this.permissionsChecked = 'true'

        onMounted(() => {
            this.bus.addEventListener('busTraySetState', ({detail}) => {
                Object.assign(this.state, detail)
            })
            this.bus.addEventListener('busTraySetException', ({detail: {exception}}) => {
                this.state.exception = exception
            })
            if (this.permissionsChecked) return
            // Check sound permission
            this.testPlayer.play().then(() => {
                this.sound = true
                this.checkPermissions()
            }).catch((e) => {
                this.checkPermissions()
            })
        })

        onWillStart(async () => {
            if (this.permissionsChecked) return
            this.testPlayer = new Audio()
            this.testPlayer.src = "/connect/static/src/sounds/mute.mp3"
            this.testPlayer.volume = 0.5
            const self = this
            // Check microphone permission for Chrome
            if (this.browser === browser.chrome) {
                const permissionStatus = await navigator.permissions.query({name: 'microphone'})
                if (permissionStatus.state === "granted") {
                    this.microphone = true
                }
                // Check microphone permission for Firefox
            } else if (this.browser === browser.firefox) {
                navigator.mediaDevices
                    .getUserMedia({video: false, audio: true})
                    .then((stream) => {
                        stream.getTracks().forEach(function (track) {
                            track.stop()
                            self.microphone = true
                        })
                    })
                    .catch((err) => {
                        console.error(`you got an error: ${err}`)
                    })
            }
        })
    }

    /**
     * The one derived value the template renders from. Precedence runs
     * hard-fault -> live call -> transport state, so a call in progress is never
     * hidden behind a transport hiccup, and a dead phone is never painted green.
     */
    get phoneState() {
        if (!this.state.isActive || this.state.exception === 'NotSupported') {
            return 'unavailable'
        }
        if (this.state.inIncoming) {
            return 'ringing'
        }
        if (this.state.inCall) {
            return 'busy'
        }
        if (this.state.exception) {
            return 'error'
        }
        if (this.state.connectionStatus === 'ready') {
            return 'available'
        }
        return this.state.connectionStatus  // 'connecting' | 'offline' | 'error'
    }

    get stateInfo() {
        return PHONE_STATES[this.phoneState] || PHONE_STATES.available
    }

    /** True while the phone cannot place or take a call and a retry would help. */
    get isRecoverable() {
        return ['error', 'offline'].includes(this.phoneState)
    }

    /** Tooltip and live-region text: the state, plus the reason when we have one. */
    get statusLabel() {
        const reason = this.state.connectionError
        return reason ? `${this.stateInfo.label} — ${reason}` : this.stateInfo.label
    }

    get buttonTitle() {
        return this.isRecoverable
            ? `${this.statusLabel}. Click to reconnect.`
            : `${this.title} — ${this.statusLabel}`
    }

    /** Overridden by connect_enqueue, which opens the Work Console from here. */
    get title() {
        return 'Toggle Connect Phone'
    }

    checkPermissions() {
        if (!this.microphone || !this.sound) {
            this.notify()
        }
        localStorage.setItem('connect_permissions_checked', 'true')
    }

    notify() {
        this.message += this.sound ? '' : '<br/>&emsp; - Sound'
        this.message += this.microphone ? '' : '<br/>&emsp; - Microphone'
        this.message += 'Sound devices permissions error!</a>'
        this.notification.add(markup(this.message), {title: 'Connect', sticky: true, type: 'warning'})
    }

    _onClick() {
        // A broken phone reads as broken from the button, so the click that used
        // to raise an explanatory toast now does the repair the toast asked for.
        if (this.isRecoverable) {
            this._onClickReconnect()
            return
        }
        if (this.state.exception === 'AccessTokenInvalid') {
            this.notification.add('Please reload the page to refresh the Phone!', {title: 'Connect', type: 'warning'})
        } else if (this.state.exception === 'NotSupported') {
            this.notification.add(
                'Your browser does not support WebRTC. Please use Chrome, Firefox, or Edge for phone features.',
                {title: 'Connect', type: 'danger'}
            )
        } else if (this.state.exception) {
            this.notification.add(markup(this.state.exception), {title: 'Connect', type: 'warning'})
        } else {
            this.bus.trigger('busPhoneToggleDisplay')
        }
    }

    _onClickReconnect() {
        this.state.exception = null
        this.state.connectionStatus = 'connecting'
        this.state.connectionError = ''
        this.bus.trigger('busPhoneReconnect')
    }

    _onClickHangUp() {
        this.bus.trigger('busPhoneHangUp')
        this.state.isDisplay = false
        this.state.inCall = false
    }
}
