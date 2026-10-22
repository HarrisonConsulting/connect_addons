/** @odoo-module **/
import {registry} from "@web/core/registry"
import {LoopbackCall} from "@connect/../tests/helpers/loopback_transport"

// Precedent: connect/static/tests/unit/click_to_call_origin.test.js
registry.category("web_tour.tours").add("connect_caller_intel_ring_tour", {
    url: "/odoo",
    steps: () => [
        {
            content: "Open the softphone",
            trigger: ".connect-tray .toggle-phone",
            run: "click",
        },
        {
            content: "A spam caller rings the softphone",
            trigger: ".connect-tray .toggle-phone",
            run: () => {
                window.connectCallerIntelLoopback._emit(
                    "incoming",
                    new LoopbackCall({From: "+14155550199", CallSid: "CA" + "9".repeat(32)})
                )
            },
        },
        {
            content: "The ring card carries the spam verdict",
            trigger: ".o_caller_intel_card[data-verdict='likely_spam']",
        },
        {
            content: "Staff mark the caller as not spam",
            trigger: ".o_caller_intel_mark_trusted",
            run: "click",
        },
        {
            content: "The card no longer shows the spam verdict",
            trigger: ".o_caller_intel_card:not([data-verdict='likely_spam'])",
        },
    ],
})
