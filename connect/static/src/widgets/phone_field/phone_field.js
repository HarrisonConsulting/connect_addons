/** @odoo-module **/
"use strict"

import {patch} from "@web/core/utils/patch"
import {PhoneField} from "@web/views/fields/phone/phone_field"

patch(PhoneField.prototype, {

    _onClickCallButton(e) {
        const bus = window.connectBus
        // No phone on this page: the href stays a plain tel link.
        if (!bus) {
            return
        }
        e.preventDefault()
        if (bus.clickToCallLocal) {
            // Dial from this browser, so the audio stays on the device that clicked.
            const {resModel, resId} = this.props.record.model.config
            const destination = {phone: this.props.record.data[this.props.name]}
            if (resModel === "res.partner") destination.partnerId = resId
            bus.trigger("busPhoneMakeCall", destination)
            return
        }
        // The phone holds the From selection, so it answers both of these at the click.
        const toolId = bus.clickToCallToolId
        this._originateCall(toolId ? {context: {connect_tool_id: toolId}} : {})
    },

    /** Ask the server to place the call, towards this tab when it has a phone. */
    _originateCall(extraKwargs = {}) {
        const {resModel, resId} = this.props.record.model.config
        const args = [this.props.record.data[this.props.name], resModel, resId]
        const kwargs = {...extraKwargs}
        const bus = window.connectBus
        if (bus?.clickToCallCallerId) {
            kwargs.caller_id = bus.clickToCallCallerId
        }
        if (bus && bus.browserSessionNonce) {
            kwargs.origin_nonce = bus.browserSessionNonce
        }
        return this.env.model.orm.call("connect.settings", "originate_call", args, kwargs)
    },
})
