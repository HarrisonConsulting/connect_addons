from .base import Adapter, parse_dt

ENDPOINT = 'https://opendata.fcc.gov/resource/3xyp-aqkj.json'
UNWANTED_ISSUES = (
    'Robocalls',
    'Unwanted Calls',
    'Telemarketing (incl. do not call & spoofing)',
)


def national_dashed(e164):
    """NANP +1NXXNXXXXXX as the dataset writes it: NXX-NXX-XXXX."""
    digits = e164.lstrip('+')
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return '%s-%s-%s' % (digits[:3], digits[3:6], digits[6:])


class FccComplaints(Adapter):
    code = 'fcc_complaints'
    batch = False
    nanp_only = True

    def lookup(self, env, e164):
        number = national_dashed(e164)
        if not number:
            return {'found': False, 'marks': []}
        issues = ','.join("'%s'" % issue.replace("'", "''") for issue in UNWANTED_ISSUES)
        where = "caller_id_number='%s' AND issue in(%s)" % (number, issues)
        rows = self._get(ENDPOINT, params={
            '$select': 'date_created,issue,method',
            '$where': where,
            '$order': 'date_created',
            '$limit': 5000,
        })
        if not rows:
            return {'found': False, 'marks': []}
        dates = [parse_dt(row.get('date_created')) for row in rows]
        dates = [d for d in dates if d]
        category = 'robocall' if any(r.get('issue') == 'Robocalls' for r in rows) else 'spam'
        return {'found': True, 'marks': [{
            'reporter': 'FCC consumer complaints',
            'category': category,
            'report_count': len(rows),
            'first_reported': min(dates) if dates else None,
            'last_reported': max(dates) if dates else None,
            'evidence_url': 'https://opendata.fcc.gov/resource/3xyp-aqkj.json?caller_id_number=' + number,
            'external_ref': 'fcc:' + e164,
        }]}
