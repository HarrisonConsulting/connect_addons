/** @odoo-module **/
"use strict"

import {patch} from "@web/core/utils/patch"
import {PhoneField} from "@web/views/fields/phone/phone_field"

patch(PhoneField.prototype, {

    _onClickCallButton(e) {
        if (window.connectBus) {
            return super._onClickCallButton(e)
        }
        // FreeSWITCH has its own panel and rings from the server.
        e.preventDefault()
        this._originateCall()
    },
})
