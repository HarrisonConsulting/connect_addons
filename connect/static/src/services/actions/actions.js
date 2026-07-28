/** @odoo-module **/

import {registry} from "@web/core/registry"
import {browser} from "@web/core/browser/browser"
import {routerBus} from "@web/core/browser/router"
import {user} from "@web/core/user"

const {markup} = owl

var personal_channel = 'connect_actions_' + user.userId
var common_channel = 'connect_actions'

// Minimum gap between two view reloads for the same model. `reload_view` is
// broadcast on a shared, org-wide bus channel, so a single busy call can emit
// far faster than a view can usefully redraw. Bursts collapse into one leading
// reload plus one trailing reload, so nothing is silently dropped.
const RELOAD_THROTTLE_MS = 5000

export const pbxActionService = {
    dependencies: ["action", "notification", 'bus_service'],

    start(env, {action, notification, bus_service}) {
        this.action = action
        this.notification = notification
        // model -> {last: timestamp, timer: handle}
        this.reloadThrottle = {}

        bus_service.addChannel(personal_channel)
        bus_service.addChannel(common_channel)
        bus_service.addChannel('connect_presence')
        bus_service.subscribe("connect_notify", (action) => this.connect_handle_notify(action))
        bus_service.subscribe("reload_view", (action) => this.connect_handle_reload_view(action))
        bus_service.subscribe("voicemail_new", (payload) => this.connect_handle_voicemail_new(payload))
    },

    /**
     * Reload the current view if it is showing `model` — throttled, and never
     * over a form view.
     *
     * ROUTE_CHANGE re-runs the action from scratch. On a multi-record view
     * that is the intended live refresh; on a form view it discards whatever
     * the user is part-way through typing. Realtime kanban updates are not
     * worth destroying in-progress edits, so form views are left alone and
     * pick up server state on their next save or manual reload.
     */
    connect_request_reload: function (model) {
        if (!this.action || !this.action.currentController) return
        const controller = this.action.currentController
        if (controller.action.res_model !== model) return
        if (controller.view && controller.view.type === "form") return

        // Precedent for the global `luxon` + `browser.setTimeout` pair:
        // /mnt/19/odoo/addons/web/static/src/core/notifications/notification.js
        const now = luxon.DateTime.now().ts
        const entry = this.reloadThrottle[model] || (this.reloadThrottle[model] = {last: 0, timer: null})
        const elapsed = now - entry.last
        if (elapsed >= RELOAD_THROTTLE_MS) {
            entry.last = now
            routerBus.trigger("ROUTE_CHANGE")
            return
        }
        // Inside the window: schedule one trailing reload for the end of it.
        if (entry.timer) return
        entry.timer = browser.setTimeout(() => {
            entry.timer = null
            entry.last = luxon.DateTime.now().ts
            // Re-check: the user may have navigated away, or into a form,
            // while the trailing reload was pending.
            const current = this.action && this.action.currentController
            if (!current || current.action.res_model !== model) return
            if (current.view && current.view.type === "form") return
            routerBus.trigger("ROUTE_CHANGE")
        }, RELOAD_THROTTLE_MS - elapsed)
    },

    connect_handle_reload_view: function (message) {
        this.connect_request_reload(message.model)
    },

    connect_handle_notify: function ({title, message, sticky, warning}) {
        if (warning === true)
            this.notification.add(markup(message), {title, sticky, type: 'danger'})
        else
            this.notification.add(markup(message), {title, sticky, type: 'info'})
    },

    connect_handle_voicemail_new: function () {
        this.connect_request_reload('connect.call')
    },
}

registry.category("services").add("connectActionService", pbxActionService)
