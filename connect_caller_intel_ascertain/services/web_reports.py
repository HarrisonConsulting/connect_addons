import re
from urllib.parse import urlparse

from odoo.addons.connect_caller_intel.services.adapters.base import Adapter, AdapterError
from odoo.addons.connect_caller_intel.services.adapters.registry import ADAPTERS

SOURCE_CODE = 'web_reports'
METHOD_VERSION_XMLID = 'connect_caller_intel_ascertain.method_caller_web_reports_v1'
SEARCH_LIMIT = 10
MAX_BOARD_PAGES = 5

#: Domains of caller complaint boards; only these produce marks.
COMPLAINT_BOARDS = (
    '800notes.com',
    'whocallsme.com',
    'shouldianswer.com',
    'callercenter.com',
    'whocalledme.com',
    'nomorobo.com',
)

_SCAM_WORDS = ('scam', 'fraud', 'phishing')
_ROBO_WORDS = ('robocall', 'robo call', 'recorded message', 'auto dialer')


def board_of(url):
    """The complaint-board domain ``url`` belongs to, or None."""
    host = (urlparse(url or '').hostname or '').lower()
    for board in COMPLAINT_BOARDS:
        if host == board or host.endswith('.' + board):
            return board
    return None


def search_query(e164):
    """Query naming the number in its international and national formats."""
    digits = e164.lstrip('+')
    if e164.startswith('+1') and len(digits) == 11:
        area, exchange, line = digits[1:4], digits[4:7], digits[7:]
        return '"+1 %s %s %s" OR "%s-%s-%s" spam OR scam OR caller' % (
            area, exchange, line, area, exchange, line)
    return '"%s" spam OR scam OR caller' % e164


def number_pattern(e164):
    """Regex matching the number with any separators between its national digits."""
    digits = e164.lstrip('+')
    national = digits[1:] if e164.startswith('+1') and len(digits) == 11 else digits
    return re.compile(r'[\s().\-]*'.join(re.escape(d) for d in national))


def categorize(text):
    lowered = text.lower()
    if any(word in lowered for word in _SCAM_WORDS):
        return 'scam'
    if any(word in lowered for word in _ROBO_WORDS):
        return 'robocall'
    return 'spam'


class WebReports(Adapter):
    """Complaint-board reports on a number, researched as one ascertain inquiry.

    The search results are candidates. A page becomes a mark only when it sits on a
    known complaint board, is captured, and its text names the number.
    """

    code = SOURCE_CODE

    def lookup(self, env, e164):
        env = env(su=True)
        version = env.ref(METHOD_VERSION_XMLID)
        inquiry = env['modelnexus.ascertain.inquiry'].create({
            'name': 'Complaint-board reports for %s' % e164,
            'method_version_id': version.id,
        })
        inquiry.action_start()

        search = inquiry.web_search(search_query(e164), limit=SEARCH_LIMIT)
        if not search['ok']:
            inquiry.action_conclude(search['error'], outcome='unknown')
            raise AdapterError(search['error'])

        pattern = number_pattern(e164)
        marks, captures, seen = [], [], set()
        for candidate in search['results']:
            board = board_of(candidate['url'])
            if not board or board in seen or len(seen) >= MAX_BOARD_PAGES:
                continue
            seen.add(board)
            fetched = inquiry.capture_source(candidate['url'], allow_truncated=True)
            if not fetched['ok']:
                continue
            capture = fetched['capture']
            match = pattern.search(capture.text or '')
            if not match:
                continue
            claim = 'Reported on %s' % board
            inquiry.cite(capture, match.start(), match.end(), claim)
            domain = (urlparse(capture.final_url).hostname or board).lower()
            observed = capture.observed_at
            marks.append({
                'reporter': domain,
                'category': categorize(capture.text),
                'report_count': 1,
                'first_reported': observed,
                'last_reported': observed,
                'evidence_url': capture.final_url,
                'external_ref': 'web:%s' % board,
                'inquiry_id': inquiry.id,
                'capture_id': capture.id,
            })
            captures.append(capture)

        if marks:
            inquiry.action_conclude(
                'Reported on %s.' % ', '.join(sorted(m['reporter'] for m in marks)))
        else:
            inquiry.action_conclude(
                'No complaint-board page names %s.' % e164, outcome='not_found')
        return {
            'found': bool(marks),
            'marks': marks,
            'inquiry_id': inquiry.id,
            'capture_id': captures[0].id if captures else False,
        }


ADAPTERS[SOURCE_CODE] = WebReports()
