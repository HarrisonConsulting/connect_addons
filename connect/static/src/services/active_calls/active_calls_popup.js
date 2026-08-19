/** @odoo-module **/
import {useService} from "@web/core/utils/hooks"

import {Component, useState, useExternalListener, useRef} from "@odoo/owl"

export class ConnectActiveCallsPopup extends Component {
    static template = 'connect.active_calls_popup'
    static props = {
        controller: Object,
    }

    setup() {
        this.action = useService('action')
        this.state = useState(this.props.controller.state)
        this.root = useRef('root')
        // The panel stays until dismissed, the way a dropdown does, rather
        // than hiding on a timer — a timer risks vanishing it mid-read or
        // mid-click.
        useExternalListener(window, "click", this._onWindowClick, {capture: true})
        useExternalListener(window, "keydown", this._onWindowKeydown)
    }

    _onWindowClick(ev) {
        if (!this.state.isDisplay) {
            return
        }
        // The systray button owns its own toggle; let it through.
        if (ev.target.closest(".connect-active-calls-tray")) {
            return
        }
        if (this.root.el && !this.root.el.contains(ev.target)) {
            this.props.controller.close()
        }
    }

    _onWindowKeydown(ev) {
        if (ev.key === "Escape" && this.state.isDisplay) {
            this.props.controller.close()
        }
    }

    _onClickClose() {
        this.props.controller.close()
    }

    _OpenActiveCallForm(id) {
        this.props.controller.close()
        this.action.doAction({
            res_id: id,
            res_model: 'connect.call',
            target: 'current',
            type: 'ir.actions.act_window',
            views: [[false, 'form']],
        })
    }

    _openPartnerForm(ev, partner) {
        if (partner) {
            ev.stopPropagation()
            this.props.controller.close()
            this.action.doAction({
                res_id: partner[0],
                res_model: 'res.partner',
                target: 'current',
                type: 'ir.actions.act_window',
                views: [[false, 'form']],
            })
        }
    }

    _openReferenceForm(ev, ref) {
        if (ref) {
            ev.stopPropagation()
            this.props.controller.close()
            let [res_model, res_id] = ref.split(',')
            res_id = parseInt(res_id)
            this.action.doAction({
                res_id,
                res_model,
                target: 'current',
                type: 'ir.actions.act_window',
                views: [[false, 'form']],
            })
        }
    }
}
