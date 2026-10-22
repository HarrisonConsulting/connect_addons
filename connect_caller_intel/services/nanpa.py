"""NANPA central office code files: download locations and line parsing. No ORM."""
from datetime import datetime

BASE_URL = 'https://reports.nanpa.com/public/'
UTILIZED_URL = BASE_URL + 'CoCodeAssignment_Utilized_AllStates_Public.zip'
AVAILABLE_REGIONS = (
    'CENTRAL_NORTH', 'CENTRAL_SOUTH', 'EASTERN_ATLANTIC', 'EASTERN_NORTH',
    'EASTERN_SOUTH', 'WESTERN_NORTH', 'WESTERN_PACIFIC', 'WESTERN_SOUTH',
)
AVAILABLE_URLS = tuple(
    '%sCoCodeAssignment_Available_%s.zip' % (BASE_URL, region) for region in AVAILABLE_REGIONS)

USE_STATUS = {'AS': 'assigned', 'UA': 'unavailable'}
_YES = frozenset({'Y', 'YES'})


def _clean(value):
    return (value or '').strip().strip('"').strip()


def _flag(value):
    return _clean(value).upper() in _YES


def _date(value):
    value = _clean(value)
    if not value:
        return None
    try:
        return datetime.strptime(value, '%m/%d/%Y').date()
    except ValueError:
        return None


def _code(value):
    """Six-digit NPA-NXX from ``601-605``, or None when the cell is not a code."""
    digits = _clean(value).replace('-', '')
    return digits if len(digits) == 6 and digits.isdigit() else None


def _cells(lines):
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode('utf-8', errors='replace')
        line = line.rstrip('\r\n')
        if not line.strip() or line.lstrip().startswith('State'):
            continue
        yield [_clean(cell) for cell in line.split('\t')]


def _base(cells):
    code = _code(cells[1]) if len(cells) > 1 else None
    if not code:
        return None
    return {'npa_nxx': code, 'npa': code[:3], 'nxx': code[3:], 'state_code': cells[0]}


def parse_utilized(lines):
    """Rows of the utilized file: State, NPA-NXX, OCN, Company, RateCenter, EffectiveDate,
    Use, AssignDate, Initial/Growth, Pooled Code, In Service."""
    for cells in _cells(lines):
        row = _base(cells)
        status = USE_STATUS.get(cells[6].upper()) if len(cells) > 6 else None
        if not row or not status:
            continue
        cells += [''] * (11 - len(cells))
        row.update(
            status=status, ocn=cells[2], company=cells[3], rate_center=cells[4],
            effective_date=_date(cells[5]), assign_date=_date(cells[7]),
            pooled=_flag(cells[9]), in_service=_flag(cells[10]))
        yield row


def parse_available(lines):
    """Rows of an available file: State, NPA-NXX."""
    for cells in _cells(lines):
        row = _base(cells)
        if not row:
            continue
        row.update(
            status='available', ocn='', company='', rate_center='',
            effective_date=None, assign_date=None, pooled=False, in_service=False)
        yield row


PARSERS = {'utilized': parse_utilized, 'available': parse_available}
