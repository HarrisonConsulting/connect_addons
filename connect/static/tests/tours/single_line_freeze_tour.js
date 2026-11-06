/** @odoo-module **/
/**
 * Freeze: the phone a person with one line sees on a real page. The Python side
 * is connect/tests/test_single_line_freeze.py.
 */

import { registry } from "@web/core/registry";

const TAB_LABELS = ["Keypad", "Contacts", "Favorites", "History", "Audio Settings"];

function assertOneToggle() {
    const count = document.querySelectorAll(".toggle-phone").length;
    if (count !== 1) {
        throw new Error(`There are ${count} phone buttons, not 1.`);
    }
}

function assertPanel() {
    const panel = document.querySelector(".o_root_connect_phone");
    const { height } = panel.getBoundingClientRect();
    if (height !== 486) {
        throw new Error(`The panel is ${height}px high, not 486px.`);
    }
    const tabs = panel.querySelector(".o_phone_tabs");
    for (const label of TAB_LABELS) {
        if (!tabs.querySelector(`button[aria-label='${label}']`)) {
            throw new Error(`The ${label} tab is missing.`);
        }
    }
}

registry.category("web_tour.tours").add("single_line_freeze_tour", {
    url: "/odoo",
    steps: () => [
        {
            content: "There is exactly one phone button",
            trigger: ".connect-tray .toggle-phone",
            run: assertOneToggle,
        },
        {
            content: "Open the panel",
            trigger: ".connect-tray .toggle-phone",
            run: "click",
        },
        {
            content: "The panel is open",
            trigger: ".o_root_connect_phone:not(.o_hide):not(.o_collapsed)",
        },
        {
            content: "It has no From row",
            trigger:
                ".o_root_connect_phone:not(:has(.o_phone_line_selector))" +
                ":not(:has([aria-label='From']))",
        },
        {
            content: "It keeps its height and every tab",
            trigger: ".o_root_connect_phone .o_phone_tabs",
            run: assertPanel,
        },
    ],
});
