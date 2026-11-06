/** @odoo-module **/
/**
 * A person with two lines on a real page. The Python side, which gives them their
 * rows, is connect/tests/test_line_row.py.
 */

import { registry } from "@web/core/registry";
import { registerLoopbackProvider } from "@connect/../tests/helpers/loopback_transport";

registerLoopbackProvider("other_voice");

const PANEL = ".o_root_connect_phone:not(.o_hide):not(.o_collapsed)";
const ROW = `${PANEL} .o_phone_line_selector`;

function panelHeight() {
    return document.querySelector(".o_root_connect_phone").getBoundingClientRect().height;
}

function assertHeightAndNothingClipped() {
    const panel = document.querySelector(".o_root_connect_phone");
    if (panelHeight() !== 572) {
        throw new Error(`The panel is ${panelHeight()}px high, not 572px.`);
    }
    const control = panel.querySelector(".o_dial_panel_position").getBoundingClientRect();
    if (control.bottom - panel.getBoundingClientRect().bottom > 1) {
        throw new Error("The call button is clipped.");
    }
    const body = panel.querySelector(".o_keypad").closest(".o_body_phone");
    if (body.scrollHeight > body.clientHeight) {
        throw new Error(`The keypad needs ${body.scrollHeight}px and has ${body.clientHeight}px.`);
    }
}

/**
 * Where connect_enqueue is installed, its stylesheet adds its activity bar's 40px to
 * --phone-height whenever the bar is in the panel. The bar itself only renders for
 * an agent, so a bare element of its class stands in for it.
 */
function assertActivityBarComposes() {
    const panel = document.querySelector(".o_root_connect_phone");
    if (!panel.querySelector("button[aria-label='Work Console']")) {
        return;
    }
    const probe = document.createElement("div");
    probe.className = "o_connect_activity_toggle";
    panel.append(probe);
    const height = panelHeight();
    probe.remove();
    if (height !== 612) {
        throw new Error(`With the activity bar the panel is ${height}px high, not 612px.`);
    }
}

function openPanel() {
    return [
        {
            content: "Open the panel",
            trigger: ".connect-tray .toggle-phone",
            run: "click",
        },
        {
            content: "The panel is open, with no row if it opened on a Work tab",
            trigger: PANEL,
            run() {
                const work = this.anchor.querySelector("button[aria-label='Work Console'].active");
                if (work && this.anchor.querySelector(".o_phone_line_selector")) {
                    throw new Error("The From row shows on the Work tab.");
                }
            },
        },
        {
            content: "Go to the keypad",
            trigger: `${PANEL} .o_phone_tabs button[aria-label='Keypad']`,
            run: "click",
        },
    ];
}

registry.category("web_tour.tours").add("connect_line_row_tour", {
    url: "/odoo",
    steps: () => [
        ...openPanel(),
        {
            content: "Two lines show as two chips, by name, the first one chosen",
            trigger: `${ROW}:has(.o_phone_line_chip:nth-of-type(2)):not(:has(.o_phone_line_dropdown)) .o_phone_line_chip.active:contains(Desk)`,
        },
        {
            content: "The caption shows its caller ID and how it connects",
            trigger: `${ROW} .o_phone_line_caption:contains(+15550100):contains(browser)`,
        },
        {
            content: "There is a To label",
            trigger: `${PANEL} .o_phone_to_label:contains(To)`,
        },
        {
            content: "The panel grew by the row, and still holds everything",
            trigger: `${PANEL} .o_keypad`,
            run: assertHeightAndNothingClipped,
        },
        {
            content: "It composes with an activity bar",
            trigger: `${PANEL} .o_keypad`,
            run: assertActivityBarComposes,
        },
        {
            content: "Choose the other line",
            trigger: `${ROW} .o_phone_line_chip:contains(Sales plan)`,
            run: "click",
        },
        {
            content: "It is the one chosen now, and the keypad still fits with its caller IDs",
            trigger: `${ROW}:has(.o_phone_line_caller_id_picker) .o_phone_line_chip.active:contains(Sales plan)`,
            run: assertHeightAndNothingClipped,
        },
        {
            content: "Pick the second one",
            trigger: `${ROW} select.o_phone_line_caller_id_picker:has(option[value='+15550102'])`,
            run: "select +15550102",
        },
        {
            content: "Reload the page",
            trigger: ROW,
            run() {
                window.location.reload();
            },
            expectUnloadPage: true,
        },
        ...openPanel(),
        {
            content: "The choice stuck",
            trigger: `${ROW} .o_phone_line_chip.active:contains(Sales plan)`,
        },
        {
            content: "Change leads to how the line connects",
            trigger: `${ROW} .o_phone_line_change`,
            run: "click",
        },
        {
            content: "Audio Settings explains it, and the row is gone",
            trigger: `${PANEL}:not(:has(.o_phone_line_selector)) .o_phone_line_how_block:contains(How this line connects):contains(Sales plan)`,
        },
        {
            content: "Back to the keypad",
            trigger: `${PANEL} .o_phone_tabs button[aria-label='Keypad']`,
            run: "click",
        },
        {
            content: "Type a number",
            trigger: `${ROW} ~ .o_body_phone .o_keypad`,
            run() {
                const buttons = [...this.anchor.querySelectorAll(".o_keypad_button")];
                for (const digit of "5550199") {
                    buttons.find((button) => button.textContent === digit).click();
                }
            },
        },
        {
            content: "Call",
            trigger: `${PANEL} .o_dial_panel_position button[aria-label='Call']`,
            run: "click",
        },
        {
            content: "During the call the row is read-only and shows its line",
            trigger: `${ROW}:not(:has(.o_phone_line_chip:enabled)):not(:has(.o_phone_line_change)):not(:has(select)) .o_phone_line_chip.active:contains(Sales plan)`,
        },
        {
            content: "End the call",
            trigger: `${PANEL} .o_dial_panel_position button[aria-label='End Call']`,
            run: "click",
        },
        {
            content: "The row comes back on the same line",
            trigger: `${ROW}:has(.o_phone_line_chip:enabled) .o_phone_line_chip.active:contains(Sales plan)`,
        },
    ],
});
