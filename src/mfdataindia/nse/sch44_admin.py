"""Export the pinned, read-only SCH44 review queue for MFData Admin."""

from argparse import ArgumentParser
from collections import defaultdict
from hashlib import sha256
import os
from pathlib import Path

from mfdataindia.enrichment.evidence import valid_isin

from . import contract, sch44, sch44_reconcile
from .mapping import SLOTS

SOURCE_SHA = '407e9642d2cf5a006237268f5912641b998f90a46e52b4ed934ac3ef5cecc2f4'
CATALOGUE_SHA = '1c0aa0f1fd4acfdb4d659af34183a3ddb8524da2cc8f71ff9ebda7ca0b06782c'
QUEUE_SHA = '0bb44d8c3e7532f3cc62c93a737a49d254fe0510c5f8166519dd2a1327b1fd4b'
COLLATERAL_SHA = '26b55110c34b909d6662daa64170eca987ba765ac58afea925f1d1ff73257779'
COMPARISON_SHA = '06d94a00ee620b77a36936641ec0f3a9cf42371df7dc5cdf96df2884cceb648b'
MEMBERSHIP_SHA = '1e88be6908047645dc163bf63acf25d884e7531a5a21673c95a4e823f24888ef'
COVERAGE = {'EXACT': 11976, 'AMBIGUOUS': 167, 'UNMATCHED': 1716, 'OUT_OF_SCOPE': 17}


def _digest(raw):
    return sha256(raw).hexdigest()


def export(source, catalogue, queue, collateral=None):
    contract.require((_digest(source), _digest(catalogue), _digest(queue)) ==
                     (SOURCE_SHA, CATALOGUE_SHA, QUEUE_SHA), 'PINNED_ADMIN_INPUT_DIFFERS')
    rows = sch44.parse(source)
    _, funds = sch44_reconcile._catalogue(catalogue)
    kept = sch44._json(queue, 'QUEUE_DIFFERS')
    contract.require(kept['coverage'] == COVERAGE and len(kept['rows']) == 1883, 'QUEUE_DIFFERS')
    membership = {}
    comparison = None
    if collateral is not None:
        contract.require(tuple(map(_digest, collateral)) == (COMPARISON_SHA, MEMBERSHIP_SHA),
                         'COLLATERAL_COMPARISON_HASH_DIFFERS')
        comparison, membership = map(contract.decode, collateral)
        contract.require(comparison['approved_list']['sha256'] == COLLATERAL_SHA
                         and comparison['nse']['sha256'] == SOURCE_SHA
                         and comparison['unresolved']['input_sha256'] == QUEUE_SHA
                         and set(membership) == {str(row['source_line']) for row in kept['rows']}
                         and all(type(value) is bool for value in membership.values())
                         and sum(membership.values()) == comparison['unresolved']['listed_rows'],
                         'COLLATERAL_MEMBERSHIP_DIFFERS')
    candidates = defaultdict(list)
    for code, fund in funds.items():
        for slot in sorted(SLOTS):
            isin = fund[slot]
            if isin and valid_isin(isin):
                candidates[isin].append({'amfi_code': code, 'amfi_issuer': fund['amfi_amc_name'],
                                         'amfi_plan': fund['plan_type'], 'amfi_option': fund['option_type'],
                                         'amfi_isin_slot': slot})
    result = []
    for row in kept['rows']:
        line = row['source_line']
        cells, _ = rows[line - 2]
        contract.require((cells[5], cells[1], cells[4], cells[27]) ==
                         tuple(row[key] for key in ('nse_amc_code', 'nse_scheme_code', 'isin',
                                                   'source_reinvestment_flag')), 'QUEUE_ROW_DIFFERS')
        result.append({**row, 'nse_scheme_name': cells[8],
                       'candidates': candidates.get(row['isin'], []),
                       'approved_collateral': membership.get(str(line))})
    return {'format': 'mfdata-admin-conflicts-v1', 'catalogue_version': 86, 'source_sha256': SOURCE_SHA,
            'queue_sha256': QUEUE_SHA, 'coverage': COVERAGE,
            'collateral': ({'sha256': COLLATERAL_SHA, 'purpose': 'NSE Clearing collateral acceptance',
                            'workbook_modified_utc': comparison['approved_list']['workbook_modified_utc']}
                           if comparison else None),
            'rows': result}


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    for flag in ('source', 'catalogue', 'queue', 'output'):
        parser.add_argument('--' + flag, type=Path, required=True)
    for flag in ('comparison', 'membership'):
        parser.add_argument('--' + flag, type=Path)
    args = parser.parse_args(argv)
    if (args.comparison is None) != (args.membership is None):
        parser.error('supply both comparison and membership or neither')
    collateral = None if args.comparison is None else (
        sch44._read(args.comparison, contract.MAX_BYTES),
        sch44._read(args.membership, contract.MAX_BYTES))
    result = export(*(sch44._read(path, contract.MAX_BYTES)
                      for path in (args.source, args.catalogue, args.queue)), collateral)
    with open(args.output, 'xb', opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
        stream.write(contract.canonical(result) + b'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
