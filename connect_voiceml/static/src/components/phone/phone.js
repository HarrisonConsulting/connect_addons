/** @odoo-module **/
// VoiceML softphone — a SIP-over-WebRTC client backed by JsSIP.
//
// Unlike connect_twilio's `Twilio.Device` phone, this registers a JsSIP UA
// against the VoiceML OpenSIPS WSS edge using the SIP credentials the module
// provisions (connect.user.get_voiceml_phone_config). Click-to-call is server-driven
// (connect.settings.originate_call dials the user's SIP URI), so this
// component's job is: register, ring on incoming INVITEs, answer only when
// the user accepts, and carry audio.
import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { vlog } from "@connect_voiceml/js/utils";

export class VoiceMLPhone extends Component {
    static template = "connect_voiceml.phone";

    setup() {
        this.orm = useService("orm");
        this.remoteAudio = useRef("remoteAudio");
        this.state = useState({
            registration: "idle", uri: "", error: "", incoming: "", inCall: false,
        });
        this.session = null;
        onMounted(this._start.bind(this));
        onWillUnmount(this._stop.bind(this));
    }

    async _start() {
        let cfg;
        try {
            cfg = await this.orm.call("connect.user", "get_voiceml_phone_config", []);
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
        this._register();
    }

    _register() {
        const socket = new JsSIP.WebSocketInterface(this.cfg.wss_url);
        this.ua = new JsSIP.UA({
            sockets: [socket],
            uri: `sip:${this.cfg.uri}`,
            authorization_user: this.cfg.username,
            password: this.cfg.password,
            realm: this.cfg.realm,
            register: true,
        });
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
        if (this.session) {
            // One call at a time: a second INVITE gets busy, never the mic.
            session.terminate({ status_code: 486, reason_phrase: "Busy Here" });
            return;
        }
        this.session = session;
        this.state.incoming = session.remote_identity.display_name || from;
        session.on("confirmed", () => {
            this.state.inCall = true;
            this._attachRemote(session);
        });
        session.on("ended", () => this._endSession(session));
        session.on("failed", () => this._endSession(session));
        // No auto-answer: the microphone opens only from accept().
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
        if (this.state.inCall) {
            this.session.terminate();
        } else {
            this.session.terminate({ status_code: 603, reason_phrase: "Decline" });
        }
    }

    _endSession(session) {
        if (this.session !== session) {
            return;
        }
        this.session = null;
        this.state.incoming = "";
        this.state.inCall = false;
        this._detachRemote();
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
