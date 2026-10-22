# -*- coding: utf-8 -*-
"""Database cleanup for connect_voicetel 19.0.4.0.0.

connect_voicetel 19.0.4.0.0 replaces two things a database may still
carry: the retired 19.0.3.x shim of this module (and, before it, the
19.0.2.x / 1.2.x VoiceTel module) and connect_voiceml, which is no longer
in the repository. Every step here:

- runs raw SQL only: the pre-migration runs before this module's models
  are in the registry;
- is guarded by to_regclass / information_schema, so a missing table or
  column is a logged no-op;
- is idempotent: a second run matches nothing;
- never drops data that is not already copied somewhere else. Legacy
  tables are renamed, legacy columns are dropped only after they are
  copied into the archive tables.

The 19.0.4.0.0 pre-migration runs the whole sequence; post_init_hook
runs the connect_voiceml cleanup on fresh installs.
"""

import hashlib
import logging

from odoo.tools import parse_version

from .tools import API_HOST_ZONE, in_zone, normalize_host

_logger = logging.getLogger(__name__)

# Columns the pre-retirement module added to core tables. Copied into the
# archive tables before they are dropped (the 19.0.3.0.0 shim's list).
ARCHIVES = {
    'connect_settings': (
        'connect_voicetel_settings_archive',
        (
            'voicetel_account_sid',
            'voicetel_api_key',
            'display_voicetel_api_key',
            'voicetel_rest_host',
            'voicetel_wss_url',
            'voicetel_softphone',
            'voicetel_verify_requests',
            'voicetel_auto_sync',
            'rest_provider',
        ),
    ),
    'connect_user': (
        'connect_voicetel_user_archive',
        (
            'voicetel_username',
            'voicetel_sid',
            'voicetel_password',
            'voicetel_domain',
            'voicetel_sip_enabled',
            'voicetel_client_enabled',
            'voicetel_sip_ring_timeout',
            'voicetel_client_ring_timeout',
            'voicetel_application',
            'voicetel_exten',
            'voicetel_exten_number',
            'voicetel_outgoing_callerid',
            'originate_provider',
            'message_provider',
        ),
    ),
}

# Records the pre-retirement module owned that name removed fields or
# models. Menus first: they point at the actions. A view takes its
# inherit tree with it.
OWNED_RECORDS = (
    ('ir.ui.menu', 'ir_ui_menu'),
    ('ir.ui.view', 'ir_ui_view'),
    ('ir.actions.act_window', 'ir_act_window'),
    ('ir.actions.server', 'ir_act_server'),
    ('ir.cron', 'ir_cron'),
    ('ir.rule', 'ir_rule'),
    ('ir.model.access', 'ir_model_access'),
    ('ir.filters', 'ir_filters'),
)

# Provider selections that named the old module's 'voicetel' key.
PROVIDER_COLUMNS = (
    ('connect_settings', ('originate_provider', 'message_provider', 'rest_provider')),
    ('connect_user', ('originate_provider', 'message_provider')),
)

LEGACY_PREFIX = 'connect_voicetel_legacy_'
ARCHIVE_SUFFIX = '_archive'
# Below this installed version the database still runs the pre-retirement
# module: its columns are live and nothing was archived yet.
SHIM_VERSION = '19.0.3.0.0'

# ICP keys of the pre-retirement module that 19.0.4.0.0 does not read.
DEAD_ICP_KEYS = ('connect.voicetel_wss_url', 'connect.voicetel_softphone')


# ---------------------------------------------------------------- helpers

def _table_exists(cr, table):
    cr.execute("SELECT to_regclass(%s) IS NOT NULL", (_quoted(table),))
    return cr.fetchone()[0]


def _quoted(name):
    return '"{}"'.format(name.replace('"', '""'))


def _existing_columns(cr, table, columns):
    cr.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = current_schema()"
        " AND table_name = %s AND column_name IN %s",
        (table, tuple(columns)),
    )
    found = {row[0] for row in cr.fetchall()}
    return [c for c in columns if c in found]


def _table_columns(cr, table):
    cr.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    )
    return {row[0] for row in cr.fetchall()}


def _relation_exists(cr, name):
    cr.execute(
        "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = current_schema() AND c.relname = %s",
        (name,),
    )
    return bool(cr.fetchone())


def _free_name(cr, wanted):
    """``wanted`` cut to PostgreSQL's 63-byte limit and unused in the schema."""
    name = wanted
    if len(name) > 63:
        digest = hashlib.sha1(wanted.encode()).hexdigest()[:8]
        name = '{}_{}'.format(wanted[:54], digest)
    candidate, n = name, 1
    while _relation_exists(cr, candidate):
        suffix = '_{}'.format(n)
        candidate = name[:63 - len(suffix)] + suffix
        n += 1
    return candidate


def _icp_get(cr, key):
    cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (key,))
    row = cr.fetchone()
    return row[0] if row else None


def _icp_set(cr, key, value):
    cr.execute(
        "UPDATE ir_config_parameter SET value = %s, write_date = now() at time zone 'UTC'"
        " WHERE key = %s",
        (value, key),
    )
    if not cr.rowcount:
        cr.execute(
            "INSERT INTO ir_config_parameter"
            " (key, value, create_uid, write_uid, create_date, write_date)"
            " VALUES (%s, %s, 1, 1, now() at time zone 'UTC', now() at time zone 'UTC')",
            (key, value),
        )


def _current_tables(cr):
    """Tables and m2m relation tables of the models in the live registry.

    During an upgrade the pre-migration runs before this module's models
    are loaded, so a connect_voicetel table found here belongs to a model
    of another module and one not found here is legacy. Run again on a
    loaded registry (tests), the module's own tables are protected.
    """
    from odoo.modules.registry import Registry
    if cr.dbname not in Registry.registries:
        return set()
    registry = Registry.registries[cr.dbname]
    tables = set()
    for model in registry.models.values():
        if model._abstract or not model._auto:
            continue
        tables.add(model._table)
        for field in model._fields.values():
            if field.type == 'many2many' and field.store and getattr(field, 'relation', None):
                tables.add(field.relation)
    return tables


# ---------------------------------------------------- P1: legacy live module

def archive_legacy_columns(cr):
    """Copy the pre-retirement columns into the archive tables.

    Skipped for an archive that already exists: the shim (or an earlier
    run) took that copy.
    """
    for source, (archive, columns) in ARCHIVES.items():
        if _table_exists(cr, archive):
            _logger.info('connect_voicetel: %s already exists, copy skipped', archive)
            continue
        present = _existing_columns(cr, source, columns)
        if not any(c.startswith(('voicetel_', 'display_voicetel_')) for c in present):
            _logger.info('connect_voicetel: no VoiceTel columns on %s', source)
            continue
        select = ', '.join(['id'] + [_quoted(c) for c in present])
        cr.execute('CREATE TABLE {} AS SELECT {} FROM {}'.format(
            _quoted(archive), select, _quoted(source)))
        _logger.info(
            'connect_voicetel: copied %s rows, columns %s, from %s to %s',
            cr.rowcount, ', '.join(present), source, archive)


def drop_owned_records(cr):
    """Delete the UI records the pre-retirement module owned.

    Dependents re-validate their parent views before the end-of-update
    cleanup and fail on a stale child that names a removed field.
    """
    for model, table in OWNED_RECORDS:
        if not _table_exists(cr, table):
            continue
        cr.execute(
            "SELECT res_id FROM ir_model_data"
            " WHERE module = 'connect_voicetel' AND model = %s"
            " AND res_id IS NOT NULL", (model,))
        ids = [row[0] for row in cr.fetchall()]
        if ids:
            if model == 'ir.ui.view':
                cr.execute(
                    "WITH RECURSIVE tree AS ("
                    " SELECT id FROM ir_ui_view WHERE id IN %s"
                    " UNION SELECT v.id FROM ir_ui_view v"
                    " JOIN tree t ON v.inherit_id = t.id)"
                    " SELECT id FROM tree", (tuple(ids),))
                ids = [row[0] for row in cr.fetchall()]
            cr.execute('DELETE FROM {} WHERE id IN %s'.format(_quoted(table)), (tuple(ids),))
            _logger.info(
                'connect_voicetel: deleted %s %s records of the retired module',
                cr.rowcount, model)
        cr.execute(
            "DELETE FROM ir_model_data"
            " WHERE module = 'connect_voicetel' AND model = %s", (model,))


def clear_provider_selections(cr):
    """NULL selections and tool adapters that name the old 'voicetel' key."""
    for table, columns in PROVIDER_COLUMNS:
        for column in _existing_columns(cr, table, columns):
            cr.execute(
                'UPDATE {t} SET {c} = NULL WHERE {c} = %s'.format(
                    t=_quoted(table), c=_quoted(column)),
                ('voicetel',))
            _logger.info(
                'connect_voicetel: cleared %s %s.%s rows', cr.rowcount, table, column)
    if len(_existing_columns(cr, 'connect_tool', ('adapter', 'active'))) == 2:
        cr.execute(
            "UPDATE connect_tool SET adapter = NULL, active = FALSE WHERE adapter = %s",
            ('voicetel',))
        _logger.info('connect_voicetel: retired %s connect_tool rows', cr.rowcount)
    cr.execute(
        "DELETE FROM ir_config_parameter WHERE key IN %s AND value = %s",
        (('rest_provider', 'connect.rest_provider'), 'voicetel'))
    _logger.info(
        'connect_voicetel: removed %s rest_provider config parameters', cr.rowcount)


def drop_archived_columns(cr):
    """Drop the legacy voicetel_* columns that the archive holds a copy of.

    Only columns present in the archive table are dropped, so the new
    fields are created clean (no FK to a renamed legacy table, no stale
    NOT NULL) and nothing is lost.
    """
    for source, (archive, columns) in ARCHIVES.items():
        if not _table_exists(cr, archive):
            continue
        archived = _table_columns(cr, archive)
        legacy = [
            c for c in _existing_columns(cr, source, columns)
            if c.startswith(('voicetel_', 'display_voicetel_')) and c in archived
        ]
        for column in legacy:
            cr.execute('ALTER TABLE {} DROP COLUMN {} CASCADE'.format(
                _quoted(source), _quoted(column)))
        if legacy:
            _logger.info(
                'connect_voicetel: dropped archived columns %s from %s',
                ', '.join(legacy), source)


def migrate_legacy_module(cr):
    archive_legacy_columns(cr)
    drop_owned_records(cr)
    clear_provider_selections(cr)
    drop_archived_columns(cr)


# ------------------------------------------------------ P2: legacy tables

def _legacy_candidates(cr):
    cr.execute(
        "SELECT c.relname FROM pg_class c"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = current_schema() AND c.relkind = 'r'"
        " AND c.relname LIKE '%%connect\\_voicetel\\_%%'"
        " ORDER BY c.relname")
    current = _current_tables(cr)
    return [
        name for (name,) in cr.fetchall()
        if not name.endswith(ARCHIVE_SUFFIX)
        and LEGACY_PREFIX not in name
        and name not in current
    ]


def _legacy_name(table):
    if table.startswith('connect_voicetel_'):
        return LEGACY_PREFIX + table[len('connect_voicetel_'):]
    return LEGACY_PREFIX + table


def rename_legacy_tables(cr):
    """Rename every connect_voicetel table no current model owns.

    Odoo leaves the table of a model that left the registry (ir.model's
    _drop_table only drops tables of loaded models), so the retired
    module's tables and its m2m relation tables are still there. Their
    names, sequences and index names would collide with this version's
    models. Rename them, never drop; then drop the foreign keys that
    other tables still point at them with, so new rows are not checked
    against legacy data.
    """
    renamed = []
    for table in _legacy_candidates(cr):
        target = _free_name(cr, _legacy_name(table))
        cr.execute('SELECT count(*) FROM {}'.format(_quoted(table)))
        rows = cr.fetchone()[0]
        cr.execute('ALTER TABLE {} RENAME TO {}'.format(_quoted(table), _quoted(target)))
        # Index (PK/UNIQUE included) and sequence names live in the schema
        # namespace and keep the old table's name after a rename.
        cr.execute(
            "SELECT i.relname FROM pg_index x"
            " JOIN pg_class i ON i.oid = x.indexrelid"
            " WHERE x.indrelid = to_regclass(%s)", (_quoted(target),))
        for (index,) in cr.fetchall():
            cr.execute('ALTER INDEX {} RENAME TO {}'.format(
                _quoted(index), _quoted(_free_name(cr, 'vtlegacy_' + index))))
        cr.execute(
            "SELECT s.relname FROM pg_class s"
            " JOIN pg_depend d ON d.objid = s.oid AND d.deptype IN ('a', 'i')"
            " WHERE s.relkind = 'S' AND d.refobjid = to_regclass(%s)",
            (_quoted(target),))
        for (sequence,) in cr.fetchall():
            cr.execute('ALTER SEQUENCE {} RENAME TO {}'.format(
                _quoted(sequence), _quoted(_free_name(cr, 'vtlegacy_' + sequence))))
        _logger.info(
            'connect_voicetel: renamed legacy table %s (%s rows) to %s',
            table, rows, target)
        renamed.append(target)
    if not renamed:
        _logger.info('connect_voicetel: no legacy connect_voicetel tables to rename')
    drop_foreign_keys_to_legacy(cr)
    return renamed


def drop_foreign_keys_to_legacy(cr):
    cr.execute(
        "SELECT src.relname, con.conname, dst.relname"
        " FROM pg_constraint con"
        " JOIN pg_class src ON src.oid = con.conrelid"
        " JOIN pg_class dst ON dst.oid = con.confrelid"
        " JOIN pg_namespace n ON n.oid = dst.relnamespace"
        " WHERE con.contype = 'f' AND n.nspname = current_schema()"
        " AND dst.relname LIKE %s AND src.relname NOT LIKE %s",
        (LEGACY_PREFIX.replace('_', '\\_') + '%', LEGACY_PREFIX.replace('_', '\\_') + '%'))
    for src, conname, dst in cr.fetchall():
        cr.execute('ALTER TABLE {} DROP CONSTRAINT {}'.format(_quoted(src), _quoted(conname)))
        _logger.warning(
            'connect_voicetel: dropped foreign key %s.%s that pointed at legacy table %s',
            src, conname, dst)


# ------------------------------------------------ P3: connect_voiceml row

def remove_voiceml_module(cr):
    """Remove connect_voiceml's module row; it is no longer in the repo.

    Refuses when it is installed: that database holds live VoiceML data
    and must uninstall connect_voiceml first.
    """
    cr.execute("SELECT id, state FROM ir_module_module WHERE name = 'connect_voiceml'")
    row = cr.fetchone()
    if not row:
        _logger.info('connect_voicetel: no connect_voiceml module row')
        return False
    module_id, state = row
    if state in ('installed', 'to upgrade', 'to remove'):
        raise Exception(
            'connect_voiceml is %s on this database. connect_voicetel '
            '19.0.4.0.0 replaces it and the module is no longer shipped: '
            'uninstall connect_voiceml first, then upgrade connect_voicetel.'
            % state)
    cr.execute("DELETE FROM ir_module_module_dependency WHERE module_id = %s", (module_id,))
    deps = cr.rowcount
    if _table_exists(cr, 'ir_module_module_exclusion'):
        cr.execute("DELETE FROM ir_module_module_exclusion WHERE module_id = %s", (module_id,))
    cr.execute(
        "DELETE FROM ir_model_data WHERE (module = 'base' AND name = 'module_connect_voiceml')"
        " OR module = 'connect_voiceml'")
    xmlids = cr.rowcount
    cr.execute(
        "DELETE FROM ir_config_parameter WHERE key LIKE %s",
        ('connect.voiceml\\_%',))
    icps = cr.rowcount
    cr.execute("DELETE FROM ir_module_module WHERE id = %s", (module_id,))
    _logger.info(
        'connect_voicetel: removed the %s connect_voiceml module row '
        '(%s dependencies, %s xmlids, %s config parameters)',
        state, deps, xmlids, icps)
    return True


# ------------------------------------------------------ P4/P5: settings

def carry_settings(cr):
    """rest_host -> api_host (normalised, allowlisted); drop dead keys.

    account_sid, api_key and display_api_key are the same keys this
    version reads and are left alone.
    """
    rest_host = _icp_get(cr, 'connect.voicetel_rest_host')
    api_host = _icp_get(cr, 'connect.voicetel_api_host')
    if rest_host is not None and api_host is None:
        host = normalize_host(rest_host)
        if host and in_zone(host, API_HOST_ZONE):
            _icp_set(cr, 'connect.voicetel_api_host', host)
            _logger.info('connect_voicetel: API host %s carried over from rest_host', host)
        else:
            _logger.warning(
                'connect_voicetel: rest_host %r is not a *.%s host and was not '
                'carried over; the default API host applies',
                rest_host, API_HOST_ZONE)
    if rest_host is not None:
        cr.execute("DELETE FROM ir_config_parameter WHERE key = 'connect.voicetel_rest_host'")
    cr.execute("DELETE FROM ir_config_parameter WHERE key IN %s", (DEAD_ICP_KEYS,))
    if cr.rowcount:
        _logger.info('connect_voicetel: removed %s unused legacy config parameters', cr.rowcount)


def force_safe_flags(cr):
    """No remote changes during the upgrade; webhooks stay signed.

    The first sync is a deliberate admin action.
    """
    _icp_set(cr, 'connect.voicetel_auto_sync', 'False')
    _icp_set(cr, 'connect.voicetel_verify_requests', 'True')
    _logger.info('connect_voicetel: auto sync forced off, request verification on')


# --------------------------------------------------------- entry points

def pre_migrate(cr, version):
    if not version:
        return
    if parse_version(version) < parse_version(SHIM_VERSION):
        _logger.info(
            'connect_voicetel: %s predates the retirement release; '
            'archiving its columns', version)
        migrate_legacy_module(cr)
    rename_legacy_tables(cr)
    remove_voiceml_module(cr)
    carry_settings(cr)
    force_safe_flags(cr)


def post_migrate(cr, version):
    if not version:
        return
    for archive in ('connect_voicetel_settings_archive', 'connect_voicetel_user_archive'):
        if _table_exists(cr, archive):
            cr.execute('SELECT count(*) FROM {}'.format(_quoted(archive)))
            _logger.info('connect_voicetel: %s keeps %s rows', archive, cr.fetchone()[0])
    cr.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = current_schema() AND c.relkind = 'r' AND c.relname LIKE %s",
        (LEGACY_PREFIX.replace('_', '\\_') + '%',))
    legacy = [row[0] for row in cr.fetchall()]
    _logger.info(
        'connect_voicetel: legacy tables kept: %s', ', '.join(legacy) or 'none')
    _logger.info(
        'connect_voicetel: API host %s, auto sync %s, verify requests %s',
        _icp_get(cr, 'connect.voicetel_api_host') or '(default)',
        _icp_get(cr, 'connect.voicetel_auto_sync'),
        _icp_get(cr, 'connect.voicetel_verify_requests'))


def post_init_hook(env):
    """Fresh install: drop the connect_voiceml row this version replaces."""
    remove_voiceml_module(env.cr)
