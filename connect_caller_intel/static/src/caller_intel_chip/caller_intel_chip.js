/** @odoo-module **/
import {Component, onWillStart, onWillUpdateProps, useState} from "@odoo/owl"
import {useService} from "@web/core/utils/hooks"
import {VERDICTS} from "../caller_intel_card/caller_intel_card"

const QUIET_VERDICTS = ["unknown", "known"]

export class CallerIntelChip extends Component {
    static template = "connect_caller_intel.CallerIntelChip"
    static props = {verdict: {type: String, optional: true}, phoneNumber: {type: String, optional: true}}

    setup() {
        this.orm = useService("orm")
        this.state = useState({verdict: this.props.verdict || false})
        onWillStart(() => this._resolve(this.props))
        onWillUpdateProps((next) => this._resolve(next))
    }

    async _resolve(props) {
        if (props.verdict) {
            this.state.verdict = props.verdict
            return
        }
        if (!props.phoneNumber) {
            this.state.verdict = false
            return
        }
        try {
            const card = await this.orm.call("connect.call", "caller_intel_card", [props.phoneNumber])
            this.state.verdict = card.verdict
        } catch (error) {
            // The chip is informational: a failed fetch renders nothing.
            console.warn("caller intel chip unavailable", error)
            this.state.verdict = false
        }
    }

    get visible() {
        return Boolean(this.state.verdict) && !QUIET_VERDICTS.includes(this.state.verdict) && this.state.verdict in VERDICTS
    }

    get verdictInfo() {
        return VERDICTS[this.state.verdict]
    }
}
