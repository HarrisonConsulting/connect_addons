/** @odoo-module **/
import {registry} from "@web/core/registry"
import {user} from "@web/core/user"
import {browser} from "@web/core/browser/browser"
import {ConnectActiveCallsTray} from "./active_calls_tray"
import {ConnectActiveCallsPopup} from "./active_calls_popup"
import {reactive} from "@odoo/owl"

// Calls appear and end far faster than a widget should redraw, and the
// `reload_view` signal is broadcast org-wide, so a busy switchboard can emit
// several per second. Collapse a burst into one refresh.
const REFRESH_DEBOUNCE_MS = 400

const ACTIVE_CALL_DOMAIN = [["status", "=", "in-progress"]]

/**
 * Owns the live active-call count shared by the systray button and its popup.
 *
 * The count is event-driven rather than polled: connect.call broadcasts
 * `reload_view` on exactly the transitions that change it (call created,
 * finalised, errored — see connect.call.on_call_status). A refresh on tab
 * re-focus closes the one gap the bus leaves, which is a tab that was asleep.
 */
class ActiveCallsController {
    constructor(orm) {
        this.orm = orm
        this.state = reactive({
            count: 0,
            calls: [],
            isDisplay: false,
            isLoading: false,
        })
        this._refreshTimer = null
    }

    async refreshCount() {
        try {
            this.state.count = await this.orm.searchCount("connect.call", ACTIVE_CALL_DOMAIN)
        } catch (e) {
            // A transient failure must not strand a stale badge on screen.
            console.warn("Connect: active call count refresh failed:", e)
            this.state.count = 0
        }
        if (this.state.count === 0) {
            this.state.calls = []
            this.state.isDisplay = false
        } else if (this.state.isDisplay) {
            await this.loadCalls()
        }
    }

    async loadCalls() {
        this.state.isLoading = true
        try {
            this.state.calls = await this.orm.call("connect.call", "get_widget_calls", [ACTIVE_CALL_DOMAIN])
            this.state.count = this.state.calls.length
        } catch (e) {
            console.warn("Connect: active call load failed:", e)
            this.state.calls = []
        } finally {
            this.state.isLoading = false
        }
    }

    scheduleRefresh() {
        if (this._refreshTimer) {
            return
        }
        this._refreshTimer = browser.setTimeout(() => {
            this._refreshTimer = null
            this.refreshCount()
        }, REFRESH_DEBOUNCE_MS)
    }

    async toggleDisplay() {
        this.state.isDisplay = !this.state.isDisplay
        if (this.state.isDisplay) {
            await this.loadCalls()
        }
    }

    close() {
        this.state.isDisplay = false
    }
}

export const ConnectActiveCallsService = {
    dependencies: ["orm", "bus_service"],

    async start(env, {orm, bus_service}) {
        // Reading connect.call requires group_connect_user (security/user.xml).
        // Without this gate the count query below would raise an AccessError for
        // every backend user who has nothing to do with the phone system.
        if (!(await user.hasGroup("connect.group_connect_user"))) {
            return
        }
        const controller = new ActiveCallsController(orm)

        registry.category("systray").add("activeCallsTray", {
            Component: ConnectActiveCallsTray,
            props: {controller},
        })
        registry.category("main_components").add("activeCallsPopup", {
            Component: ConnectActiveCallsPopup,
            props: {controller},
        })

        // connectActionService adds this channel too; addChannel is idempotent,
        // and asking for it here keeps this service independent of that one's
        // start order.
        bus_service.addChannel("connect_actions")
        bus_service.subscribe("reload_view", ({model}) => {
            if (model === "connect.call") {
                controller.scheduleRefresh()
            }
        })
        // A hidden tab can miss bus traffic; re-sync the moment it comes back.
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden) {
                controller.scheduleRefresh()
            }
        })

        await controller.refreshCount()
    },
}

registry.category('services').add("connect_active_calls", ConnectActiveCallsService)
