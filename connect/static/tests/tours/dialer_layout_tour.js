/** @odoo-module **/
/**
 * Regression cover for the in-call dialer clipping its own hang-up button.
 *
 * The phone is a FIXED-height (486px) flex column with `overflow: hidden`.
 * Every child pins `flex-shrink: 0` — except the `.mt-auto` stack that holds
 * the call controls, which was left shrinkable. During a call the visible body
 * panel is the dialing panel, which matches
 * `.o_body_phone:not(:has(.o_keypad)):not(:has(.o_dial_contact_list))` and was
 * `flex: 0 0 auto` (rigid), so 100% of any overflow was taken out of
 * `.mt-auto` and the hang-up button was clipped off the bottom.
 *
 * The tour reproduces that DOM shape without needing a live call — it shows the
 * dialing panel and makes it tall — then asserts the hang-up row is still
 * inside the phone's box.
 */

import { registry } from "@web/core/registry";

const OVERFLOW_PROBE_ID = "connect_dialer_overflow_probe";

function showDialingPanelWithTallContent() {
    const root = document.querySelector(".o_root_connect_phone");
    if (!root) {
        throw new Error("Phone root not found");
    }
    // Show the phone by hand rather than through the bus. The point of this
    // tour is the CSS, and every extra step costs a poll cycle — the softphone
    // is mounted against a throwaway credential that the provider rejects a
    // few seconds later, and HttpCase fails a test on ANY browser console
    // error (common.py sets had_failure before consulting error_checker). So
    // the tour has to be done before that lands.
    root.classList.remove("o_hide", "o_collapsed");
    // During a call the phone drops `o_hide` from the control rows; reproduce
    // that, otherwise .mt-auto has no height and there is nothing to clip.
    for (const row of root.querySelectorAll(".mt-auto .o_dial_panel")) {
        row.classList.remove("o_hide");
    }
    const panels = [...root.querySelectorAll(".o_body_phone")];
    // The dialing panel is the one the phone shows during a call: no keypad,
    // no contact list.
    const dialing = panels.find(
        (el) => !el.querySelector(".o_keypad") && !el.querySelector(".o_dial_contact_list")
    );
    if (!dialing) {
        throw new Error("Dialing panel not found");
    }
    for (const panel of panels) {
        panel.classList.toggle("o_hide", panel !== dialing);
    }
    // Stand in for a call's worth of content: caller card, timer, quality
    // metrics, transfer UI. Any tall body reproduces the overflow.
    const probe = document.createElement("div");
    probe.id = OVERFLOW_PROBE_ID;
    probe.style.height = "400px";
    probe.style.flex = "0 0 auto";
    dialing.appendChild(probe);
}

function assertCallControlsVisible() {
    const root = document.querySelector(".o_root_connect_phone");
    const controls = root.querySelector(".mt-auto .o_dial_panel_position");
    if (!controls) {
        throw new Error("Call control row not found");
    }
    const rootBox = root.getBoundingClientRect();
    const controlsBox = controls.getBoundingClientRect();
    // Allow a sub-pixel rounding slack; anything more is a real clip.
    const clipped = controlsBox.bottom - rootBox.bottom;
    if (clipped > 1) {
        throw new Error(
            `Call controls are clipped by ${clipped.toFixed(1)}px: ` +
                `controls bottom ${controlsBox.bottom.toFixed(1)} vs phone bottom ` +
                `${rootBox.bottom.toFixed(1)}. The hang-up button is unreachable.`
        );
    }
    if (controlsBox.height < 40) {
        throw new Error(
            `Call control row collapsed to ${controlsBox.height.toFixed(1)}px — ` +
                "the hang-up button is squashed."
        );
    }
    document.getElementById(OVERFLOW_PROBE_ID)?.remove();
}

registry.category("web_tour.tours").add("connect_dialer_layout_tour", {
    url: "/odoo",
    steps: () => [
        {
            content: "Show the phone and reproduce the in-call panel, tall enough to overflow",
            trigger: ".connect-tray .toggle-phone",
            run: showDialingPanelWithTallContent,
        },
        {
            content: "The hang-up button is still fully inside the phone",
            trigger: ".o_root_connect_phone .mt-auto .o_dial_panel_position",
            run: assertCallControlsVisible,
        },
    ],
});
