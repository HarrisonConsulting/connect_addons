/** @odoo-module **/
"use strict"
/**
 * A provider that rings the person's own phone, as far as a browser can tell: it
 * answers the two requests and publishes states. It exists only in the test bundle.
 */

import {onRpc} from "@web/../tests/web_test_helpers"

export const BRIDGE_PROVIDER = "fake_bridge"

export function bridgeLine(overrides = {}) {
    return {
        key: "bridge:1",
        tool_id: 41,
        label: "Sales plan",
        provider: BRIDGE_PROVIDER,
        media: "bridge",
        capabilities: {},
        token_data: {media: "bridge", label: "Sales plan"},
        ...overrides,
    }
}

/**
 * @param {Object} [options]
 * @param {Function} [options.originate] - stands in for originate_call's answer
 * @param {Function} [options.hangup] - stands in for hangup_softphone_call's answer
 * @returns {{originates: Object[], hangups: Object[]}} every request received
 */
export function installFakeBridgeProvider({originate = () => true, hangup = () => true} = {}) {
    const requests = {originates: [], hangups: []}
    onRpc("connect.settings", "originate_call", ({args, kwargs}) => {
        requests.originates.push({number: args[0], kwargs})
        return originate()
    })
    onRpc("connect.channel", "hangup_softphone_call", ({args, kwargs}) => {
        requests.hangups.push({payload: args[0], kwargs})
        return hangup()
    })
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    return requests
}

/** What the core actions service hands the phone for one connect_call_state message. */
export function publish(phone, call_ref, state, extra = {}) {
    phone.bus.trigger("busPhoneCallState", {call_ref, line_key: "bridge:1", state, reason_code: "", since: false, ...extra})
}
