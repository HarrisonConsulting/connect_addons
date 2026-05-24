/** @odoo-module **/
import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { Dialog } from "@web/core/dialog/dialog";

export class ConnectStripePaymentDialog extends Component {
    static template = "connect_stripe.payment_dialog";
    static components = { Dialog };
    static props = {
        callId: Number,
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            phase: "setup",
            amount: "",
            amountError: null,
            partnerName: null,
            currency: "",
            maskedCard: null,
            cardType: null,
            errorMessage: null,
            paymentId: null,
            cancelling: false,
        });
        this._pollInterval = null;
        this._captureTimeout = null;
        this._MAX_CAPTURE_MS = 5 * 60 * 1000;  // 5 minutes — guards abandoned dialogs

        onWillStart(async () => {
            try {
                const [call] = await this.orm.read(
                    "connect.call",
                    [this.props.callId],
                    ["partner"],
                );
                if (call && call.partner) {
                    this.state.partnerName = call.partner[1];
                }
            } catch (_e) {
                // Non-fatal — agent can still take the payment.
            }
        });

        onWillUnmount(() => this._stopPolling());
    }

    async _initiate() {
        const amount = parseFloat(this.state.amount);
        if (!amount || amount <= 0) {
            this.state.amountError = "Please enter a valid amount.";
            return;
        }
        this.state.amountError = null;
        try {
            const id = await this.orm.call("connect.stripe.payment", "create", [
                { call_id: this.props.callId, amount },
            ]);
            await this.orm.call("connect.stripe.payment", "action_initiate", [[id]]);
            this.state.paymentId = id;
            this.state.phase = "capturing";
            this._pollInterval = setInterval(() => this._poll(), 2000);
            this._captureTimeout = setTimeout(() => this._onCaptureTimeout(), this._MAX_CAPTURE_MS);
        } catch (e) {
            this.notification.add(e.message || "Failed to initiate payment.", { type: "danger" });
        }
    }

    async _onCaptureTimeout() {
        if (["complete", "failed", "cancelled"].includes(this.state.phase)) {
            return;
        }
        this._stopPolling();
        try {
            await this.orm.call("connect.stripe.payment", "action_cancel", [[this.state.paymentId]]);
        } catch (_e) {
            // Ignore — we're surfacing a timeout to the agent regardless.
        }
        this.state.errorMessage =
            "Payment timed out after 5 minutes. The customer's call has been returned.";
        this.state.phase = "failed";
    }

    async _poll() {
        try {
            const result = await this.orm.call(
                "connect.stripe.payment",
                "action_get_state",
                [[this.state.paymentId]]
            );
            const s = result.state;
            if (s === "capturing") {
                // Surface partial card info as it arrives from intermediate webhooks.
                if (result.masked_card) {
                    this.state.maskedCard = result.masked_card;
                    this.state.cardType = result.card_type;
                }
                return;
            } else if (s === "captured" || s === "processing") {
                this.state.maskedCard = result.masked_card;
                this.state.cardType = result.card_type;
                this.state.phase = "processing";
            } else if (s === "complete") {
                this._stopPolling();
                this.state.maskedCard = result.masked_card;
                this.state.cardType = result.card_type;
                this.state.phase = "complete";
            } else if (s === "failed") {
                this._stopPolling();
                this.state.errorMessage = result.error_message || "Payment failed.";
                this.state.phase = "failed";
            } else if (s === "cancelled") {
                this._stopPolling();
                this.state.phase = "cancelled";
            }
        } catch (e) {
            this._stopPolling();
            this.state.errorMessage = e.message || "An error occurred while checking payment status.";
            this.state.phase = "failed";
        }
    }

    _stopPolling() {
        if (this._pollInterval) {
            clearInterval(this._pollInterval);
            this._pollInterval = null;
        }
        if (this._captureTimeout) {
            clearTimeout(this._captureTimeout);
            this._captureTimeout = null;
        }
    }

    async _cancel() {
        if (!this.state.paymentId) {
            this.props.close();
            return;
        }
        this.state.cancelling = true;
        try {
            await this.orm.call("connect.stripe.payment", "action_cancel", [[this.state.paymentId]]);
        } catch (e) {
            this.notification.add(e.message || "Cancel failed.", { type: "warning" });
        }
        this.state.cancelling = false;
        this._stopPolling();
        this.props.close();
    }

    _retry() {
        this._stopPolling();
        this.state.phase = "setup";
        this.state.amount = "";
        this.state.amountError = null;
        this.state.maskedCard = null;
        this.state.cardType = null;
        this.state.errorMessage = null;
        this.state.paymentId = null;
    }
}
