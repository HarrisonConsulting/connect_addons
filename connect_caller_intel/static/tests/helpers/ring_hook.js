/** @odoo-module **/
import {patch} from "@web/core/utils/patch"
import {LoopbackTransport} from "@connect/../tests/helpers/loopback_transport"

// Hands the ring tour the transport so it can emit an incoming call.
patch(LoopbackTransport.prototype, {
    async register() {
        window.connectCallerIntelLoopback = this
        return super.register(...arguments)
    },
})
