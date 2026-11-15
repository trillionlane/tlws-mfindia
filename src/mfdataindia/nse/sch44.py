"""Observed SCH44 profile and file-only historical mapping report."""

from argparse import ArgumentParser
from collections import Counter, defaultdict
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
import re

from mfdataindia.enrichment.evidence import valid_isin
from . import contract as c, mapping
from .schema import HEADERS44

PROFILE = 'SCH-OBSERVED-44-DRY-RUN-v1'
CATALOGUE_FORMAT = 'nse01-mfdata-catalogue-v1'
EXPORT_CONTEXT_FORMAT = 'nse01-sch44-export-context-v1'
NUMBER = re.compile(r'(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)\Z')
INTEGER = re.compile(r'[0-9]+\Z')
DECIMALS = (12, 13, 14, 15, 19, 20, 21, 22, 23, 24, 35)
INTEGERS = (1, 41)
DATES = (36, 37, 43)
TIMES = (16, 25)
OBSERVED_TOKENS = {
    7: frozenset({'DEBT', 'EQUITY', 'ETF', 'LIQUID', 'OVERNIGHT'}),
    8: frozenset({'NORMAL'}),
    11: frozenset({'D', 'P', 'DP'}),
    18: frozenset({'D', 'P', 'DP'}),
    28: frozenset({'Y', 'N', 'Z'}),
    33: frozenset({'L0', 'L1', 'MF', 'T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7'}),
    **dict.fromkeys((10, 17, 27, 29, 30, 31, 32, 38, 40), frozenset({'Y', 'N'})),
}
# These columns are retained literally. The observations establish spelling and
# format but do not establish facility semantics or eligibility.
UNRESOLVED = {
    14: 'SCH44_ADDITIONAL_MAX_UNVERIFIED',
    16: 'SCH44_CUTOFF_UNVERIFIED',
    24: 'SCH44_REDEMPTION_MULTIPLIER_UNVERIFIED',
    25: 'SCH44_CUTOFF_UNVERIFIED',
    33: 'SCH44_SETTLEMENT_UNVERIFIED',
    34: 'SCH44_AMC_IND_UNVERIFIED',
    39: 'SCH44_EXIT_LOAD_UNVERIFIED',
    41: 'SCH44_LOCK_IN_UNVERIFIED',
    42: 'SCH44_CHANNEL_CODE_UNVERIFIED',
    43: 'SCH44_REOPENING_DATE_UNVERIFIED',
    44: 'SCH44_OPEN_CLOSE_UNVERIFIED',
}
FUND_KEYS = {'amfi_scheme_code', 'amfi_amc_name', 'plan_type', 'option_type',
             'in_scope', *mapping.SLOTS}
STATUSES = ('EXACT', 'UNMATCHED', 'AMBIGUOUS', 'OUT_OF_SCOPE')


def _check_datetime(value, fmt):
    try:
        return datetime.strptime(value, fmt).strftime(fmt) == value
    except ValueError:
        return False


def parse(raw):
    """Validate the observed layout; retain every cell and field hold."""
    c.require(type(raw) is bytes and 0 < len(raw) <= c.MAX_BYTES, 'INVALID_FILE')
    try:
        decoded = raw.decode('utf-8-sig')
    except UnicodeError:
        raise c.ImportError('INVALID_ENCODING') from None
    normalized = decoded.replace('\r\n', '\n')
    c.require('\r' not in normalized and '\ufeff' not in normalized, 'INVALID_ENCODING')
    lines = normalized.removesuffix('\n').split('\n')
    c.require(tuple(lines[0].split('|')) == HEADERS44, 'SCH44_INVALID_HEADER')
    c.require(1 <= len(lines) - 1 <= c.MAX_ROWS, 'INVALID_ROW_COUNT')

    rows = []
    for line in lines[1:]:
        c.require(0 < len(line.encode()) <= c.MAX_ROW_BYTES, 'INVALID_ROW_BYTES')
        cells = tuple(line.split('|'))
        c.require(len(cells) == len(HEADERS44), 'INVALID_COLUMN_COUNT')
        c.require(not any(ord(ch) < 32 or ord(ch) == 127 for ch in line), 'INVALID_CELL')
        for positions, grammar in ((DECIMALS, NUMBER), (INTEGERS, INTEGER)):
            c.require(all(not cells[i - 1] or grammar.fullmatch(cells[i - 1]) for i in positions),
                      'SCH44_INVALID_NUMBER')
        for i in DATES:
            value = cells[i - 1]
            c.require(value in {'', '00-00-0000'} or _check_datetime(value, '%d-%m-%Y'),
                      'SCH44_INVALID_DATE')
        for i in TIMES:
            value = cells[i - 1]
            c.require(not value or _check_datetime(value, '%H:%M:%S'), 'SCH44_INVALID_TIME')
        holds = dict(UNRESOLVED)
        for i, value in enumerate(cells, 1):
            if not value:
                holds.setdefault(i, 'SCH44_BLANK')
            elif i in OBSERVED_TOKENS and value not in OBSERVED_TOKENS[i]:
                holds[i] = 'SCH44_TOKEN_UNREVIEWED'
            elif (i in DATES and value == '00-00-0000') or (
                i in TIMES and value == '00:00:00'
            ):
                holds.setdefault(i, 'SCH44_SENTINEL')
        if cells[4] and not valid_isin(cells[4]):
            holds[5] = 'SCH44_ISIN_INVALID'
        holds.setdefault(8, 'SCH44_PLAN_NEEDS_CROSSWALK')
        if cells[27] == 'Z':
            holds[28] = 'SCH44_REINVESTMENT_TOKEN_UNRESOLVED'
        rows.append((cells, holds))
    return rows


def _json(raw, reason):
    c.require(type(raw) is bytes and 0 < len(raw) <= c.MAX_BYTES, reason)
    try:
        return json.loads(raw, object_pairs_hook=c.unique)
    except (ValueError, UnicodeError, RecursionError):
        raise c.ImportError(reason) from None


def catalogue(raw):
    """A dated snapshot of the exact columns used by the canonical SCH join."""
    snapshot = _json(raw, 'INVALID_CATALOGUE')
    c.require(type(snapshot) is dict and set(snapshot) == {
        'format', 'as_of', 'dataset_version', 'funds',
    } and snapshot['format'] == CATALOGUE_FORMAT, 'INVALID_CATALOGUE')
    _date(snapshot['as_of'])
    c.require(type(snapshot['dataset_version']) is str and 0 < len(snapshot['dataset_version']) <= 256
              and type(snapshot['funds']) is list and 0 < len(snapshot['funds']) <= c.MAX_ROWS,
              'INVALID_CATALOGUE')
    funds = {}
    for fund in snapshot['funds']:
        c.require(type(fund) is dict and set(fund) == FUND_KEYS, 'INVALID_CATALOGUE_ROW')
        code = fund['amfi_scheme_code']
        c.require(type(code) is int and 0 < code < 2**31 and code not in funds
                  and type(fund['in_scope']) is bool
                  and type(fund['amfi_amc_name']) is str and bool(fund['amfi_amc_name'])
                  and all(fund[key] is None or type(fund[key]) is str
                          for key in ('plan_type', 'option_type', *mapping.SLOTS)),
                  'INVALID_CATALOGUE_ROW')
        funds[code] = fund
    return snapshot, funds


def _date(value):
    try:
        c.require(type(value) is str and date.fromisoformat(value).isoformat() == value,
                  'INVALID_SOURCE_DATE')
    except ValueError:
        raise c.ImportError('INVALID_SOURCE_DATE') from None


def _resolve(cells, walk, targets, funds, candidates):
    scheme, isin, amc, plan, active, reinvest = (cells[i - 1] for i in (2, 5, 6, 8, 27, 28))
    if not scheme or not amc or not isin:
        return 'UNMATCHED', 'SOURCE_IDENTITY_MISSING'
    if not valid_isin(isin):
        return 'UNMATCHED', 'SOURCE_ISIN_INVALID'
    if plan != 'NORMAL':
        return 'UNMATCHED', 'SOURCE_PLAN_TOKEN_UNREVIEWED'
    if reinvest not in {'Y', 'N', 'Z', ''}:
        return 'UNMATCHED', 'SOURCE_REINVESTMENT_TOKEN_UNREVIEWED'
    if amc not in walk['amcs']:
        if len(candidates) > 1:
            return 'AMBIGUOUS', 'MULTIPLE_ISIN_SLOTS_WITHOUT_AMC_CROSSWALK'
        reason = 'AMC_CROSSWALK_MISSING' if candidates else 'NO_CANONICAL_ISIN_SLOT'
        return 'UNMATCHED', reason
    issuer = walk['amcs'][amc]
    if not targets:
        issuer_candidates = [code for code, _ in candidates if funds[code]['amfi_amc_name'] == issuer]
        if len(issuer_candidates) > 1:
            return 'AMBIGUOUS', 'MULTIPLE_ISSUER_ISIN_SLOTS_WITHOUT_SCHEME_CROSSWALK'
        return 'UNMATCHED', 'SCHEME_CROSSWALK_MISSING'

    matches = set()
    conflict = False
    expected = {'Y': '1', 'N': '0'}.get(reinvest)
    for target in targets:
        fund = funds.get(target['amfi_scheme_code'])
        if (fund is not None and fund['amfi_amc_name'] == issuer
                and fund[target['isin_slot']] == isin and fund['plan_type'] == target['plan']
                and fund['option_type'] == target['option']
                and (expected is None or target['reinvestment_flag'] == expected)):
            matches.add((target['amfi_scheme_code'], target['isin_slot']))
        else:
            conflict = True
    if len(matches) > 1:
        return 'AMBIGUOUS', 'MULTIPLE_EXACT_CROSSWALK_TARGETS'
    if not matches:
        return 'UNMATCHED', 'CROSSWALK_TARGET_DIFFERS'
    if conflict:
        return 'AMBIGUOUS', 'CONFLICTING_CROSSWALK_TARGETS'
    code, _ = next(iter(matches))
    if not funds[code]['in_scope'] or funds[code]['plan_type'] != 'REGULAR':
        return 'OUT_OF_SCOPE', 'MFDATA_SCOPE_OR_NONREGULAR'
    if active != 'Y':
        return 'OUT_OF_SCOPE', 'SOURCE_ACTIVITY_NOT_CONFIRMED'
    return 'EXACT', 'EXPLICIT_CROSSWALK_AND_EXACT_CANONICAL_SLOT'


def _export_context(value, source_sha256):
    c.require(type(value) is dict and set(value) == {
        'format', 'environment', 'report_type', 'member_id', 'source_sha256',
    } and value['format'] == EXPORT_CONTEXT_FORMAT, 'INVALID_EXPORT_CONTEXT')
    c.require(value['environment'] in ('PRODUCTION', 'UAT')
              and value['report_type'] == 'CONSOLIDATED_SCHEME_MASTER'
              and type(value['member_id']) is str
              and re.fullmatch(r'[0-9]{1,20}', value['member_id']), 'INVALID_EXPORT_CONTEXT')
    c.require(value['source_sha256'] == source_sha256, 'EXPORT_CONTEXT_SOURCE_DIFFERS')
    return {'environment': value['environment'], 'report_type': value['report_type'],
            'member_id_recorded': True}


def report(source, source_date, catalogue_bytes, walk, *, export_context=None):
    """Return private-safe counts; a candidate ISIN hit never proves a match."""
    _date(source_date)
    rows = parse(source)
    source_sha256 = sha256(source).hexdigest()
    context = (_export_context(export_context, source_sha256)
               if export_context is not None else None)
    snapshot, funds = catalogue(catalogue_bytes)
    mapping.validate_crosswalk(walk)
    by_isin = defaultdict(set)
    for code, fund in funds.items():
        for slot in mapping.SLOTS:
            if fund[slot] and valid_isin(fund[slot]):
                by_isin[fund[slot]].add((code, slot))
    by_source = defaultdict(list)
    for target in walk['targets']:
        by_source[target['amc_code'], target['scheme_code'], target['isin']].append(target)

    reasons = {status: Counter() for status in STATUSES}
    field_holds = Counter()
    candidate_counts = Counter()
    source_keys = Counter((cells[5], cells[1], cells[4], cells[7], cells[10], cells[17])
                          for cells, _ in rows)
    for cells, holds in rows:
        for position, reason in holds.items():
            field_holds[(position, reason)] += 1
        key = (cells[5], cells[1], cells[4], cells[7], cells[10], cells[17])
        candidates = by_isin.get(cells[4], ())
        count = len(candidates)
        candidate_counts['MULTIPLE' if count > 1 else 'UNIQUE' if count else 'NONE'] += 1
        if source_keys[key] > 1:
            status, reason = 'AMBIGUOUS', 'DUPLICATE_SOURCE_IDENTITY'
        else:
            status, reason = _resolve(cells, walk, by_source.get(key[:3], ()), funds, candidates)
        reasons[status][reason] += 1

    return {
        'profile': PROFILE, 'result': 'HISTORICAL_DRY_RUN', 'source_scope': 'UNVERIFIED',
        'source_date': source_date, 'source_sha256': source_sha256,
        'export_context': context,
        'source_bytes': len(source), 'source_rows': len(rows),
        'catalogue_as_of': snapshot['as_of'], 'catalogue_dataset_version': snapshot['dataset_version'],
        'catalogue_sha256': sha256(catalogue_bytes).hexdigest(), 'catalogue_rows': len(funds),
        'crosswalk_version': walk['version'], 'crosswalk_sha256': c.digest(walk),
        'crosswalk_amcs': len(walk['amcs']), 'crosswalk_targets': len(walk['targets']),
        'counts': {status: sum(values.values()) for status, values in reasons.items()},
        'reasons': {status: dict(values) for status, values in reasons.items()},
        'unverified_isin_candidates': dict(candidate_counts),
        'field_holds': [{'position': i, 'reason': reason, 'rows': count}
                        for (i, reason), count in sorted(field_holds.items())],
        'publication': 'NOT_ATTEMPTED',
    }


def _read(path, limit):
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    c.require(len(content) <= limit, 'INPUT_TOO_LARGE')
    return content


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-date', required=True, help='Recorded export date (YYYY-MM-DD)')
    parser.add_argument('--catalogue', type=Path, required=True, help='Dated canonical catalogue JSON')
    parser.add_argument('--crosswalk', type=Path, required=True, help='Reviewed AMC and scheme mappings')
    parser.add_argument('--export-context', type=Path,
                        help='Private settings from the public NSE export form')
    args = parser.parse_args(argv)
    try:
        context = None
        if args.export_context is not None:
            context = c.decode(_read(args.export_context, 4096))
            c.require(type(context) is dict, 'INVALID_EXPORT_CONTEXT')
        result = report(_read(args.source, c.MAX_BYTES), args.source_date,
                        _read(args.catalogue, c.MAX_BYTES),
                        _json(_read(args.crosswalk, c.MAX_BYTES), 'INVALID_CROSSWALK'),
                        export_context=context)
    except (OSError, c.ImportError) as error:
        parser.exit(2, 'SCH44_DRY_RUN: ' + (
            str(error) if isinstance(error, c.ImportError) else 'INPUT_UNAVAILABLE'
        ) + '\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
