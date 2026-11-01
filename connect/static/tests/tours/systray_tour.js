/** @odoo-module **/
/**
 * Tours for the two Connect systray surfaces.
 *
 *   connect_phone_tray_state_tour
 *       The phone button carries its connection state as a class + status dot
 *       instead of raising toasts. Asserts the state markup renders and that no
 *       connection toast is raised while the transport is coming up (in a
 *       headless test it never registers — the path that would otherwise
 *       spray "Reconnecting to phone service..." across the screen).
 *
 *   connect_active_calls_absent_tour / connect_active_calls_present_tour
 *       The active-calls button exists only while calls are in progress, and
 *       carries the live count.
 */

import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("connect_phone_tray_state_tour", {
    url: "/odoo",
    steps: () => [
        {
            content: "Phone systray button is mounted",
            trigger: ".connect-tray .toggle-phone",
        },
        {
            content: "It draws the handset glyph that carries the state colour",
            trigger: ".connect-tray .toggle-phone .connect-tray-glyph",
        },
        {
            content: "It carries a corner badge (the state indicator)",
            trigger: ".connect-tray .toggle-phone .connect-tray-badge",
        },
        {
            content: "Its state is one of the known lifecycle classes",
            trigger:
                ".connect-tray .toggle-phone.connecting," +
                ".connect-tray .toggle-phone.available," +
                ".connect-tray .toggle-phone.offline," +
                ".connect-tray .toggle-phone.error," +
                ".connect-tray .toggle-phone.unavailable",
        },
        {
            content: "Screen readers get a live status region instead of a toast",
            trigger: ".connect-tray [role='status'][aria-live='polite']",
        },
        {
            content: "No connection toast was raised",
            trigger: "body:not(:has(.o_notification:contains('Reconnecting')))",
        },
        {
            content: "The old always-on Reconnect button is gone",
            trigger: "body:not(:has(.connect-reconnect-btn))",
        },
    ],
});

registry.category("web_tour.tours").add("connect_active_calls_absent_tour", {
    url: "/odoo",
    steps: () => [
        {
            content: "Wait for the webclient",
            trigger: ".o_main_navbar",
        },
        {
            content: "No calls in progress means no active-calls button",
            trigger: "body:not(:has(.connect-active-calls-tray))",
        },
    ],
});

registry.category("web_tour.tours").add("connect_active_calls_present_tour", {
    url: "/odoo",
    steps: () => [
        {
            content: "Active-calls button appears while a call is in progress",
            trigger: ".connect-active-calls-tray button",
        },
        {
            content: "It shows the live count",
            trigger: ".connect-active-calls-tray .connect-active-calls-count:contains('1')",
            run: "click",
        },
        {
            content: "The panel lists the call",
            trigger: ".o_active_calls td:contains('+15551230001')",
        },
        {
            content: "The panel stays put until dismissed, then closes",
            trigger: ".o_active_calls .o_active_calls_close",
            run: "click",
        },
        {
            content: "Panel is closed but the button remains",
            trigger: ".connect-active-calls-tray button:not(.active)",
        },
    ],
});
