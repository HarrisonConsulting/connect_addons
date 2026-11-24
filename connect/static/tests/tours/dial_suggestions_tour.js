/** @odoo-module **/
/**
 * Dial suggestions beside the keypad, for a person with one line and for one
 * with two. The Python side, which gives them their rows and the partners, is
 * connect/tests/test_dial_suggestions.py.
 */

import { registry } from "@web/core/registry";
import { registerLoopbackProvider } from "@connect/../tests/helpers/loopback_transport";

registerLoopbackProvider("other_voice");

const PANEL = ".o_root_connect_phone:not(.o_hide):not(.o_collapsed)";
const INPUT = `${PANEL} .o_phone_number_input`;
const NAME = "Zyxwvut Quillfeather";
const DIGITS = "555014";

const BODY = ".o_body_phone";

function inside(element, box) {
    const rect = element.getBoundingClientRect();
    return rect.height > 0 && rect.top >= box.top - 1 && rect.bottom <= box.bottom + 1;
}

function typeInto(input, text) {
    input.value = text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
}

function body() {
    return document.querySelector(INPUT).closest(BODY);
}

function bodyBox() {
    return body().getBoundingClientRect();
}

function assertKeysClickable() {
    const box = bodyBox();
    const keys = [...document.querySelectorAll(`${PANEL} .o_keypad_button`)];
    if (keys.length !== 12) {
        throw new Error(`The keypad has ${keys.length} keys, not 12.`);
    }
    for (const key of keys) {
        const rect = key.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        if (hit !== key || !inside(key, box)) {
            const covering = hit && `${hit.tagName}.${hit.className}`;
            throw new Error(`The key ${key.textContent} at ${JSON.stringify(rect)} is not clickable inside the body ${JSON.stringify(box)} (viewport ${innerHeight}); hit ${covering}.`);
        }
    }
}

function clickKeys(digits) {
    return [...digits].map((digit) => ({
        content: `Click the ${digit} key at its centre`,
        trigger: `${PANEL} .o_keypad_button:contains(${digit})`,
        run() {
            const rect = this.anchor.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
            if (hit !== this.anchor) {
                throw new Error(`The ${digit} key is covered or outside the viewport.`);
            }
            hit.click();
        },
    }));
}

function suggestionsTour(name) {
    registry.category("web_tour.tours").add(name, {
        url: "/odoo",
        steps: () => [
            {
                content: "Open the panel",
                trigger: ".connect-tray .toggle-phone",
                run: "click",
            },
            {
                content: "Go to the keypad",
                trigger: `${PANEL} .o_phone_tabs button[aria-label='Keypad']`,
                run: "click",
            },
            {
                content: "Without suggestions the keypad fits the body without scrolling",
                trigger: `${PANEL} .o_keypad`,
                run() {
                    if (body().scrollHeight > body().clientHeight) {
                        throw new Error("The keypad overflows the body.");
                    }
                    assertKeysClickable();
                },
            },
            {
                content: "Type a name",
                trigger: INPUT,
                run() {
                    typeInto(this.anchor, NAME.slice(0, 6));
                },
            },
            {
                content: "The first suggestion is visible inside the body and the keypad has given way",
                trigger: `${PANEL}:not(:has(.o_keypad)) .o_dial_suggestion:first`,
                run() {
                    if (!inside(this.anchor, bodyBox())) {
                        throw new Error("The first suggestion is not visible inside the body.");
                    }
                },
            },
            {
                content: "A longer name keeps the keypad hidden while its lookup runs",
                trigger: INPUT,
                run() {
                    typeInto(this.anchor, NAME.slice(0, 7));
                    if (document.querySelector(`${PANEL} .o_keypad`)) {
                        throw new Error("The keypad flashed in while a name lookup was pending.");
                    }
                },
            },
            {
                content: "Clear the field",
                trigger: INPUT,
                run() {
                    typeInto(this.anchor, "");
                },
            },
            {
                content: "The keypad is back",
                trigger: `${PANEL}:has(.o_keypad):not(:has(.o_dial_suggestion))`,
            },
            ...clickKeys(DIGITS),
            {
                content: "Two suggestions and all twelve keys fit inside the body",
                trigger: `${PANEL} .o_dial_suggestion:nth-child(2)`,
                run() {
                    const box = bodyBox();
                    const list = document.querySelector(`${PANEL} .o_dial_suggestions`).getBoundingClientRect();
                    const rows = [...document.querySelectorAll(`${PANEL} .o_dial_suggestion`)].slice(0, 2);
                    if (!rows.every((row) => inside(row, box) && inside(row, list))) {
                        throw new Error("The first two suggestions are not both visible in the list.");
                    }
                    assertKeysClickable();
                },
            },
            {
                content: "The input holds exactly the digits clicked",
                trigger: INPUT,
                run() {
                    if (this.anchor.value !== DIGITS) {
                        throw new Error(`The input holds ${this.anchor.value}, not ${DIGITS}.`);
                    }
                },
            },
            ...clickKeys("7"),
            {
                content: "The keys stay clickable while the list is refreshed",
                trigger: `${PANEL} .o_keypad_button:contains(7)`,
                run() {
                    assertKeysClickable();
                    if (document.querySelector(INPUT).value !== `${DIGITS}7`) {
                        throw new Error("The seventh digit did not reach the input.");
                    }
                },
            },
        ],
    });
}

suggestionsTour("connect_dial_suggestions_tour");
suggestionsTour("connect_dial_suggestions_one_line_tour");
