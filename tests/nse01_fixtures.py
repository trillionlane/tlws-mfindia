"""Synthetic fixtures only; manifests here do not certify any original export."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from mfdataindia.nse import parser
from mfdataindia.nse.fixture import FULL_MANIFEST_JSON, FULL_SOURCE

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/nse01'
RAW = FULL_SOURCE
MANIFEST = json.loads(FULL_MANIFEST_JSON)
WALK = json.loads((FIXTURES / 'crosswalk.json').read_bytes())


def write_full_fixture(root):
    folder = root / 'approved'
    folder.mkdir(exist_ok=True)
    source = folder / 'sch43_full.psv'
    manifest = folder / 'sch43_full.manifest.json'
    source.write_bytes(RAW)
    manifest.write_bytes(FULL_MANIFEST_JSON)
    return source, manifest


def candidate(raw=RAW, **metadata):
    manifest = {**deepcopy(MANIFEST), 'expected_sha256': sha256(raw).hexdigest(),
                'expected_bytes': len(raw), 'expected_rows': len(raw.decode('utf-8-sig').splitlines())-1,
                **metadata}
    return parser.parse(raw, manifest)


def change(*, row=1, column=8, value='FIXTURE CHANGED', raw=RAW):
    lines = raw.decode().splitlines()
    cells = lines[row].split('|')
    cells[column] = value
    lines[row] = '|'.join(cells)
    return ('\n'.join(lines)+'\n').encode()


def funds():
    return [
        {'amfi_scheme_code': 1, 'amfi_amc_name': 'Verified fixture', 'plan_type': 'REGULAR',
         'option_type': 'GROWTH', 'in_scope': True, 'isin_growth_or_div_payout': 'INFX00000001', 'isin_div_reinvest': None},
        {'amfi_scheme_code': 2, 'amfi_amc_name': 'Verified fixture', 'plan_type': 'REGULAR',
         'option_type': 'IDCW', 'in_scope': True, 'isin_growth_or_div_payout': 'INFX00000002', 'isin_div_reinvest': 'INFX00000003'},
        {'amfi_scheme_code': 3, 'amfi_amc_name': 'Verified fixture', 'plan_type': 'DIRECT',
         'option_type': 'GROWTH', 'in_scope': False, 'isin_growth_or_div_payout': 'INFX00000004', 'isin_div_reinvest': None},
    ]
