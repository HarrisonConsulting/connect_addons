/** @odoo-module **/
"use strict"

import {patch} from "@web/core/utils/patch"
import {PhoneField} from "@web/views/fields/phone/phone_field"
import {useService} from "@web/core/utils/hooks"
import {user} from "@web/core/user"

patch(PhoneField.prototype, {

    setup() {
        super.setup()
        this.action = useService("action")
    },

    _onClickCallButton(e) {
        if (window.connectBus) {
            return super._onClickCallButton(e)
        }
        // No softphone is not no phone: the server rings the user's first
        // callflow, which can be a desk phone on SIP.
        e.preventDefault()
        this._originateCall()
    },

    _onClickWhatsappCallButton(e) {
        e.preventDefault()
        // Pass whatsapp_call flag via kwargs to avoid breaking positional args
        this._originateCall({whatsapp_call: true})
    },

    async _onClickWhatsappMessageButton(e){
        e.preventDefault()
        await this.props.record.save()
        this.action.doAction(
            {
                type: "ir.actions.act_window",
                target: "new",
                name: this.title,
                res_model: "connect.whatsapp_composer",
                views: [[false, "form"]],
                context: {
                    ...user.context,
                    active_model: this.props.record.resModel,
                    active_id: this.props.record.resId,
                    default_phone: this.props.record.data[this.props.name],
                },
            },
            {
                onClose: () => {
                    this.props.record.load()
                    this.props.record.model.notify()
                },
            }
        )
    }
})
