"""Versioned, explicit AMC + NSE code + ISIN slot + plan/option mapping.

No name-only, ISIN-only, family-group, provider-serial or fuzzy fallback.
"""
from . import contract as c

SLOTS = frozenset({'isin_growth_or_div_payout', 'isin_div_reinvest'})
TARGET_KEYS = {'amc_code', 'scheme_code', 'isin', 'plan', 'option', 'isin_slot',
               'amfi_scheme_code', 'source_name', 'reinvestment_flag'}


def crosswalk(value):
    c.require(type(value) is dict and set(value) == {'format', 'version', 'amcs', 'targets'}
              and value['format'] == 'nse01-crosswalk-v1', 'INVALID_CROSSWALK')
    c.require(type(value['version']) is str and c.NAME.fullmatch(value['version']), 'INVALID_CROSSWALK')
    c.require(type(value['amcs']) is dict and len(value['amcs']) <= 500
              and all(type(k) is str and 0 < len(k) <= 100 and type(v) is str and 0 < len(v) <= 200
                      for k, v in value['amcs'].items()), 'INVALID_CROSSWALK')
    c.require(type(value['targets']) is list and len(value['targets']) <= c.MAX_ROWS, 'INVALID_CROSSWALK')
    for row in value['targets']:
        c.require(type(row) is dict and set(row) == TARGET_KEYS, 'INVALID_CROSSWALK')
        c.require(all(type(row[k]) is str and 0 < len(row[k]) <= 200 for k in TARGET_KEYS - {'amfi_scheme_code'}),
                  'INVALID_CROSSWALK')
        c.require(row['amc_code'] in value['amcs'] and row['plan'] in {'REGULAR', 'DIRECT'}
                  and row['option'] in {'GROWTH', 'IDCW', 'DIVIDEND', 'BONUS'}
                  and row['isin_slot'] in SLOTS and row['reinvestment_flag'] in {'0', '1'}
                  and type(row['amfi_scheme_code']) is int and 0 < row['amfi_scheme_code'] < 2**31,
                  'INVALID_CROSSWALK')
        c.require((row['isin_slot'] == 'isin_div_reinvest') == (row['reinvestment_flag'] == '1')
                  and (row['isin_slot'] != 'isin_div_reinvest' or row['option'] in {'IDCW', 'DIVIDEND'}),
                  'INVALID_CROSSWALK')
    return c.decode(c.canonical(value))


def resolve(row, walk, funds):
    cells = row['cells']
    result = {**row, 'status': 'UNMAPPED', 'row_reason': 'SCH_MAPPING', 'candidates': []}
    if not all(cells[i] for i in (1, 4, 5, 7, 8)):
        return {**result, 'row_reason': 'SCH_REQUIREDNESS'}
    if row['plan'] is None:
        return {**result, 'row_reason': 'SCH_PLAN'}
    if cells[5] not in walk['amcs']:
        return result
    targets = [t for t in walk['targets'] if t['amc_code'] == cells[5] and t['scheme_code'] == cells[1]]
    exact = [t for t in targets if t['isin'] == cells[4] and t['plan'] == row['plan']
             and t['source_name'] == cells[8] and t['reinvestment_flag'] == cells[27]]
    if not exact:
        return {**result, 'status': 'CONFLICT' if targets else 'UNMAPPED'}
    candidates = []
    conflict = False
    for target in exact:
        matching = [f for f in funds if f['amfi_scheme_code'] == target['amfi_scheme_code']
                    and f['amfi_amc_name'] == walk['amcs'][cells[5]]
                    and f[target['isin_slot']] == cells[4] and f['plan_type'] == row['plan']
                    and f['option_type'] == target['option']]
        conflict |= not bool(matching)
        for fund in matching:
            candidate = {'amfi_scheme_code': fund['amfi_scheme_code'], 'isin_slot': target['isin_slot'],
                         'option': target['option'], 'in_scope': fund['in_scope']}
            if candidate not in candidates:
                candidates.append(candidate)
    result['candidates'] = candidates
    if conflict:
        return {**result, 'status': 'CONFLICT'}
    if len(candidates) != 1:
        return {**result, 'status': 'AMBIGUOUS' if candidates else 'UNMAPPED'}
    if not candidates[0]['in_scope'] or row['plan'] != 'REGULAR':
        return {**result, 'status': 'OUT_OF_SCOPE', 'row_reason': 'SCH_SCOPE'}
    if row['normalized']['amc_active'] is not True:
        return {**result, 'status': 'SOURCE_DISABLED' if row['normalized']['amc_active'] is False else 'UNMAPPED',
                'row_reason': 'SCH_FLAGS_MODES'}
    return {**result, 'status': 'MAPPED', 'row_reason': None}
