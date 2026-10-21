/** @odoo-module **/
import {Component, onMounted, onWillStart, onWillUnmount, onWillUpdateProps, useState} from "@odoo/owl"
import {useService} from "@web/core/utils/hooks"
import {_t} from "@web/core/l10n/translation"

export const VERDICTS = {
    trusted: {label: _t("Verified caller"), cls: "text-bg-success"},
    known: {label: _t("Known contact"), cls: "text-bg-light"},
    unknown: {label: _t("No record"), cls: "text-bg-light"},
    suspicious: {label: _t("Suspicious"), cls: "text-bg-warning"},
    likely_spam: {label: _t("Likely spam"), cls: "text-bg-danger"},
    likely_spoof: {label: _t("Likely spoofed"), cls: "text-bg-danger"},
}

export class CallerIntelCard extends Component {
    static template = "connect_caller_intel.CallerIntelCard"
    static props = {phoneNumber: {type: String, optional: true}}

    setup() {
        this.orm = useService("orm")
        this.busService = useService("bus_service")
        this.notification = useService("notification")
        this.state = useState({card: false})
        this._onReputationUpdate = (payload) => {
            if (payload && payload.e164 === this.state.card.e164) {
                this._load(this.props.phoneNumber)
            }
        }
        onWillStart(() => this._load(this.props.phoneNumber))
        onWillUpdateProps((next) => {
            if (next.phoneNumber !== this.props.phoneNumber) {
                return this._load(next.phoneNumber)
            }
        })
        onMounted(() => this.busService.subscribe("connect_reputation_update", this._onReputationUpdate))
        onWillUnmount(() => this.busService.unsubscribe("connect_reputation_update", this._onReputationUpdate))
    }

    async _load(number) {
        if (!number) {
            this.state.card = false
            return
        }
        try {
            this.state.card = await this.orm.call("connect.call", "caller_intel_card", [number])
        } catch (error) {
            // The card is informational: a failed fetch must never disturb the call panel.
            console.warn("caller intel card unavailable", error)
            this.state.card = false
        }
    }

    async _mark(method, message) {
        try {
            await this.orm.call("connect.call", method, [[this.state.card.call_id]])
            await this._load(this.props.phoneNumber)
            this.notification.add(message, {type: "success"})
        } catch (error) {
            console.warn("caller intel mark failed", error)
            this.notification.add(_t("Could not save the mark"), {type: "danger"})
        }
    }

    onMarkSpam() {
        return this._mark("action_caller_mark_spam", _t("Marked as spam"))
    }

    onMarkTrusted() {
        return this._mark("action_caller_mark_trusted", _t("Marked as not spam"))
    }

    get canMark() {
        return Boolean(this.state.card.call_id && this.state.card.can_mark)
    }

    get verdict() {
        return VERDICTS[this.state.card.verdict] || VERDICTS.unknown
    }

    get showFlags() {
        const card = this.state.card
        return card && (card.marks.length || card.signals.length)
    }
}
