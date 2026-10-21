import csv
import io

from ..heuristics import IDENTITY_CONFIDENCE
from .base import Adapter, AdapterError
from .wikidata import INSERT_PAGE, skip_for_privacy, to_e164

EXTRACT_PARAM = 'connect_caller_intel.osm_extract'
MAX_IDENTITIES = 3
OSM_TYPES = ('node', 'way', 'relation')


class OsmPhone(Adapter):
    """Places that OpenStreetMap tags with a phone number, matched from a periodic extract.

    The extract is a CSV with the columns ``phone, osm_type, osm_id, name, lat, lon`` (``phone``
    may hold several numbers separated by ``;``). ``connect_caller_intel.osm_extract`` holds its
    file path or URL. The public Overpass API is never called.
    """

    code = 'osm'
    nanp_only = True

    def applies_to(self, env, rep):
        return super().applies_to(env, rep) and not skip_for_privacy(env, rep)

    def credential(self, env):
        return bool(env['connect.caller.osm'].sudo().search_count([], limit=1))

    def key_hint(self):
        return 'ir.config_parameter %s (extract file or URL), then the monthly import cron' % EXTRACT_PARAM

    def lookup(self, env, e164):
        rows = env['connect.caller.osm'].sudo().search([('e164', '=', e164)], limit=MAX_IDENTITIES)
        identities = [{
            'name': row.name, 'kind': 'business',
            'url': 'https://www.openstreetmap.org/%s/%s' % (row.osm_type, row.osm_id),
            'external_ref': '%s/%s' % (row.osm_type, row.osm_id),
            'confidence': IDENTITY_CONFIDENCE['osm'],
        } for row in rows if row.name]
        return {'found': bool(identities), 'identities': identities}

    def import_extract(self, env):
        """Replace ``connect.caller.osm`` with the configured extract. Returns the row count.

        Returns 0 and changes nothing when no extract is configured or it holds no usable row.
        """
        location = env['ir.config_parameter'].sudo().get_param(EXTRACT_PARAM)
        if not location:
            return 0
        entries = {}
        for row in csv.DictReader(io.StringIO(self._read_extract(location))):
            osm_type, osm_id, name = row.get('osm_type'), row.get('osm_id'), (row.get('name') or '').strip()
            if osm_type not in OSM_TYPES or not (osm_id or '').isdigit() or not name:
                continue
            for raw in (row.get('phone') or '').split(';'):
                e164 = to_e164(raw, 'US')
                if e164 and e164.startswith('+1'):
                    entries[(e164, osm_type, int(osm_id))] = (
                        name, _coordinate(row.get('lat')), _coordinate(row.get('lon')))
        if not entries:
            return 0
        env.cr.execute('DELETE FROM connect_caller_osm')
        keys = sorted(entries)
        for start in range(0, len(keys), INSERT_PAGE):
            env.cr.execute_values(
                'INSERT INTO connect_caller_osm (e164, osm_type, osm_id, name, lat, lon) VALUES %s',
                [key + entries[key] for key in keys[start:start + INSERT_PAGE]])
        env['connect.caller.osm'].invalidate_model()
        return len(keys)

    def _read_extract(self, location):
        if location.startswith(('http://', 'https://')):
            return self._get_text(location)
        try:
            with open(location, encoding='utf-8', newline='') as handle:
                return handle.read()
        except OSError as exc:
            raise AdapterError('OSM extract %s is unreadable: %s' % (location, exc)) from exc


def _coordinate(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
