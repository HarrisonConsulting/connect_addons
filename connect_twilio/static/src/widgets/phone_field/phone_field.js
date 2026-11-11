/** @odoo-module **/
"use strict"

import {onMounted, reactive, useState} from "@odoo/owl"
import {registry} from "@web/core/registry"
import {browser} from "@web/core/browser/browser"
import {patch} from "@web/core/utils/patch"
import {PhoneField} from "@web/views/fields/phone/phone_field"
import {useService} from "@web/core/utils/hooks"
import {_t} from "@web/core/l10n/translation"
import {user} from "@web/core/user"

export function createWhatsAppCapabilities(orm) {
    const state = reactive({call: false, message: false, pending: true})
    let pending
    async function refresh() {
        if (pending) return pending
        Object.assign(state, {call: false, message: false, pending: true})
        pending = (async () => {
            try {
                const result = await Promise.resolve().then(() => orm.call("connect.user", "get_whatsapp_capabilities", []))
                Object.assign(state, {call: result?.call === true, message: result?.message === true})
            } catch {
                Object.assign(state, {call: false, message: false})
            } finally {
                state.pending = false
                pending = null
            }
            return {call: state.call, message: state.message}
        })()
        return pending
    }
    return {state, refresh}
}

export const whatsappCapabilitiesService = {
    dependencies: ["orm"],
    start(env, {orm}) {
        const capabilities = createWhatsAppCapabilities(orm)
        // Refresh after configuration work in another tab/window. Fields also
        // refresh when mounted, and every action awaits a fresh check.
        browser.addEventListener("focus", () => { void capabilities.refresh() })
        return capabilities
    },
}
registry.category("services").add("connect_whatsapp_capabilities", whatsappCapabilitiesService)


patch(PhoneField.prototype, {

    setup() {
        super.setup()
        this.action = useService("action")
        this.whatsapp = useService("connect_whatsapp_capabilities")
        this.whatsappCapabilities = useState(this.whatsapp.state)
        this.notification = useService("notification")
        onMounted(() => { void this.whatsapp.refresh() })
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

    async _onClickWhatsappCallButton(e) {
        e.preventDefault()
        if (!(await this._canUseWhatsApp("call"))) return
        // Pass whatsapp_call flag via kwargs to avoid breaking positional args
        return this._originateCall({whatsapp_call: true})
    },

    async _canUseWhatsApp(operation) {
        const capabilities = await this.whatsapp.refresh()
        if (!capabilities[operation]) {
            this.notification.add(_t("WhatsApp is not available for this action."), {type: "warning"})
            return false
        }
        return true
    },

    async _onClickWhatsappMessageButton(e){
        e.preventDefault()
        if (!(await this._canUseWhatsApp("message"))) return
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
