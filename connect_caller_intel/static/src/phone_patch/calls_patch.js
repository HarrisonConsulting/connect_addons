/** @odoo-module **/
import {patch} from "@web/core/utils/patch"
import {Calls} from "@connect/components/phone/calls/calls"
import {CallerIntelChip} from "../caller_intel_chip/caller_intel_chip"

// Precedent: /mnt/19/odoo/addons/mail/static/src/discuss/core/common/message_patch.js
patch(Calls, {
    components: {...Calls.components, CallerIntelChip},
})
