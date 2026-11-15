import json
from pathlib import Path
import subprocess
import sys

import pytest

from mfdataindia.nse import contract, parser, sch44
from mfdataindia.nse.fixture import FULL_MANIFEST_JSON, FULL_SOURCE

SOURCE = (Path(__file__).parent / 'fixtures/nse01/sch44_synthetic.psv').read_bytes()
AMFI_CODES = (100001, 100002, 100003, 100004)


def fund(code, isin, plan='REGULAR', option='GROWTH', *, in_scope=True, slot=None):
    return {
        'amfi_scheme_code': code, 'amfi_amc_name': 'Fixture Fund House', 'plan_type': plan,
        'option_type': option, 'in_scope': in_scope,
        'isin_growth_or_div_payout': isin if slot != 'reinvest' else None,
        'isin_div_reinvest': isin if slot == 'reinvest' else None,
    }


def target(scheme, canonical):
    slot = ('isin_div_reinvest' if canonical['isin_div_reinvest']
            else 'isin_growth_or_div_payout')
    return {
        'amc_code': 'FIXTURE_AMC', 'scheme_code': scheme, 'isin': canonical[slot],
        'plan': canonical['plan_type'], 'option': canonical['option_type'], 'isin_slot': slot,
        'amfi_scheme_code': canonical['amfi_scheme_code'], 'source_name': 'Synthetic evidence',
        'reinvestment_flag': '1' if slot == 'isin_div_reinvest' else '0',
    }


@pytest.fixture
def inputs():
    funds = [
        fund(AMFI_CODES[0], 'INZZ00000019'),
        fund(AMFI_CODES[1], 'INZZ00000027', 'DIRECT'),
        fund(AMFI_CODES[2], 'INZZ00000035', option='IDCW', slot='reinvest'),
        fund(AMFI_CODES[3], 'INZZ00000035', option='IDCW', slot='reinvest'),
    ]
    snapshot = {'format': sch44.CATALOGUE_FORMAT, 'as_of': '2026-09-19',
                'dataset_version': 'synthetic-v1', 'funds': funds}
    walk = {'format': 'nse01-crosswalk-v1', 'version': 'synthetic-v1',
            'amcs': {'FIXTURE_AMC': 'Fixture Fund House'},
            'targets': [target(scheme, item) for scheme, item
                        in zip(('SYNTH_A', 'SYNTH_B', 'SYNTH_C', 'SYNTH_C'), funds)]}
    return snapshot, walk


def edit_position(position, value):
    lines = SOURCE.decode().strip('\n').split('\n')
    cells = lines[1].split('|')
    cells[position - 1] = value
    lines[1] = '|'.join(cells)
    return ('\n'.join(lines) + '\n').encode()


def test_raw_44_cells_survive_without_eligibility():
    parsed = sch44.parse(SOURCE.replace(b'\n', b'\r\n').removesuffix(b'\r\n'))
    assert all(len(cells) == 44 for cells, _ in parsed)
    cells, holds = parsed[0]
    assert cells[11:16] == ('.25', '10', '999999', '1.25', '00:00:00')
    assert holds[14] == 'SCH44_ADDITIONAL_MAX_UNVERIFIED'
    assert holds[8] == 'SCH44_PLAN_NEEDS_CROSSWALK'
    assert cells[33] == cells[43] == ''
    assert holds[36] == 'SCH44_SENTINEL'
    assert holds[37] == 'SCH44_SENTINEL'
    assert parsed[1][1][28] == 'SCH44_REINVESTMENT_TOKEN_UNRESOLVED'
    assert parsed[2][0][43] == 'SYNTHETIC_UNKNOWN'
    assert parsed[2][1][44] == 'SCH44_OPEN_CLOSE_UNVERIFIED'
    assert parsed[4][0][35] == '01-01-2020'


@pytest.mark.parametrize(('position', 'value', 'reason'), [
    (14, '1e8', 'SCH44_INVALID_NUMBER'),
    (1, '1.0', 'SCH44_INVALID_NUMBER'),
    (21, '-1', 'SCH44_INVALID_NUMBER'),
    (36, '31-02-2026', 'SCH44_INVALID_DATE'),
    (16, '24:00:00', 'SCH44_INVALID_TIME'),
    (39, 'lo\tad', 'INVALID_CELL'),
    (44, 'foot|note', 'INVALID_COLUMN_COUNT'),
])
def test_structural_failure(position, value, reason):
    with pytest.raises(contract.ImportError, match=reason):
        sch44.parse(edit_position(position, value))


def test_scope_always_unverified_and_profiles_stay_separate(inputs):
    snapshot, walk = inputs
    result = sch44.report(SOURCE, '2026-09-18', contract.canonical(snapshot), walk)
    assert result['counts'] == {'EXACT': 1, 'UNMATCHED': 2, 'AMBIGUOUS': 1, 'OUT_OF_SCOPE': 1}
    assert result['reasons']['OUT_OF_SCOPE'] == {'MFDATA_SCOPE_OR_NONREGULAR': 1}
    assert result['reasons']['AMBIGUOUS'] == {'MULTIPLE_EXACT_CROSSWALK_TARGETS': 1}
    assert result['catalogue_as_of'] == '2026-09-19'
    assert result['publication'] == 'NOT_ATTEMPTED' and result['source_scope'] == 'UNVERIFIED'
    assert 'SYNTH_' not in json.dumps(result)
    with pytest.raises(contract.ImportError, match='SCH44_INVALID_HEADER'):
        sch44.parse(FULL_SOURCE)
    manifest = contract.decode(FULL_MANIFEST_JSON)
    manifest['schema'] = sch44.PROFILE
    with pytest.raises(contract.ImportError, match='SCHEMA_UNVERIFIED'):
        parser.parse_full(SOURCE, manifest)


def test_catalogue_slot_candidates_do_not_replace_crosswalk(inputs):
    snapshot, walk = inputs
    walk['targets'] = []
    result = sch44.report(SOURCE, '2026-09-18', contract.canonical(snapshot), walk)
    assert result['counts']['EXACT'] == result['counts']['OUT_OF_SCOPE'] == 0
    assert result['unverified_isin_candidates'] == {'UNIQUE': 2, 'MULTIPLE': 1, 'NONE': 2}
    assert result['reasons']['AMBIGUOUS'] == {
        'MULTIPLE_ISSUER_ISIN_SLOTS_WITHOUT_SCHEME_CROSSWALK': 1,
    }
    assert result['reasons']['UNMATCHED'] == {'SCHEME_CROSSWALK_MISSING': 4}


def test_bad_isin_and_unknown_token_are_accounted_for(inputs):
    snapshot, walk = inputs
    changed = edit_position(5, 'INZZ00000010')
    assert sch44.parse(changed)[0][1][5] == 'SCH44_ISIN_INVALID'
    result = sch44.report(changed, '2026-09-18', contract.canonical(snapshot), walk)
    assert result['reasons']['UNMATCHED']['SOURCE_ISIN_INVALID'] == 1
    assert sch44.parse(edit_position(10, 'U'))[0][1][10] == 'SCH44_TOKEN_UNREVIEWED'


def test_crosswalk_conflict_with_same_code_and_slot_cannot_be_exact(inputs):
    snapshot, walk = inputs
    walk['targets'].append({**walk['targets'][0], 'option': 'IDCW'})
    result = sch44.report(SOURCE, '2026-09-18', contract.canonical(snapshot), walk)
    assert result['counts']['EXACT'] == 0
    assert result['reasons']['AMBIGUOUS']['CONFLICTING_CROSSWALK_TARGETS'] == 1


def test_cli_only_reads_input_files(tmp_path, inputs):
    snapshot, walk = inputs
    paths = [tmp_path / name for name in ('source.psv', 'catalogue.json', 'crosswalk.json')]
    payloads = [SOURCE, contract.canonical(snapshot), contract.canonical(walk)]
    for path, raw in zip(paths, payloads, strict=True):
        path.write_bytes(raw)
    completed = subprocess.run([
        sys.executable, '-m', 'mfdataindia.nse.sch44', '--source', str(paths[0]),
        '--source-date', '2026-09-18', '--catalogue', str(paths[1]), '--crosswalk', str(paths[2]),
    ], text=True, capture_output=True, check=True)
    assert json.loads(completed.stdout)['counts']['EXACT'] == 1
    assert sorted(tmp_path.iterdir()) == sorted(paths)
    assert [path.read_bytes() for path in paths] == payloads
