# -*- coding: utf-8 -*-
"""connect_voicetel 19.0.2.0.1 — drop the two settings views 19.0.2.0.0 retired.

19.0.2.0.0 removed the res.config.settings block and the connect.settings
form that extended connect_twilio's. Their rows stay in ir_ui_view until
Odoo deletes orphaned xmlids at the very end of an upgrade, and until then
every settings view validated later in the same run inherits them and fails
on fields that no longer exist. Deleting them before this module loads
closes that window.

Exact xmlids only. Forward-only and idempotent; raises when an xmlid points
at an unexpected model or a view inheriting it belongs to another module.
"""

MODULE = 'connect_voicetel'
RETIRED_VIEWS = {
    'res_config_settings_view_form_connect_voicetel': 'res.config.settings',
    'connect_voicetel_settings_form': 'connect.settings',
}


def _retired_view_ids(cr):
    ids = []
    for name, expected_model in RETIRED_VIEWS.items():
        cr.execute(
            "SELECT res_id, model FROM ir_model_data WHERE module = %s AND name = %s",
            (MODULE, name))
        row = cr.fetchone()
        if not row:
            continue
        res_id, imd_model = row
        if imd_model != 'ir.ui.view':
            raise AssertionError(
                '%s.%s is a %s, expected ir.ui.view' % (MODULE, name, imd_model))
        cr.execute("SELECT model FROM ir_ui_view WHERE id = %s", (res_id,))
        view = cr.fetchone()
        if not view:
            continue
        if view[0] != expected_model:
            raise AssertionError(
                '%s.%s (view %s) is on %s, expected %s'
                % (MODULE, name, res_id, view[0], expected_model))
        ids.append(res_id)
    return ids


def _with_descendants(cr, root_ids):
    cr.execute("""
        WITH RECURSIVE tree(id, depth) AS (
            SELECT id, 0 FROM ir_ui_view WHERE id = ANY(%s)
            UNION ALL
            SELECT v.id, t.depth + 1 FROM ir_ui_view v JOIN tree t ON v.inherit_id = t.id
        )
        SELECT id, max(depth) FROM tree GROUP BY id ORDER BY max(depth) DESC
    """, (root_ids,))
    return [row[0] for row in cr.fetchall()]


def _assert_owned(cr, view_ids):
    cr.execute("""
        SELECT module, name, res_id FROM ir_model_data
        WHERE model = 'ir.ui.view' AND res_id = ANY(%s) AND module != %s
    """, (view_ids, MODULE))
    foreign = cr.fetchall()
    if foreign:
        raise AssertionError(
            'Views inheriting the retired connect_voicetel settings views belong '
            'to other modules; resolve them before upgrading: %s'
            % ', '.join('%s.%s (%s)' % row for row in foreign))


def migrate(cr, version):
    root_ids = _retired_view_ids(cr)
    if not root_ids:
        return
    view_ids = _with_descendants(cr, root_ids)
    _assert_owned(cr, view_ids)
    for view_id in view_ids:
        cr.execute("DELETE FROM ir_ui_view WHERE id = %s", (view_id,))
    cr.execute(
        "DELETE FROM ir_model_data WHERE model = 'ir.ui.view' AND res_id = ANY(%s)",
        (view_ids,))
