/** @odoo-module **/
import {Component, useState} from "@odoo/owl"

export class ConnectActiveCallsTray extends Component {
    static template = 'connect.active_calls_tray'
    static props = {
        controller: Object,
    }

    setup() {
        this.state = useState(this.props.controller.state)
    }

    get label() {
        const n = this.state.count
        return n === 1 ? '1 call in progress' : `${n} calls in progress`
    }

    _onClick() {
        this.props.controller.toggleDisplay()
    }
}
