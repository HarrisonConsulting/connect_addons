import io
import logging
import tempfile
import zipfile
from datetime import timedelta

import requests

from odoo import _, fields, models, tools

from ..services import heuristics, nanpa
from ..services.adapters.base import polite_user_agent
from .reputation_refresh import JOB_CHANNEL

_logger = logging.getLogger(__name__)

BATCH_SIZE = 5000
DOWNLOAD_TIMEOUT = 120
LOADED_PARAM = 'connect_caller_intel.nanpa_loaded_at'
INSERT_SQL = """
    INSERT INTO connect_caller_nxx (
        npa_nxx, npa, nxx, state_code, status, in_service, ocn, company, rate_center, pooled,
        effective_date, assign_date, origin, loaded_at,
        create_uid, write_uid, create_date, write_date)
    VALUES %s
    ON CONFLICT (npa_nxx) DO UPDATE SET
        state_code = EXCLUDED.state_code, status = EXCLUDED.status,
        in_service = EXCLUDED.in_service, ocn = EXCLUDED.ocn, company = EXCLUDED.company,
        rate_center = EXCLUDED.rate_center, pooled = EXCLUDED.pooled,
        effective_date = EXCLUDED.effective_date, assign_date = EXCLUDED.assign_date,
        origin = EXCLUDED.origin, loaded_at = EXCLUDED.loaded_at,
        write_uid = EXCLUDED.write_uid, write_date = EXCLUDED.write_date
"""


class ConnectCallerNxx(models.Model):
    _name = 'connect.caller.nxx'
    _description = 'Caller Numbering Exchange'
    _rec_name = 'npa_nxx'
    _order = 'npa_nxx'

    _npa_nxx_uniq = models.Constraint('UNIQUE (npa_nxx)', 'An exchange is listed once.')

    npa_nxx = fields.Char(
        size=6, required=True, index=True,
        help='NANP area code and exchange, six digits, e.g. 601555.')
    npa = fields.Char(size=3, index=True, help='Area code of the exchange.')
    nxx = fields.Char(size=3, help='Exchange (central office code) within the area code.')
    state_code = fields.Char(
        help='US state or territory the exchange serves, per the numbering administrator.')
    status = fields.Selection(
        [('assigned', 'Assigned'), ('unavailable', 'Unavailable'), ('available', 'Available')],
        help='NANPA status: assigned to a carrier, unavailable for assignment, or available. '
             'A number in an available exchange cannot exist.')
    in_service = fields.Boolean(
        help='The administrator reports the assigned exchange as in service.')
    ocn = fields.Char(
        help='Operating Company Number of the code holder. Pooling and porting make it '
             'approximate per number.')
    company = fields.Char(
        help='Code holder named by the administrator. The number may have been ported since.')
    rate_center = fields.Char(help='Rate centre abbreviation the exchange is assigned to.')
    locality = fields.Char(help='Readable rate centre name from Local Calling Guide, when known.')
    lata = fields.Char(help='LATA of the exchange, from Local Calling Guide.')
    pooled = fields.Boolean(
        help='The exchange is split into thousands-blocks among carriers.')
    effective_date = fields.Date(help='Date the assignment took effect.')
    assign_date = fields.Date(help='Date the administrator assigned the code.')
    origin = fields.Selection(
        [('nanpa', 'NANPA'), ('lcg', 'Local Calling Guide')], required=True, default='nanpa',
        help='Which dataset created this row.')
    loaded_at = fields.Datetime(
        index=True,
        help='Bulk load that last confirmed this row. Rows from older loads are removed.')
    lcg_checked_at = fields.Datetime(
        help='When Local Calling Guide was last asked about this exchange. Cached for 30 days.')

    @tools.ormcache()
    def _loaded_npas(self):
        """Area codes the NANPA load has covered; an exchange is only "unlisted" inside them."""
        self.env.cr.execute(
            "SELECT DISTINCT npa FROM connect_caller_nxx WHERE origin = 'nanpa' AND npa IS NOT NULL")
        return frozenset(npa for (npa,) in self.env.cr.fetchall())

    def _lookup_facts(self, npa_nxx):
        """Exchange facts for the heuristics, from one indexed read; ``{}`` when the area code is not loaded."""
        if npa_nxx[:3] not in self._loaded_npas():
            return {}
        row = self.sudo().search_fetch(
            [('npa_nxx', '=', npa_nxx)],
            ['status', 'in_service', 'state_code', 'ocn', 'company', 'rate_center', 'locality'],
            limit=1)
        loaded = self.env['ir.config_parameter'].sudo().get_param(LOADED_PARAM)
        loaded_at = fields.Datetime.to_datetime(loaded) if loaded else None
        stale_after = timedelta(days=heuristics.DEFAULTS['nxx_stale_days'])
        stale = not loaded_at or loaded_at < fields.Datetime.now() - stale_after
        return {
            'status': row.status if row else 'unlisted',
            'in_service': bool(row and row.in_service),
            'stale': stale,
            'row': row,
        }

    def _load_nanpa_file(self, fileobj, kind, stamp):
        """Upsert one NANPA file (``utilized`` or ``available``) in batches, stamped ``stamp``."""
        uid = self.env.uid
        count = 0
        batch = []
        for item in nanpa.PARSERS[kind](fileobj):
            batch.append((
                item['npa_nxx'], item['npa'], item['nxx'], item['state_code'], item['status'],
                item['in_service'], item['ocn'], item['company'], item['rate_center'],
                item['pooled'], item['effective_date'], item['assign_date'], 'nanpa', stamp,
                uid, uid, stamp, stamp))
            if len(batch) >= BATCH_SIZE:
                self.env.cr.execute_values(INSERT_SQL, batch, page_size=BATCH_SIZE)
                count += len(batch)
                batch = []
        if batch:
            self.env.cr.execute_values(INSERT_SQL, batch, page_size=BATCH_SIZE)
            count += len(batch)
        return count

    def _load_nanpa(self, files, stamp=None):
        """Load every ``(fileobj, kind)`` under one stamp, then drop NANPA rows the files no longer list."""
        stamp = stamp or fields.Datetime.now()
        count = sum(self._load_nanpa_file(fileobj, kind, stamp) for fileobj, kind in files)
        self.env.flush_all()
        self.env.cr.execute(
            "DELETE FROM connect_caller_nxx WHERE origin = 'nanpa' AND loaded_at < %s", [stamp])
        self.env['ir.config_parameter'].sudo().set_param(LOADED_PARAM, fields.Datetime.to_string(stamp))
        self.env.invalidate_all()
        self.env.registry.clear_cache()
        return count

    def _download_nanpa(self, url, spool):
        """Stream ``url`` into ``spool`` and return the text lines of the single file in the zip."""
        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT,
                          headers={'User-Agent': polite_user_agent(self.env)}) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1 << 16):
                spool.write(chunk)
        spool.flush()
        spool.seek(0)
        archive = zipfile.ZipFile(spool)
        member = next(name for name in archive.namelist() if name.lower().endswith('.txt'))
        return io.TextIOWrapper(archive.open(member), encoding='utf-8', errors='replace', newline='')

    def _cron_load_nanpa(self):
        """Fetch the NANPA utilized and available files and replace the NANPA rows with them."""
        targets = [(nanpa.UTILIZED_URL, 'utilized')] + [(url, 'available') for url in nanpa.AVAILABLE_URLS]
        spools = []
        try:
            files = []
            for url, kind in targets:
                spool = tempfile.TemporaryFile()
                spools.append(spool)
                files.append((self._download_nanpa(url, spool), kind))
            return self._load_nanpa(files)
        except (requests.RequestException, zipfile.BadZipFile, StopIteration, OSError) as exc:
            _logger.warning('NANPA load failed, table unchanged: %s', exc)
            return 0
        finally:
            for spool in spools:
                spool.close()

    def action_load_nanpa(self):
        """Queue a NANPA load for an administrator."""
        self.with_delay(
            channel=JOB_CHANNEL, identity_key='caller_intel:nanpa_load',
            description='Load NANPA central office codes')._cron_load_nanpa()
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'message': _('NANPA load queued.'), 'type': 'info', 'sticky': False},
        }


class ConnectChannel(models.Model):
    _inherit = 'connect.channel'

    def _caller_intel_extra_facts(self, facts_num, rep, first_sight):
        """Add the NANPA exchange facts and numbering values for US and Caribbean numbers."""
        extra_facts, vals = super()._caller_intel_extra_facts(facts_num, rep, first_sight)
        if facts_num.get('calling_code') != 1 or not facts_num.get('npanxx'):
            return extra_facts, vals
        found = self.env['connect.caller.nxx']._lookup_facts(facts_num['npanxx'])
        if not found:
            return extra_facts, vals
        row = found['row']
        extra_facts = dict(extra_facts, nxx={
            'status': found['status'], 'in_service': found['in_service'], 'stale': found['stale']})
        if row:
            vals = dict(vals, **{
                'rate_center': row.locality or row.rate_center,
                'numbering_state': row.state_code,
                'ocn': row.ocn,
                'ocn_company': row.company,
                'nxx_status': row.status,
            })
        return extra_facts, vals
