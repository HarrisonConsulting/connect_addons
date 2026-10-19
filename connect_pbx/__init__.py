import json
import logging

from odoo.tools import SQL
from odoo.tools.sql import table_columns, table_exists

from . import controllers
from . import models
from . import wizard

_logger = logging.getLogger(__name__)


# Written by connect/migrations/19.0.4.5.2 when connect_pbx installs over a
# database that already holds its noupdate records.
INSTALL_KEEP_TABLE = '_connect_pbx_install_keep'


def restore_install_keep(env):
    """Write back the noupdate rows the install-mode data load overwrote.

    Init mode rewrites existing noupdate records with their shipped values.
    The upgrade snapshot holds what the database had before; restoring every
    snapshotted column gives the install the same outcome an update-mode
    load would have had. Returns the number of rows restored.
    """
    cr = env.cr
    if not table_exists(cr, INSTALL_KEEP_TABLE):
        return 0
    cr.execute(SQL(
        """
        SELECT d.model, d.res_id, k.data
          FROM %s k
          JOIN ir_model_data d ON d.id = k.imd_id AND d.res_id = k.res_id
        """,
        SQL.identifier(INSTALL_KEEP_TABLE),
    ))
    restored = 0
    columns_by_table = {}
    for model, res_id, data in cr.fetchall():
        Model = env.get(model)
        if Model is None or Model._abstract:
            continue
        table = Model._table
        if table not in columns_by_table:
            columns_by_table[table] = set(table_columns(cr, table))
        columns = sorted(
            name for name in data
            if name != 'id' and name in columns_by_table[table]
        )
        if not columns:
            continue
        idents = SQL(', ').join(SQL.identifier(name) for name in columns)
        try:
            with cr.savepoint():
                cr.execute(SQL(
                    "UPDATE %s SET (%s) = (SELECT %s FROM jsonb_populate_record(NULL::%s, %s::jsonb)) WHERE id = %s",
                    SQL.identifier(table), idents, idents,
                    SQL.identifier(table), json.dumps(data), res_id,
                ))
            restored += cr.rowcount
        except Exception as e:
            _logger.warning('could not restore %s(%s) after install: %s', model, res_id, e)
    cr.execute(SQL('DROP TABLE %s', SQL.identifier(INSTALL_KEEP_TABLE)))
    env.invalidate_all()
    env.registry.clear_cache()
    _logger.info('restored %s noupdate rows kept through the connect_pbx install', restored)
    return restored


def post_init_hook(env):
    restore_install_keep(env)
    _refresh_reachability(env)


def _refresh_reachability(env):
    """Seed connect.audio.is_reachable after install/upgrade.

    XML data loads go through the ORM — the referrer mixin catches them —
    but only once their module finishes loading. Calling the BFS at the end
    of install/upgrade means fresh databases and upgraded instances both
    land with is_reachable correctly populated across every audio + reference,
    rather than waiting for the next routing write to trigger it.
    """
    try:
        env['connect.audio']._refresh_reachability()
    except Exception as e:
        _logger.warning('post_init reachability refresh failed: %s', e)
