"""Reconcile retained production SCH44 evidence offline and emit a private review report."""

from argparse import ArgumentParser
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import unicodedata

from mfdataindia.enrichment.evidence import valid_isin
from mfdataindia.ingest.amfi_navall import parse_navall

from . import contract as c, sch44
from .mapping import SLOTS

PROFILE = 'nse-sch44-production-link-review-v2'
GROWTH_SLOT = 'isin_growth_or_div_payout'
REINVEST_SLOT = 'isin_div_reinvest'
DISAGREEMENT = 'SOURCE_FLAG_SLOT_DISAGREEMENT'
VALID_PLANS = {'REGULAR', 'DIRECT'}
VALID_OPTIONS = {'GROWTH', 'IDCW', 'DIVIDEND', 'BONUS'}


def _source_key(cells):
    return cells[5], cells[1], cells[4]


def _source_index(rows):
    keys, codes = defaultdict(list), defaultdict(set)
    for line, (cells, _) in enumerate(rows, 2):
        key = _source_key(cells)
        keys[key].append((line, cells[7]))
        codes[key[:2]].add(key[2])
    return keys, codes


def _issuer_key(value):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize(
        'NFKC', value.removesuffix('_MF'),
    ).casefold()).removesuffix('mutualfund').removesuffix('mf')


def _catalogue(raw):
    # The private snapshot may carry a display name. Names never enter the join.
    snapshot = sch44._json(raw, 'INVALID_CATALOGUE')
    c.require(type(snapshot) is dict and type(snapshot.get('funds')) is list, 'INVALID_CATALOGUE')
    for fund in snapshot['funds']:
        c.require(type(fund) is dict, 'INVALID_CATALOGUE_ROW')
        if 'scheme_name' in fund:
            c.require(type(fund.pop('scheme_name')) is str, 'INVALID_CATALOGUE_ROW')
    return sch44.catalogue(c.canonical(snapshot))


def _amfi_index(raw):
    c.require(type(raw) is bytes and 0 < len(raw) <= c.MAX_BYTES, 'INVALID_AMFI_EVIDENCE')
    try:
        records, _ = parse_navall(raw.decode('utf-8-sig'))
    except UnicodeError:
        raise c.ImportError('INVALID_AMFI_EVIDENCE') from None
    c.require(bool(records), 'INVALID_AMFI_EVIDENCE')
    codes, isins = defaultdict(list), defaultdict(set)
    for record in records:
        code = int(record.amfi_scheme_code)
        codes[code].append(record)
        for slot, isin in ((GROWTH_SLOT, record.isin_div_payout_or_growth),
                           (REINVEST_SLOT, record.isin_div_reinvestment)):
            if isin:
                isins[isin].add((code, slot))
    return codes, isins


def _amfi_witness(index, code, slot, isin, fund):
    codes, isins = index
    if isins.get(isin) != {(code, slot)}:
        return None
    records = codes[code]
    periods = {row.periodicity for row in records}
    if len(periods) != 1 or any(
        (row.amc, row.isin_div_payout_or_growth, row.isin_div_reinvestment,
         row.plan_type, row.option, row.plan_source, row.option_source)
        != (fund['amfi_amc_name'], fund[GROWTH_SLOT], fund[REINVEST_SLOT],
            fund['plan_type'], fund['option_type'], 'COLUMN', 'COLUMN')
        for row in records
    ):
        return None
    return {'lines': [row.line_no for row in records], 'periodicity': next(iter(periods))}


def _resolve(row, cells, source_indexes, funds, issuers, candidates, amfi):
    key = _source_key(cells)
    issuer, scheme, isin = key
    if not valid_isin(isin):
        return 'UNMATCHED', 'INVALID_SOURCE_ISIN'
    flag = cells[27]
    slot = None
    disagrees = False
    if len(candidates) == 1:
        code, slot = next(iter(candidates))
        disagrees = flag in {'Y', 'N'} and (flag == 'Y') != (slot == REINVEST_SLOT)
        row['candidate_slot'] = slot
        row['flag_slot_disagreement'] = DISAGREEMENT if disagrees else None
    current, current_codes, earlier, earlier_codes = source_indexes
    if len(current[key]) != 1 or len(current_codes[key[:2]]) != 1:
        return 'AMBIGUOUS', 'NON_UNIQUE_CURRENT_NSE_SCHEME_KEY'
    if not candidates:
        return 'UNMATCHED', 'NO_CATALOGUE_ISIN_SLOT'
    if slot is None:
        return 'AMBIGUOUS', 'NON_UNIQUE_CATALOGUE_ISIN_SLOT'
    hold = 'AMBIGUOUS' if disagrees else 'UNMATCHED'
    fund = funds[code]
    issuer_key = _issuer_key(issuer)
    if not issuer_key or issuers.get(issuer_key) != {fund['amfi_amc_name']}:
        return hold, 'ISSUER_NOT_CORROBORATED'
    previous = earlier.get(key, ())
    if len(previous) > 1 or len(earlier_codes.get(key[:2], ())) > 1:
        return 'AMBIGUOUS', 'NON_UNIQUE_EARLIER_NSE_SCHEME_KEY'
    if not scheme:
        return hold, 'NSE_SCHEME_KEY_NOT_CORROBORATED'
    # NSE scheme codes identify source routes, not AMFI variants. A current
    # unique route may be new; adverse retained history still cannot be waived.
    if earlier_codes.get(key[:2]) and earlier_codes[key[:2]] != {isin}:
        return hold, 'NSE_SCHEME_CODE_HISTORY_CONFLICT'
    earlier_line, earlier_plan = previous[0] if previous else (None, None)
    if cells[7] != 'NORMAL' or (previous and earlier_plan != 'NORMAL') or flag not in {'Y', 'N', 'Z', ''}:
        return hold, 'UNVERIFIED_NSE_TOKEN'
    if fund['plan_type'] not in VALID_PLANS or fund['option_type'] not in VALID_OPTIONS:
        return hold, 'CATALOGUE_PLAN_OR_OPTION_NOT_ESTABLISHED'

    witness = [_amfi_witness(index, code, slot, isin, fund) for index in amfi]
    if any(item is None for item in witness) or witness[0]['periodicity'] != witness[1]['periodicity']:
        return hold, 'EXPLICIT_AMFI_VARIANT_NOT_CORROBORATED'
    if cells[26] not in {'Y', 'N'}:
        return hold, 'NSE_ACTIVITY_NOT_ESTABLISHED'
    row.update(
        amfi_code=code, amfi_isin_slot=slot, amfi_issuer=fund['amfi_amc_name'],
        amfi_plan=fund['plan_type'], amfi_option=fund['option_type'],
        earlier_nse_line=earlier_line, amfi_witnesses=witness,
    )
    if not fund['in_scope'] or fund['plan_type'] != 'REGULAR':
        return 'OUT_OF_SCOPE', 'VERIFIED_VARIANT_EXCLUDED_BY_MFDATA_SCOPE'
    if cells[26] == 'N':
        return 'OUT_OF_SCOPE', 'VERIFIED_VARIANT_WITH_INACTIVE_NSE_AMC_FLAG'
    return 'EXACT', 'INDEPENDENT_VARIANT_EVIDENCE_CONFIRMED'


def report(source, catalogue, earlier_source, amfi_first, amfi_second):
    """Use local files only. Output is review evidence and cannot authorize publication."""
    c.require(source != earlier_source, 'NSE_EVIDENCE_MUST_BE_DISTINCT')
    c.require(amfi_first != amfi_second, 'AMFI_EVIDENCE_MUST_BE_DISTINCT')
    rows = sch44.parse(source)
    source_indexes = (*_source_index(rows), *_source_index(sch44.parse(earlier_source)))
    snapshot, funds = _catalogue(catalogue)
    amfi = (_amfi_index(amfi_first), _amfi_index(amfi_second))
    by_isin, issuers = defaultdict(set), defaultdict(set)
    for code, fund in funds.items():
        issuers[_issuer_key(fund['amfi_amc_name'])].add(fund['amfi_amc_name'])
        for slot in SLOTS:
            if fund[slot] and valid_isin(fund[slot]):
                by_isin[fund[slot]].add((code, slot))

    detail, counts, disagreements = [], Counter(), Counter()
    for line, (cells, _) in enumerate(rows, 2):
        issuer, scheme, isin = _source_key(cells)
        row = {'source_line': line, 'nse_amc_code': issuer, 'nse_scheme_code': scheme,
               'isin': isin, 'source_reinvestment_flag': cells[27], 'amfi_isin_slot': None,
               'flag_slot_disagreement': None}
        candidates = by_isin.get(isin, ())
        status, reason = _resolve(row, cells, source_indexes, funds, issuers, candidates, amfi)
        row.update(status=status, reason=reason)
        detail.append(row)
        counts[status] += 1
        if row['flag_slot_disagreement']:
            disagreements[status] += 1
    return {
        'profile': PROFILE, 'publication': 'NOT_ATTEMPTED', 'source_scope': 'UNVERIFIED',
        'effective_date': None, 'catalogue_version': snapshot['dataset_version'],
        'catalogue_as_of': snapshot['as_of'],
        'input_sha256': {label: sha256(raw).hexdigest() for label, raw in (
            ('source', source), ('catalogue', catalogue), ('earlier_source', earlier_source),
            ('amfi_first', amfi_first), ('amfi_second', amfi_second),
        )},
        'counts': {status: counts[status] for status in sch44.STATUSES},
        'flag_slot_disagreement_counts': {status: disagreements[status] for status in sch44.STATUSES},
        'rows': detail,
    }


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    inputs = ('source', 'catalogue', 'earlier-source', 'amfi-first', 'amfi-second')
    for name in inputs:
        parser.add_argument(f'--{name}', type=Path, required=True)
    args = vars(parser.parse_args(argv))
    try:
        result = report(*(sch44._read(args[name.replace('-', '_')], c.MAX_BYTES) for name in inputs))
    except (OSError, c.ImportError) as error:
        reason = str(error) if isinstance(error, c.ImportError) else 'INPUT_UNAVAILABLE'
        parser.exit(2, f'SCH44_RECONCILE: {reason}\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
