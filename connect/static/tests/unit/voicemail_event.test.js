/** @odoo-module **/
"use strict"
/**
 * A new message reloads the call list and leaves every other screen where it is.
 */

import {describe, expect, test} from "@odoo/hoot"
import {patchWithCleanup} from "@web/../tests/web_test_helpers"
import {routerBus} from "@web/core/browser/router"
import {pbxActionService} from "@connect/services/actions/actions"

describe.current.tags("desktop")

function started() {
    const subscriptions = []
    const service = Object.create(pbxActionService)
    service.start({}, {
        action: {},
        notification: {add() {}},
        bus_service: {
            addChannel() {},
            subscribe(name, handler) {
                subscriptions.push({name, handler})
            },
        },
    })
    return {service, subscriptions}
}

test("the service subscribes to a new message", () => {
    const {subscriptions} = started()
    expect(subscriptions.map((row) => row.name)).toEqual([
        "connect_notify",
        "reload_view",
        "connect_call_state",
        "voicemail_new",
    ])
})

test("a new message reloads only the call list", () => {
    const triggers = []
    patchWithCleanup(routerBus, {
        trigger(name) {
            triggers.push(name)
        },
    })
    const {service, subscriptions} = started()
    const handler = subscriptions.find((row) => row.name === "voicemail_new").handler
    service.action = {currentController: {action: {res_model: "connect.call"}}}
    handler({})
    expect(triggers).toEqual(["ROUTE_CHANGE"])

    triggers.length = 0
    service.action = {currentController: {action: {res_model: "res.partner"}}}
    handler({})
    service.connect_handle_voicemail_new({})
    expect(triggers).toEqual([])

    service.action = {}
    service.connect_handle_voicemail_new({})
    expect(triggers).toEqual([])
})
