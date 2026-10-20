/** @odoo-module **/
// VoiceML softphone — a SIP-over-WebRTC client backed by JsSIP.
//
// Unlike connect_twilio's `Twilio.Device` phone, this registers a JsSIP UA
// against the VoiceML OpenSIPS WSS edge using the SIP credentials the module
// provisions (connect.user.get_voiceml_phone_config). Click-to-call is server-driven
// (connect.settings.originate_call dials the user's SIP URI), so this
// component's job is: register, ring on incoming INVITEs, answer only when
// the user accepts (or has just dialled from this phone), carry audio, and
// offer dialpad, DTMF, mute, hold and transfer. Hold and transfer go through
// Connect's provider-dispatched connect.channel entry points (payload
// provider: "voiceml"); mute and DTMF are JsSIP session calls.
import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { vlog } from "@connect_voiceml/js/utils";

const PROVIDER = "voiceml";
const DIAL_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"];
// A dial from this phone is expected to ring back within this long.
const OUTBOUND_WINDOW_MS = 30000;

/** One id per browser tab: sessionStorage is per tab, a reload keeps it. */
function tabNonce() {
    const fresh = () => (globalThis.crypto && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
    try {
        let nonce = sessionStorage.getItem("connect_voiceml_tab");
        if (!nonce) {
            nonce = fresh();
            sessionStorage.setItem("connect_voiceml_tab", nonce);
        }
        return nonce;
    } catch (err) {
        return fresh();
    }
}

export class VoiceMLPhone extends Component {
    static template = "connect_voiceml.phone";

    setup() {
        this.orm = useService("orm");
        this.remoteAudio = useRef("remoteAudio");
        this.state = useState({
            registration: "idle", uri: "", error: "", incoming: "", inCall: false,
            dialNumber: "", muted: false, onHold: false, busy: false,
            // "" | "blind" | "attended": the transfer target is being typed.
            transferMode: "",
            // true from Hold-the-caller until the consult is completed or cancelled.
            consulting: false,
        });
        this.session = null;
        this.heldSession = null;
        this.callSid = null;
        this.expectOutboundUntil = 0;
        this.nonce = tabNonce();
        onMounted(this._start.bind(this));
        onWillUnmount(this._stop.bind(this));
    }

    get keys() {
        return DIAL_KEYS;
    }

    async _start() {
        let cfg;
        try {
            cfg = await this.orm.call(
                "connect.user", "get_voiceml_phone_config", [], { nonce: this.nonce });
        } catch (err) {
            this.state.error = "Could not load softphone configuration.";
            vlog("config error", err);
            return;
        }
        if (!cfg || !cfg.ok) {
            this.state.registration = "disabled";
            return;
        }
        this.cfg = cfg;
        this.state.uri = cfg.uri;
        if (typeof JsSIP === "undefined") {
            this.state.error = "JsSIP is not loaded — check the asset bundle.";
            this.state.registration = "missing-js";
            return;
        }
        // The server derives this from the domain when the setting is empty;
        // never hand JsSIP anything but a non-empty string.
        const wss = typeof cfg.wss_url === "string" ? cfg.wss_url.trim() : "";
        if (!wss) {
            this.state.registration = "failed";
            this.state.error = "No WSS URL is configured for the VoiceML softphone.";
            return;
        }
        this.wssUrl = wss;
        this._register();
    }

    _register() {
        const socket = new JsSIP.WebSocketInterface(this.wssUrl);
        const uaConfig = {
            sockets: [socket],
            uri: `sip:${this.cfg.uri}`,
            authorization_user: this.cfg.username,
            password: this.cfg.password,
            realm: this.cfg.realm,
            register: true,
        };
        // Per-tab registration binding: a second tab must not replace the
        // first tab's contact.
        if (this.cfg.instance_id) {
            uaConfig.instance_id = this.cfg.instance_id;
        }
        this.ua = new JsSIP.UA(uaConfig);
        this.ua.on("registered", () => {
            this.state.registration = "registered";
            vlog("registered as", this.cfg.uri);
        });
        this.ua.on("unregistered", () => {
            this.state.registration = "unregistered";
        });
        this.ua.on("registrationFailed", (e) => {
            this.state.registration = "failed";
            this.state.error = e && e.cause ? String(e.cause) : "registration failed";
            vlog("registration failed", e);
        });
        this.ua.on("newRTCSession", ({ session, originator }) => {
            if (originator === "remote") {
                this._handleIncoming(session);
            }
        });
        this.ua.start();
    }

    _handleIncoming(session) {
        const from = session.remote_identity.uri.toString();
        vlog("incoming call from", from);
        if (this.session && !(this.state.consulting && !this.heldSession)) {
            // One call at a time: a second INVITE gets busy, never the mic.
            // The one exception is the consult leg of an attended transfer.
            session.terminate({ status_code: 486, reason_phrase: "Busy Here" });
            return;
        }
        if (this.session) {
            this.heldSession = this.session;
        }
        this.session = session;
        this.sessionSid = this._sidOf(session);
        if (!this.heldSession) {
            this.callSid = this.sessionSid;
        }
        this.state.incoming = session.remote_identity.display_name || from;
        session.on("confirmed", () => {
            this.state.inCall = true;
            this._attachRemote(session);
        });
        session.on("ended", () => this._endSession(session));
        session.on("failed", () => this._endSession(session));
        // The microphone opens only from accept(), or for the call this user
        // just dialled from this phone (the server rings us back).
        if (Date.now() < this.expectOutboundUntil) {
            this.expectOutboundUntil = 0;
            this.accept();
        }
    }

    /** The VoiceML call SID, when the INVITE carries it (else the server finds the leg). */
    _sidOf(session) {
        const request = session && session.request;
        if (!request || !request.getHeader) {
            return null;
        }
        for (const name of ["X-VoiceML-CallSid", "X-Twilio-CallSid", "X-CallSid"]) {
            const value = request.getHeader(name);
            if (value) {
                return value;
            }
        }
        return null;
    }

    accept() {
        if (!this.session || this.state.inCall) {
            return;
        }
        this.session.answer({ mediaConstraints: { audio: true, video: false } });
        this.state.incoming = "";
    }

    decline() {
        if (!this.session) {
            return;
        }
        if (this.state.consulting && this.heldSession) {
            this.attendedCancel();
            return;
        }
        if (this.state.inCall) {
            this.session.terminate();
        } else {
            this.session.terminate({ status_code: 603, reason_phrase: "Decline" });
        }
    }

    _endSession(session) {
        if (this.heldSession === session) {
            this.heldSession = null;
            return;
        }
        if (this.session !== session) {
            return;
        }
        if (this.heldSession) {
            // The consult leg ended: the held caller is the call again.
            this.session = this.heldSession;
            this.heldSession = null;
            this.state.incoming = "";
            this._attachRemote(this.session);
            return;
        }
        this.session = null;
        this.callSid = null;
        this.sessionSid = null;
        this.state.incoming = "";
        this.state.inCall = false;
        this.state.muted = false;
        this.state.onHold = false;
        this.state.transferMode = "";
        this.state.consulting = false;
        this._detachRemote();
    }

    // --- Dialpad and outbound dial ---------------------------------------

    onKey(key) {
        if (this.state.inCall && this.session && !this.state.transferMode) {
            this.sendDTMF(key);
        } else {
            this.state.dialNumber += key;
        }
    }

    backspace() {
        this.state.dialNumber = this.state.dialNumber.slice(0, -1);
    }

    /** Ring this phone back from the server, bridged to `number`. */
    async _originate(number) {
        this.expectOutboundUntil = Date.now() + OUTBOUND_WINDOW_MS;
        try {
            await this.orm.call("connect.settings", "originate_call", [number]);
        } catch (err) {
            this.expectOutboundUntil = 0;
            this.state.error = (err && err.data && err.data.message) || "Could not place the call.";
            throw err;
        }
    }

    async dial() {
        const number = this.state.dialNumber.trim();
        if (!number || this.session || this.state.busy) {
            return;
        }
        this.state.error = "";
        this.state.busy = true;
        try {
            await this._originate(number);
            this.state.dialNumber = "";
        } catch (err) {
            vlog("dial failed", err);
        } finally {
            this.state.busy = false;
        }
    }

    // --- In-call: DTMF, mute ---------------------------------------------

    sendDTMF(tone) {
        if (this.session && this.state.inCall) {
            this.session.sendDTMF(tone);
        }
    }

    toggleMute() {
        if (!this.session || !this.state.inCall) {
            return;
        }
        if (this.state.muted) {
            this.session.unmute({ audio: true });
        } else {
            this.session.mute({ audio: true });
        }
        this.state.muted = !this.state.muted;
    }

    // --- In-call: hold and transfer (provider-dispatched, server side) ---

    _payload(extra = {}) {
        const payload = { provider: PROVIDER, ...extra };
        if (this.callSid) {
            payload.channel_sid = this.callSid;
        }
        return payload;
    }

    async _control(model, method, extra) {
        this.state.error = "";
        try {
            const result = await this.orm.call(model, method, [this._payload(extra)]);
            if (result && result.success === false) {
                throw new Error(result.error || "Call control failed");
            }
            if (result && result.channel_sid && !this.callSid) {
                this.callSid = result.channel_sid;
            }
            return result;
        } catch (err) {
            this.state.error = (err && err.data && err.data.message) || err.message || "Call control failed";
            return null;
        }
    }

    async toggleHold() {
        if (!this.session || !this.state.inCall || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const hold = !this.state.onHold;
            const result = await this._control(
                "connect.channel", hold ? "hold_softphone_call" : "resume_softphone_call");
            if (result) {
                (this.heldSession || this.session)[hold ? "hold" : "unhold"]();
                this.state.onHold = hold;
            }
        } finally {
            this.state.busy = false;
        }
    }

    startTransfer(mode) {
        this.state.transferMode = this.state.transferMode === mode ? "" : mode;
        this.state.dialNumber = "";
    }

    /** Blind: hand the caller to the typed number and drop out. */
    async blindTransfer() {
        const number = this.state.dialNumber.trim();
        if (!number || !this.session || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const result = await this._control(
                "connect.channel", "forward_softphone_call", { number });
            if (result) {
                this.state.transferMode = "";
                this.state.dialNumber = "";
                this.session.terminate();
            }
        } finally {
            this.state.busy = false;
        }
    }

    /** Attended step 1: hold the caller, then ring the consult target. */
    async attendedStart() {
        const number = this.state.dialNumber.trim();
        if (!number || !this.session || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const held = await this._control(
                "connect.channel", "attended_transfer_softphone_call", { step: "start" });
            if (!held) {
                return;
            }
            this.session.hold();
            this.state.onHold = true;
            this.state.consulting = true;
            this.consultNumber = number;
            this.state.dialNumber = "";
            try {
                await this._originate(number);
            } catch (err) {
                await this.attendedCancel();
            }
        } finally {
            this.state.busy = false;
        }
    }

    /** Attended step 2: connect the caller to the consulted target and drop out. */
    async attendedComplete() {
        if (!this.state.consulting || this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const result = await this._control(
                "connect.channel", "attended_transfer_softphone_call",
                { step: "complete", number: this.consultNumber });
            if (result) {
                this.state.consulting = false;
                this.state.transferMode = "";
                for (const s of [this.session, this.heldSession]) {
                    if (s) {
                        try { s.terminate(); } catch (err) { vlog("terminate", err); }
                    }
                }
            }
        } finally {
            this.state.busy = false;
        }
    }

    /** Attended abort: drop the consult leg and take the caller off hold. */
    async attendedCancel() {
        const consult = this.heldSession ? this.session : null;
        const caller = this.heldSession || this.session;
        const result = await this._control(
            "connect.channel", "attended_transfer_softphone_call", { step: "cancel" });
        if (consult) {
            try { consult.terminate(); } catch (err) { vlog("terminate", err); }
        }
        if (result && caller) {
            caller.unhold();
            this.state.onHold = false;
        }
        this.state.consulting = false;
        this.state.transferMode = "";
    }

    _attachRemote(session) {
        const stream = session.connection.getRemoteStreams()[0];
        if (stream && this.remoteAudio.el) {
            this.remoteAudio.el.srcObject = stream;
            this.remoteAudio.el.play().catch(() => {});
        }
    }

    _detachRemote() {
        if (this.remoteAudio.el) {
            this.remoteAudio.el.srcObject = null;
        }
    }

    _stop() {
        if (this.ua) {
            this.ua.stop();
            this.ua = null;
        }
    }
}
