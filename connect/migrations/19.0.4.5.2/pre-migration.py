"""Keep the PBX noupdate records when connect_pbx installs over old data.

A database upgrading from the monolithic connect installs connect_pbx in the
same run. Two things go wrong unless this runs first:

1. Any record connect_pbx's data files declare whose xmlid is still owned by
   ``connect`` (``schedule_us_business_hours`` was missed by 1.32.0) is not
   found under ``connect_pbx`` and is created a second time.
2. connect_pbx loads in init mode, and init mode writes noupdate records
   that already exist (``odoo/tools/convert.py`` only skips noupdate nodes
   when mode != 'init'; ``BaseModel._load_records`` is called with
   update=False, so the ir_model_data noupdate flag is not consulted).
   Schedules, holidays, prompts, voices, voicemail stages and the park cron
   would all be reset to their shipped values.

So: re-own to connect_pbx every xmlid that connect_pbx's data files declare
and that connect still owns (the list is read from the files, never typed),
leaving noupdate as it is; then, when connect_pbx is being installed in this
run, snapshot the rows behind its noupdate xmlids. connect_pbx's
post_init_hook writes the snapshot back after its data load and drops it.
"""

import logging
import os

from lxml import etree

from odoo.modules.module import get_manifest, get_module_path
from odoo.tools.sql import table_exists

_logger = logging.getLogger(__name__)

OLD_MODULE = 'connect'
NEW_MODULE = 'connect_pbx'
KEEP_TABLE = '_connect_pbx_install_keep'
XML_TAGS = ('record', 'menuitem', 'template')


def declared_xmlids(module):
    """Return {name: noupdate} for every xmlid ``module``'s data files declare.

    Only ids owned by ``module`` count: a bare id, or one prefixed with the
    module itself. ``other_module.name`` overrides another module's record.
    Returns None when the module is not on the addons path.
    """
    path = get_module_path(module, display_warning=False)
    if not path:
        return None
    names = {}
    for rel in get_manifest(module).get('data', ()):
        if not rel.endswith('.xml'):
            continue
        root = etree.parse(os.path.join(path, rel)).getroot()
        for el in root.iter(*XML_TAGS):
            xid = el.get('id')
            if not xid:
                continue
            prefix, _dot, name = xid.rpartition('.')
            if prefix and prefix != module:
                continue
            noupdate = False
            for node in [el, *el.iterancestors()]:
                if node.tag in ('odoo', 'openerp', 'data') and node.get('noupdate') is not None:
                    noupdate = node.get('noupdate') in ('1', 'True', 'true')
                    break
            names[name] = noupdate
    return names


def reown(cr, names):
    """Move connect-owned rows for ``names`` to connect_pbx; noupdate untouched."""
    if not names:
        return 0
    cr.execute(
        """
        UPDATE ir_model_data d
           SET module = %s
         WHERE d.module = %s
           AND d.name IN %s
           AND NOT EXISTS (
                SELECT 1 FROM ir_model_data p
                 WHERE p.module = %s AND p.name = d.name)
        RETURNING d.name
        """,
        (NEW_MODULE, OLD_MODULE, tuple(names), NEW_MODULE),
    )
    moved = sorted(row[0] for row in cr.fetchall())
    _logger.info('connect_pbx: re-owned %s xmlids: %s', len(moved), ', '.join(moved))
    cr.execute(
        """
        SELECT d.name FROM ir_model_data d
         WHERE d.module = %s AND d.name IN %s
        """,
        (OLD_MODULE, tuple(names)),
    )
    clashes = [row[0] for row in cr.fetchall()]
    if clashes:
        _logger.warning(
            'connect_pbx: xmlids owned by both connect and connect_pbx, left '
            'in place: %s', ', '.join(sorted(clashes)))
    return len(moved)


def snapshot(cr, names):
    """Copy the rows behind connect_pbx's noupdate xmlids into KEEP_TABLE."""
    cr.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {KEEP_TABLE} (
            imd_id integer PRIMARY KEY,
            model varchar NOT NULL,
            res_id integer NOT NULL,
            data jsonb NOT NULL
        )
        """
    )
    cr.execute(f"DELETE FROM {KEEP_TABLE}")
    if not names:
        return 0
    cr.execute(
        """
        SELECT id, model, res_id FROM ir_model_data
         WHERE module = %s AND name IN %s AND res_id IS NOT NULL
        """,
        (NEW_MODULE, tuple(names)),
    )
    kept = 0
    for imd_id, model, res_id in cr.fetchall():
        table = model.replace('.', '_')
        if not table_exists(cr, table):
            _logger.warning('connect_pbx: no table %s for %s, not kept', table, model)
            continue
        cr.execute(
            f"""
            INSERT INTO {KEEP_TABLE} (imd_id, model, res_id, data)
            SELECT %s, %s, t.id, to_jsonb(t) FROM "{table}" t WHERE t.id = %s
            """,
            (imd_id, model, res_id),
        )
        kept += cr.rowcount
    _logger.info('connect_pbx: kept %s noupdate rows for the install', kept)
    return kept


def migrate(cr, version):
    if not version:
        return
    declared = declared_xmlids(NEW_MODULE)
    if declared is None:
        _logger.info('connect_pbx is not on the addons path; nothing to re-own')
        return
    # Never take a name connect itself still declares.
    for name in declared_xmlids(OLD_MODULE) or ():
        declared.pop(name, None)
    reown(cr, list(declared))

    cr.execute("SELECT state FROM ir_module_module WHERE name = %s", (NEW_MODULE,))
    row = cr.fetchone()
    if not row or row[0] != 'to install':
        # Already installed: its noupdate data loads in update mode and is
        # skipped. Not being installed: a snapshot would go stale.
        return
    snapshot(cr, [name for name, noupdate in declared.items() if noupdate])
