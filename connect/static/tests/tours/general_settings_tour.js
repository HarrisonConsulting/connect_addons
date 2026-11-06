/** @odoo-module **/
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("connect_general_settings_button", {
    steps: () => [
        { trigger: ".settings_tab .tab[data-key='connect']", run: "click" },
        { trigger: "button.o_connect_general_settings", run: "click" },
        { trigger: ".o_form_view .o_notebook a[name='general']" },
    ],
});
