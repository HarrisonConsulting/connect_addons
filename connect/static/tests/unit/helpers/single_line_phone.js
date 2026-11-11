/** @odoo-module **/
"use strict"
/**
 * The one place that knows Phone's props, so a change to them touches this file
 * and no test that mounts a phone.
 */

import {EventBus} from "@odoo/owl"
import {mockService, mountWithCleanup, onRpc} from "@web/../tests/web_test_helpers"
import {Phone} from "@connect/components/phone/phone/phone"
// Registers the loopback under the real provider key, which the phone looks up.
import "@connect/../tests/helpers/loopback_transport"

export const SINGLE_LINE_NONCE = "tab-nonce-1"

// What connect.channel answers for Twilio: every action, and asserted so by
// connect_twilio's TestCapabilitiesTwilio.
export const ALL_CAPABILITIES = Object.fromEntries([
    "hold", "resume", "attended_start", "attended_complete", "attended_cancel",
    "add_participant", "forward", "recording_state", "recording_start",
    "recording_pause", "recording_resume", "recording_stop",
].map((action) => [action, true]))

export async function mountLines(lines, extraProps = {}) {
    mockService("connect_audio", {play() {}})
    onRpc("connect.user", "validate_dial_number", ({args}) => args[0])
    onRpc("connect.user", "dialer_suggestions", () => [])
    // The phone reports presence as soon as its transport registers.
    onRpc("connect.user", "update_presence", () => true)
    // An answered call asks for its recording state.
    onRpc("connect.channel", "get_softphone_recording_state", () => ({}))
    // The favorites tab loads with the panel.
    onRpc("connect.favorite", "search_read", () => [])
    return mountWithCleanup(Phone, {
        props: {
            bus: new EventBus(),
            lines,
            browserSessionNonce: SINGLE_LINE_NONCE,
            ...extraProps,
        },
    })
}

export function mountSingleLinePhone(tokenData) {
    return mountLines([{
        key: "twilio",
        provider: "twilio",
        media: "browser",
        capabilities: ALL_CAPABILITIES,
        token_data: tokenData,
    }])
}
