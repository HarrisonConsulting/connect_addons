# -*- coding: utf-8 -*-
"""Keep renamed Connect XMLIDs on their existing records."""

import logging


_logger = logging.getLogger(__name__)

_XMLID_RECONCILIATIONS = (
    ('res.groups', 'group_connect_admin', 'group_admin'),
    ('res.groups', 'group_connect_user', 'group_user'),
    ('res.groups', 'group_connect_webhook', 'group_webhook'),
    ('ir.module.category', 'module_connect_category', 'module_category_connect'),
    ('ir.ui.menu', 'connect_top_menu', 'menu_connect_root'),
    ('ir.ui.menu', 'connect_settings_menu', 'menu_connect_settings'),
    ('ir.ui.view', 'user_list', 'view_connect_user_tree'),
    ('ir.ui.view', 'connect_user_form', 'view_connect_user_form'),
    ('ir.ui.view', 'connect_call_form', 'view_connect_call_form'),
    ('ir.ui.view', 'connect_call_list', 'view_connect_call_tree'),
    ('ir.ui.view', 'connect_call_search', 'view_connect_call_search'),
    ('ir.ui.view', 'view_connect_sms_message_form', 'view_connect_message_form'),
    ('ir.ui.view', 'view_connect_sms_message_tree', 'view_connect_message_tree'),
    ('ir.actions.act_window', 'user_action', 'action_connect_user'),
    ('ir.actions.act_window', 'call_action', 'action_connect_call'),
    ('ir.actions.act_window', 'channel_action', 'action_connect_channel'),
    ('ir.actions.act_window', 'recording_action', 'action_connect_recording'),
    ('ir.actions.act_window', 'connect_debug_action', 'action_connect_debug'),
    ('ir.actions.act_window', 'connect_calls_action', 'action_connect_call_partner'),
    ('ir.actions.act_window', 'connect_messages_action', 'action_connect_message_partner'),
    ('ir.ui.view', 'view_partner_form', 'view_partner_form_connect'),
    ('ir.ui.view', 'recording_list', 'view_connect_recording_tree'),
    ('ir.ui.view', 'connect_recording_form', 'view_connect_recording_form'),
    ('ir.ui.view', 'connect_recording_search', 'view_connect_recording_search'),
    ('ir.ui.menu', 'users_menu', 'menu_connect_users'),
    ('ir.ui.menu', 'calls_menu', 'menu_connect_calls_list'),
    ('ir.ui.menu', 'channels_menu', 'menu_connect_channels'),
    ('ir.ui.menu', 'recordings_menu', 'menu_connect_recordings'),
    ('ir.ui.menu', 'connect_debug_messages_menu', 'menu_connect_debug'),
)


_REMOVED_PORTAL_VIEW_XMLIDS = (
    'portal_my_home_menu_connect_calls',
    'portal_my_home_connect_calls',
    'portal_my_calls',
    'portal_my_call',
)

_VIEW_REFERENCE_COLUMNS = (
    ('ir_ui_view', 'inherit_id'),
    ('ir_ui_view_custom', 'ref_id'),
    ('ir_act_window', 'view_id'),
    ('ir_act_window', 'search_view_id'),
    ('ir_act_window_view', 'view_id'),
)


def _reconcile_xmlid(cr, model, legacy_name, canonical_name):
    cr.execute(
        """
        SELECT name, model, res_id
          FROM ir_model_data
         WHERE module = 'connect'
           AND name IN %s
        """,
        ((legacy_name, canonical_name),),
    )
    records = {name: (record_model, res_id) for name, record_model, res_id in cr.fetchall()}
    for name, record in records.items():
        if record[0] != model:
            raise ValueError(
                'connect.%s has model %s; expected %s' % (name, record[0], model))
    legacy_id = records.get(legacy_name, (None, False))[1]
    canonical_id = records.get(canonical_name, (None, False))[1]
    if legacy_id and not canonical_id:
        cr.execute(
            """
            UPDATE ir_model_data
               SET name = %s
             WHERE module = 'connect'
               AND model = %s
               AND name = %s
            """,
            (canonical_name, model, legacy_name),
        )
        _logger.info(
            'renamed legacy connect.%s to canonical connect.%s',
            legacy_name, canonical_name,
        )
        return False
    if legacy_id and canonical_id and legacy_id != canonical_id:
        if model not in ('ir.actions.act_window', 'ir.ui.view', 'ir.ui.menu'):
            raise ValueError(
                'Cannot reconcile divergent %s identities connect.%s and connect.%s '
                'without merging their references' % (model, legacy_name, canonical_name))
        cr.execute(
            """
            UPDATE ir_model_data
               SET res_id = %s
             WHERE module = 'connect'
               AND model = %s
               AND name = %s
            """,
            (legacy_id, model, canonical_name),
        )
        _logger.info(
            'reconciled connect.%s onto legacy %s record %s',
            canonical_name, model, legacy_id,
        )
        return legacy_id, canonical_id
    return False


def _reconcile_action_menus(cr, action_pairs):
    for legacy_id, duplicate_id in action_pairs:
        cr.execute(
            """
            UPDATE ir_ui_menu
               SET action = %s
             WHERE action = %s
            """,
            (
                f'ir.actions.act_window,{legacy_id}',
                f'ir.actions.act_window,{duplicate_id}',
            ),
        )


def _reconcile_view_references(cr, view_pairs):
    for legacy_id, duplicate_id in view_pairs:
        # Precedent: /mnt/19/odoo/odoo/addons/base/models/ir_actions.py
        cr.execute(
            'UPDATE ir_act_window SET view_id = %s WHERE view_id = %s',
            (legacy_id, duplicate_id),
        )
        cr.execute(
            'UPDATE ir_act_window SET search_view_id = %s WHERE search_view_id = %s',
            (legacy_id, duplicate_id),
        )
        cr.execute(
            'UPDATE ir_act_window_view SET view_id = %s WHERE view_id = %s',
            (legacy_id, duplicate_id),
        )
        cr.execute(
            'UPDATE ir_ui_view SET inherit_id = %s WHERE inherit_id = %s',
            (legacy_id, duplicate_id),
        )
        cr.execute(
            'UPDATE ir_ui_view_custom SET ref_id = %s WHERE ref_id = %s',
            (legacy_id, duplicate_id),
        )


def _reconcile_menu_references(cr, menu_pairs):
    for legacy_id, duplicate_id in menu_pairs:
        cr.execute(
            'UPDATE ir_ui_menu SET parent_id = %s WHERE parent_id = %s',
            (legacy_id, duplicate_id),
        )



def _remove_legacy_aliases(cr, reconciliations=_XMLID_RECONCILIATIONS):
    for model, legacy_name, canonical_name in reconciliations:
        cr.execute(
            """
            SELECT name, res_id
              FROM ir_model_data
             WHERE module = 'connect'
               AND model = %s
               AND name IN %s
            """,
            (model, (legacy_name, canonical_name)),
        )
        records = dict(cr.fetchall())
        legacy_id = records.get(legacy_name)
        canonical_id = records.get(canonical_name)
        if not legacy_id:
            continue
        if not canonical_id:
            raise AssertionError(
                'legacy Connect XMLID connect.%s remains without canonical connect.%s'
                % (legacy_name, canonical_name)
            )
        if legacy_id != canonical_id:
            raise AssertionError(
                'legacy Connect XMLID connect.%s does not match canonical connect.%s'
                % (legacy_name, canonical_name)
            )
        cr.execute(
            """
            DELETE FROM ir_model_data
             WHERE module = 'connect'
               AND model = %s
               AND name = %s
            """,
            (model, legacy_name),
        )


def _retire_removed_portal_views(cr, xmlids=_REMOVED_PORTAL_VIEW_XMLIDS):
    cr.execute(
        """
        SELECT d.id, d.name, d.res_id
          FROM ir_model_data d
         WHERE d.module = 'connect'
           AND d.model = 'ir.ui.view'
           AND d.name IN %s
        """,
        (xmlids,),
    )
    records = cr.fetchall()
    if not records:
        return
    view_ids = tuple(record[2] for record in records)
    for table, column in _VIEW_REFERENCE_COLUMNS:
        cr.execute(
            'SELECT id FROM %s WHERE %s IN %%s LIMIT 1' % (table, column),
            (view_ids,),
        )
        reference = cr.fetchone()
        if reference:
            raise AssertionError(
                'removed Connect portal view %s is still referenced by %s.%s id %s'
                % (view_ids, table, column, reference[0])
            )
    cr.execute(
        """
        SELECT module, name, res_id
          FROM ir_model_data
         WHERE model = 'ir.ui.view'
           AND res_id IN %s
           AND NOT (module = 'connect' AND name IN %s)
        """,
        (view_ids, xmlids),
    )
    shared = cr.fetchone()
    if shared:
        raise AssertionError(
            'removed Connect portal view %s is also owned by %s.%s'
            % (shared[2], shared[0], shared[1])
        )
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'connect'
           AND model = 'ir.ui.view'
           AND name IN %s
        """,
        (xmlids,),
    )
    cr.execute('DELETE FROM ir_ui_view WHERE id IN %s', (view_ids,))


def _move_sms_composer_view(cr, legacy_name, canonical_name):
    cr.execute(
        """
        SELECT module, name, model, res_id
          FROM ir_model_data
         WHERE (module = 'connect' AND name = %s)
            OR (module = 'connect_twilio' AND name = %s)
        """,
        (legacy_name, canonical_name),
    )
    rows = {module: (name, model, res_id) for module, name, model, res_id in cr.fetchall()}
    for _name, model, _record_id in rows.values():
        if model != 'ir.ui.view':
            raise AssertionError('SMS composer XMLID must identify an ir.ui.view')
    legacy = rows.get('connect')
    canonical = rows.get('connect_twilio')
    if legacy and canonical:
        if legacy[2] != canonical[2]:
            _reconcile_view_references(cr, [(legacy[2], canonical[2])])
            cr.execute('UPDATE ir_ui_view SET active = false WHERE id = %s', (canonical[2],))
            cr.execute(
                "UPDATE ir_model_data SET res_id = %s WHERE module = 'connect_twilio' AND name = %s",
                (legacy[2], canonical[0]),
            )
        cr.execute(
            "DELETE FROM ir_model_data WHERE module = 'connect' AND name = %s",
            (legacy[0],),
        )
    elif legacy:
        cr.execute(
            """
            UPDATE ir_model_data
               SET module = 'connect_twilio', name = %s
             WHERE module = 'connect' AND name = %s
            """,
            (canonical_name, legacy[0]),
        )
    record = legacy or canonical
    if record:
        # Precedent: /mnt/19/odoo/odoo/addons/base/models/ir_ui_view.py
        # Development mode reads this source before XML import updates the view.
        cr.execute(
            """
            UPDATE ir_ui_view SET arch_fs = 'connect_twilio/wizard/sms_composer_views.xml'
             WHERE id = %s AND arch_fs = 'connect/wizard/sms_composer_views.xml'
            """,
            (record[2],),
        )


def migrate(cr, version):
    if not version:
        return
    _move_sms_composer_view(
        cr, 'custom_sms_composer_view_form', 'view_sms_composer_form_connect',
    )
    action_pairs = []
    view_pairs = []
    menu_pairs = []
    for model, legacy_name, canonical_name in _XMLID_RECONCILIATIONS:
        reconciled = _reconcile_xmlid(cr, model, legacy_name, canonical_name)
        if not reconciled:
            continue
        if model == 'ir.actions.act_window':
            action_pairs.append(reconciled)
        elif model == 'ir.ui.view':
            view_pairs.append(reconciled)
        elif model == 'ir.ui.menu':
            menu_pairs.append(reconciled)
    _reconcile_action_menus(cr, action_pairs)
    _reconcile_view_references(cr, view_pairs)
    _reconcile_menu_references(cr, menu_pairs)
    _remove_legacy_aliases(cr)
    _retire_removed_portal_views(cr)
    if view_pairs:
        cr.execute(
            'UPDATE ir_ui_view SET active = FALSE WHERE id IN %s',
            (tuple(duplicate_id for _, duplicate_id in view_pairs),),
        )
    if menu_pairs:
        cr.execute(
            'UPDATE ir_ui_menu SET active = FALSE WHERE id IN %s',
            (tuple(duplicate_id for _, duplicate_id in menu_pairs),),
        )
