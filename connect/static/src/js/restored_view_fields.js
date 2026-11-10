/** @odoo-module **/
/**
 * Going back to a list or kanban restores the model settings saved when the
 * user left it, including the fields it read. If the view changed meanwhile
 * (a release landed under an open tab, or the view was edited), the view can
 * name a field those saved settings never read, and a row decoration or a
 * modifier on that field fails with "Name '<field>' is not defined".
 *
 * Complete the restored settings with the fields the current view reads, so
 * the reload fetches them. Scoped to Connect models.
 */

import { patch } from "@web/core/utils/patch";
import { extractFieldsFromArchInfo } from "@web/model/relational_model/utils";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { ListController } from "@web/views/list/list_controller";

function completeRestoredConfig(params, archInfo, viewFields) {
    const restored = params.state?.config;
    if (!restored || params.config !== restored || !restored.resModel?.startsWith("connect.")) {
        return params;
    }
    const { activeFields, fields } = extractFieldsFromArchInfo(archInfo, viewFields);
    const missing = Object.keys(activeFields).filter((name) => !(name in restored.activeFields));
    if (!missing.length) {
        return params;
    }
    const config = {
        ...restored,
        activeFields: { ...restored.activeFields },
        fields: { ...restored.fields },
    };
    for (const name of missing) {
        config.activeFields[name] = activeFields[name];
        config.fields[name] = fields[name];
    }
    return { ...params, config };
}

patch(ListController.prototype, {
    get modelParams() {
        return completeRestoredConfig(super.modelParams, this.archInfo, this.props.fields);
    },
});

patch(KanbanController.prototype, {
    get modelParams() {
        return completeRestoredConfig(super.modelParams, this.props.archInfo, this.props.fields);
    },
});
