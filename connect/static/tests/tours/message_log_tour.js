/** @odoo-module **/
/**
 * The message log opens as a list, and a failed message is drawn as a failed
 * row. An evaluation error in the row decoration fails the tour.
 */

import { registry } from "@web/core/registry";
import { rpc, rpcBus } from "@web/core/network/rpc";

registry.category("web_tour.tours").add("connect_message_log_tour", {
    steps: () => [
        {
            content: "The message log lists the messages",
            trigger: ".o_list_view .o_data_row",
        },
        {
            content: "A failed message is drawn as a failed row",
            trigger: ".o_list_view .o_data_row.text-danger",
        },
    ],
});

/**
 * The list view changes while the list is open, as it does when a release
 * lands under an open tab. Going back from a message to the list must still
 * draw the list.
 */
registry.category("web_tour.tours").add("connect_message_log_view_change_tour", {
    steps: () => [
        {
            content: "The list opens on the earlier view, with no failed row",
            trigger: ".o_list_view .o_data_row:not(.text-danger)",
        },
        {
            content: "The list view is replaced by the shipped one",
            trigger: ".o_list_view",
            async run() {
                const [view] = await rpc("/web/dataset/call_kw/ir.ui.view/search_read", {
                    model: "ir.ui.view",
                    method: "search_read",
                    args: [[["name", "=", "connect.message.tree"]], ["id"]],
                    kwargs: {},
                });
                await rpc("/web/dataset/call_kw/ir.ui.view/reset_arch", {
                    model: "ir.ui.view",
                    method: "reset_arch",
                    args: [[view.id], "hard"],
                    kwargs: {},
                });
                rpcBus.trigger("CLEAR-CACHES", "get_views");
            },
        },
        {
            content: "Open a message",
            trigger: ".o_list_view .o_data_row td.o_data_cell",
            run: "click",
        },
        {
            content: "The message opens",
            trigger: ".o_form_view .o_form_sheet",
        },
        {
            content: "Go back to the list",
            trigger: ".o_breadcrumb .o_back_button a, .o_breadcrumb li.breadcrumb-item a",
            run: "click",
        },
        {
            content: "The list is drawn again, with the failed row marked",
            trigger: ".o_list_view .o_data_row.text-danger",
        },
    ],
});
