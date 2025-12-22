/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class PhoneSystray extends Component {
    static template = "connect.PhoneSystray";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.dropdownRef = useRef("dropdown");

        this.state = useState({
            inCall: false,
            isRinging: false,
            isMuted: false,
            isHeld: false,
            duration: 0,
            callerName: "",
            callerNumber: "",
            showDropdown: false,
            phoneStatus: "ended",
        });

        // Timer for call duration
        this.durationInterval = null;

        // Get the phone bus from the window (it's created by main.js)
        this.bus = null;

        // Click outside handler
        this.onClickOutside = (event) => {
            if (this.state.showDropdown && this.dropdownRef.el &&
                !this.dropdownRef.el.contains(event.target)) {
                this.closeDropdown();
            }
        };

        onMounted(() => {
            // Get bus from window (created by main.js)
            this.bus = window.connectBus;

            if (this.bus) {
                // Listen for state updates from the phone tray
                this.bus.addEventListener('busTraySetState', this.handleTrayState.bind(this));
                this.bus.addEventListener('busTraySetException', this.handleTrayException.bind(this));

                // Listen for call status updates (these come from phone.js)
                this.bus.addEventListener('busPhoneCallStatus', this.handleCallStatus.bind(this));
            }

            document.addEventListener('click', this.onClickOutside);
        });

        onWillUnmount(() => {
            if (this.bus) {
                this.bus.removeEventListener('busTraySetState', this.handleTrayState.bind(this));
                this.bus.removeEventListener('busTraySetException', this.handleTrayException.bind(this));
                this.bus.removeEventListener('busPhoneCallStatus', this.handleCallStatus.bind(this));
            }

            document.removeEventListener('click', this.onClickOutside);

            if (this.durationInterval) {
                clearInterval(this.durationInterval);
            }
        });
    }

    handleTrayState(event) {
        const { isDisplay, inCall } = event.detail;
        this.state.inCall = inCall;

        if (inCall && !this.durationInterval) {
            this.startDurationTimer();
        } else if (!inCall && this.durationInterval) {
            this.stopDurationTimer();
        }
    }

    handleTrayException(event) {
        // Handle exceptions if needed
        const { exception } = event.detail;
        if (exception) {
            console.warn("Phone exception:", exception);
        }
    }

    handleCallStatus(event) {
        // This is a custom event we'll need to add to phone.js
        // For now, we'll work with what we have
        const { status, callerName, callerNumber, isMuted } = event.detail || {};

        if (status) {
            this.state.phoneStatus = status;
            this.state.isRinging = status === "incoming";
        }

        if (callerName !== undefined) {
            this.state.callerName = callerName;
        }

        if (callerNumber !== undefined) {
            this.state.callerNumber = callerNumber;
        }

        if (isMuted !== undefined) {
            this.state.isMuted = isMuted;
        }
    }

    startDurationTimer() {
        this.state.duration = 0;
        this.durationInterval = setInterval(() => {
            this.state.duration += 1;
        }, 1000);
    }

    stopDurationTimer() {
        if (this.durationInterval) {
            clearInterval(this.durationInterval);
            this.durationInterval = null;
        }
        this.state.duration = 0;
    }

    formatDuration(seconds) {
        const totalSeconds = parseInt(seconds) || 0;
        const hours = Math.floor(totalSeconds / 3600);
        const mins = Math.floor((totalSeconds % 3600) / 60);
        const secs = totalSeconds % 60;

        if (hours > 0) {
            return `${hours}:${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
        }
        return `${mins}:${secs.toString().padStart(2, "0")}`;
    }

    get buttonClass() {
        if (this.state.isRinging) {
            return "o-phone-systray-btn ringing";
        }
        if (this.state.inCall) {
            if (this.state.isHeld) {
                return "o-phone-systray-btn on-hold";
            }
            return "o-phone-systray-btn in-call";
        }
        return "o-phone-systray-btn idle";
    }

    get iconClass() {
        if (this.state.isRinging) {
            return "fa fa-phone icon-call ringing-icon";
        }
        if (this.state.inCall) {
            return "fa fa-phone icon-call active-icon";
        }
        return "fa fa-phone icon-call";
    }

    get badgeText() {
        if (this.state.isRinging) {
            return "Incoming";
        }
        if (this.state.inCall) {
            if (this.state.isHeld) {
                return "HOLD";
            }
            return this.formatDuration(this.state.duration);
        }
        return "";
    }

    onClick() {
        if (this.state.inCall || this.state.isRinging) {
            this.toggleDropdown();
        } else {
            // Open the main phone panel
            if (this.bus) {
                this.bus.trigger('busPhoneToggleDisplay');
            }
        }
    }

    toggleDropdown() {
        this.state.showDropdown = !this.state.showDropdown;
    }

    closeDropdown() {
        this.state.showDropdown = false;
    }

    answerCall() {
        if (this.bus) {
            this.bus.trigger('busPhoneAnswerCall');
        }
        this.state.isRinging = false;
        this.closeDropdown();
    }

    rejectCall() {
        if (this.bus) {
            this.bus.trigger('busPhoneRejectCall');
        }
        this.state.isRinging = false;
        this.closeDropdown();
    }

    hangupCall() {
        if (this.bus) {
            this.bus.trigger('busPhoneHangUp');
        }
        this.closeDropdown();
    }

    toggleMute() {
        if (this.bus) {
            this.bus.trigger('busPhoneToggleMute');
        }
        this.state.isMuted = !this.state.isMuted;
    }

    toggleHold() {
        if (this.bus) {
            this.bus.trigger('busPhoneToggleHold');
        }
        this.state.isHeld = !this.state.isHeld;
    }

    openKeypad() {
        if (this.bus) {
            this.bus.trigger('busPhoneToggleDisplay');
        }
        this.closeDropdown();
    }

    openTransfer() {
        if (this.bus) {
            this.bus.trigger('busPhoneOpenTransfer');
        }
        this.closeDropdown();
    }
}

// Register in systray at sequence 40
registry.category("systray").add("connect.phone_systray", {
    Component: PhoneSystray
}, { sequence: 40 });
