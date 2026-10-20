/** @odoo-module **/
import {Phone} from "@connect/components/phone/phone/phone"
import {CallerIntelCard} from "../caller_intel_card/caller_intel_card"

// Precedent: /mnt/19/odoo/addons/mail/static/src/discuss/core/common/message_patch.js
Phone.components = {...Phone.components, CallerIntelCard}
