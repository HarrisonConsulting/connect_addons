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

export async function mountLines(lines) {
    mockService("connect_audio", {play() {}})
    // The phone reports presence as soon as its transport registers.
    onRpc("connect.user", "update_presence", () => true)
    // An answered call asks for its recording state.
    onRpc("connect.call", "get_recording_state", () => ({}))
    // The favorites tab loads with the panel.
    onRpc("connect.favorite", "search_read", () => [])
    return mountWithCleanup(Phone, {
        props: {
            bus: new EventBus(),
            lines,
            browserSessionNonce: SINGLE_LINE_NONCE,
        },
    })
}

export function mountSingleLinePhone(tokenData) {
    return mountLines([{key: "twilio", provider: "twilio", media: "browser", token_data: tokenData}])
}
