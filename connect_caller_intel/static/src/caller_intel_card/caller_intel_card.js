/** @odoo-module **/
import {Component, onMounted, onWillStart, onWillUnmount, onWillUpdateProps, useState} from "@odoo/owl"
import {useService} from "@web/core/utils/hooks"
import {_t} from "@web/core/l10n/translation"

const VERDICTS = {
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

    get verdict() {
        return VERDICTS[this.state.card.verdict] || VERDICTS.unknown
    }

    get showFlags() {
        const card = this.state.card
        return card && (card.marks.length || card.signals.length)
    }
}
