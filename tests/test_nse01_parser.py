"""Offline SCH acceptance; no NSE transport, credentials or live publication."""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
from uuid import uuid4

import pytest

from mfdataindia.nse import contract as c, mapping, parser
from mfdataindia.nse.cli import main
from mfdataindia.nse.schema import HEADERS43, HEADERS44, SCHEMA44
from nse01_fixtures import ROOT, RAW, MANIFEST, WALK, FIXTURES, candidate, change, funds


def test_family_field_accounting():
    doc = json.loads((ROOT/'docs/NSE01_FIELD_MAPPING_20261110.json').read_bytes())
    assert sum(len(f['fields']) for f in doc['families']) == 132
    sch = doc['families'][0]['fields']
    assert tuple(f['document_field_name'] for f in sch) == HEADERS43
    assert len(HEADERS43) == 43 and len(HEADERS44) == 44


def test_sch_doc43_retained44_stay_distinct():
    assert len(candidate().rows) == 7
    with pytest.raises(c.ImportError, match='SCHEMA_UNVERIFIED'):
        candidate(schema=SCHEMA44)
    raw = RAW.replace('|'.join(HEADERS43).encode(), '|'.join(HEADERS44).encode())
    with pytest.raises(c.ImportError, match='SCHEMA_UNVERIFIED'):
        candidate(raw)


def test_sch_opaque_id_and_original_bytes():
    value = candidate()
    assert value.raw == RAW and value.rows[0]['cells'][0] == '00000000001'
    assert value.rows[0]['cells'][1] == 'SCH_G'
    assert value.manifest['expected_sha256'] == sha256(RAW).hexdigest()


@pytest.mark.parametrize('family', ['SIP', 'STP', 'SWP', 'NAV', 'SET', 'STEPUP', 'sch'])
def test_request_allowlist_no_db_or_intent(tmp_path, family):
    meta = tmp_path/'manifest.json'
    meta.write_text(json.dumps({**MANIFEST, 'family': family}))
    output = []
    def forbidden(*args, **kwargs):
        raise AssertionError('must not connect')
    result = main(['--file',str(FIXTURES/'sch43_full.psv'),'--manifest',str(meta),
                   '--crosswalk',str(FIXTURES/'crosswalk.json'),'--import-file',
                   '--operation',str(uuid4()),'--journal-dir',str(tmp_path)], connect=forbidden, output=output.append)
    assert result == 1
    body = json.loads(output[-1])
    assert body['effect'] == 'NO_PUBLICATION'
    assert body['reason'] == ('FAMILY_NOT_IMPLEMENTED' if family in c.FAMILIES else 'INVALID_FAMILY')
    assert list(tmp_path.iterdir()) == [meta]


def test_precise_decimal_without_rounding():
    row = candidate(change(column=11, value='1234567890.123456789')).rows[0]
    assert Decimal(row['normalized']['minimum_purchase_amount']) == Decimal('1234567890.123456789')
    for bad in ('NaN', 'Infinity', '1e3', '-1', '+1', '1,000', '1.2.3'):
        with pytest.raises(c.ImportError, match='INVALID_DECIMAL'):
            candidate(change(column=11, value=bad))


def test_headers_cells_bom_crlf_preserve_final_blank():
    value = candidate(b'\xef\xbb\xbf'+RAW.replace(b'\n', b'\r\n'))
    assert len(value.rows[0]['cells']) == 43 and value.rows[0]['cells'][-1] == ''
    for raw in (RAW+b'\n', RAW.replace(b'UNIQUE NO',b'UNIQUE NUMBER'), RAW.replace(b'\n',b'\r')):
        with pytest.raises(c.ImportError):
            candidate(raw)


def test_clean_truncation_full_expected_hash_and_count_are_independent():
    clean = b'\n'.join(RAW.splitlines()[:3])+b'\n'
    assert len(clean.splitlines()[1].split(b'|')) == 43
    with pytest.raises(c.ImportError, match='FULL_BYTES_MISMATCH'):
        parser.parse(clean, MANIFEST)
    # A correct hash/byte count alone still does not make a shorter file FULL.
    with pytest.raises(c.ImportError, match='FULL_ROW_COUNT_MISMATCH'):
        parser.parse(clean, {**MANIFEST, 'expected_sha256':sha256(clean).hexdigest(), 'expected_bytes':len(clean)})


@pytest.mark.parametrize('raw', [b'', b'{"response_status":"F"}\n', b'<html>failure</html>\n',
                                  ('|'.join(HEADERS43)+'\n').encode()])
def test_error_and_truncation_not_empty_universe(raw):
    with pytest.raises(c.ImportError):
        candidate(raw)


def test_requiredness_and_flags_plans_never_invent_regular_or_permission():
    row = candidate(change(column=11,value='')).rows[0]
    assert row['normalized']['minimum_purchase_amount'] is None
    assert row['field_holds']['minimum_purchase_amount'] == 'SCH_REQUIREDNESS'
    missing = candidate(change(column=4,value='')).rows[0]
    assert mapping.resolve(missing, WALK, funds())['row_reason'] == 'SCH_REQUIREDNESS'
    unknown = candidate(change(column=7,value='Q')).rows[0]
    assert unknown['plan'] is None and mapping.resolve(unknown,WALK,funds())['row_reason'] == 'SCH_PLAN'
    flag = candidate(change(column=9,value='Q')).rows[0]
    assert flag['normalized']['purchase_allowed'] is None
    assert flag['normalized']['purchase_eligibility'] == 'UNKNOWN'


def test_crosswalk_exact_code_amc_plan_option_and_isin_slots():
    rows = candidate().rows
    resolved = [mapping.resolve(r,WALK,funds()) for r in rows]
    assert [r['status'] for r in resolved] == ['MAPPED','MAPPED','MAPPED','OUT_OF_SCOPE','UNMAPPED','CONFLICT','CONFLICT']
    assert [r['candidates'][0]['isin_slot'] for r in resolved[:3]] == ['isin_growth_or_div_payout','isin_growth_or_div_payout','isin_div_reinvest']
    for column, value in ((1,'UNKNOWN'), (5,'UNKNOWN'), (4,'INFX00009999'),(8,'Other name')):
        assert mapping.resolve(candidate(change(column=column,value=value)).rows[0],WALK,funds())['status'] != 'MAPPED'
    altered = funds()
    altered[0]['option_type'] = 'IDCW'
    assert mapping.resolve(rows[0],WALK,altered)['status'] == 'CONFLICT'


def test_mapping_ambiguity_and_out_of_scope_retained():
    row = candidate().rows[0]
    walk = deepcopy(WALK)
    walk['targets'].append({**walk['targets'][0], 'amfi_scheme_code':4})
    ambiguous = mapping.resolve(row,walk,[*funds(),{**funds()[0],'amfi_scheme_code':4}])
    assert ambiguous['status'] == 'AMBIGUOUS' and len(ambiguous['candidates']) == 2
    direct = mapping.resolve(candidate().rows[3],walk,funds())
    assert direct['status'] == 'OUT_OF_SCOPE' and direct['cells'][7] == 'D'


def test_duplicate_keys_identical_preserved_conflict_blocks():
    extra = RAW + RAW.splitlines()[1]+b'\n'
    assert len(candidate(extra).rows) == 8
    with pytest.raises(c.ImportError, match='CONFLICTING_DUPLICATE'):
        candidate(change(raw=extra,row=8,column=11,value='101'))


def test_ambiguity_boundaries_all_fourteen_policies():
    row = candidate().rows[0]
    holds = set(row['field_holds'].values())
    assert {'SCH_FIELD14','SCH_BLANK19','SCH_BLANK43','SCH_CUTOFF','SCH_SETTLEMENT',
            'SCH_SOURCE_METADATA','SCH_FLAGS_MODES','SCH_NUMERIC'} <= holds
    assert row['normalized']['maximum_purchase_amount'] is None
    assert row['normalized']['purchase_cutoff_instant'] is None
    for i in (18,42):
        with pytest.raises(c.ImportError, match='DOCUMENT_BLANK_FIELD_NONEMPTY'):
            candidate(change(column=i,value='1' if i==18 else '01-01-2020'))
    for i,value,reason in ((15,'25:00:00','INVALID_TIME'),(35,'31-02-2020','INVALID_DATE'),
                           (22,'1','CONTRADICTORY_BOUNDS'),(14,'0','NONPOSITIVE_RULE')):
        with pytest.raises(c.ImportError, match=reason):
            candidate(change(column=i,value=value))
    matrix = json.loads((ROOT/'docs/NSE01_FIXTURE_ACCEPTANCE_20261110.json').read_bytes())
    assert len(matrix['sch_ambiguity_dispositions']) == 14
    assert {p['id'] for p in matrix['sch_ambiguity_dispositions']} == holds | {
        'SCH_LAYOUT','SCH_PLAN','SCH_MAPPING','SCH_SCOPE','SCH_REQUIREDNESS','SCH_DUPLICATES'}


def test_every_sch_position_is_exposed_or_privately_held_with_a_reason():
    row = candidate().rows[0]
    # Identity columns are explicit top-level read-model fields; the remaining
    # approved references are in reference_fields. All other positions have
    # named reasons in the immutable private row, not silent omissions.
    identity = {2:'nse_scheme_code', 5:'isin', 8:'plan_type'}
    reference = {6:'source_amc_code',9:'source_scheme_name',10:'purchase_allowed',
        11:'purchase_mode',12:'minimum_purchase_amount',13:'additional_purchase_amount',
        15:'purchase_multiple',16:'purchase_cutoff_text',17:'redemption_allowed',
        18:'redemption_mode',20:'redemption_quantity_multiple',21:'maximum_redemption_quantity',
        22:'minimum_redemption_amount',23:'maximum_redemption_amount',24:'redemption_amount_multiple',
        25:'redemption_cutoff_text',27:'amc_active',28:'dividend_reinvestment',
        35:'face_value',36:'source_start_date',37:'source_end_date',33:'settlement_code'}
    private = {1:'unique_no',3:'rta_scheme_code',4:'amc_scheme_code',7:'scheme_type',
        14:'maximum_purchase_amount',19:'minimum_redemption_quantity',26:'rta_agent_code',
        29:'sip_flag',30:'stp_flag',31:'swp_flag',32:'switch_flag',34:'amc_ind',
        38:'exit_load_flag',39:'exit_load',40:'lock_in_period_flag',41:'lock_in_period',
        42:'channel_partner_code',43:'reopening_date'}
    assert set(identity) | set(reference) | set(private) == set(range(1,44))
    assert not (set(identity)&set(reference) or set(reference)&set(private))
    assert all(key in row['normalized'] for key in reference.values())
    assert all(key in row['field_holds'] for key in private.values())
    assert row['normalized']['source_scheme_name']=='FIXTURE SCH_G'
    assert row['normalized']['source_start_date']=='2020-01-01'
    assert row['normalized']['source_end_date'] is None
    assert row['normalized']['purchase_cutoff_text']=='15:00:00'
    assert row['normalized']['purchase_cutoff_instant'] is None


@pytest.mark.parametrize('extra', [{'unexpected':True}, {'scope':'PARTIAL'}, {'environment':'PROD'},
                                  {'completeness_basis':'CALLER_SAYS_FULL'}, {'expected_rows':True}])
def test_unknown_incomplete_and_unverified_manifest(extra):
    with pytest.raises(c.ImportError):
        parser.parse(RAW,{**MANIFEST,**extra})


def test_duplicate_metadata_member_and_secret_safe_cli(tmp_path):
    with pytest.raises(c.ImportError):
        c.decode(b'{"family":"SCH","family":"NAV"}')
    p = tmp_path/'bad.json'
    p.write_text('synthetic-secret-marker not json')
    output=[]
    assert main(['--dry-run','--file',str(FIXTURES/'sch43_full.psv'), '--manifest',str(p),
                 '--crosswalk',str(FIXTURES/'crosswalk.json')],output=output.append) == 1
    assert 'synthetic-secret-marker' not in ''.join(output)


def test_package_main_actual_environ_dry_run_no_connection_or_operation(monkeypatch):
    monkeypatch.setenv('NSE01_SYNTHETIC_SENTINEL','synthetic-secret-marker')
    output=[]
    def forbidden(*a,**kw):
        pytest.fail('dry-run connected')
    assert main(['--dry-run','--file',str(FIXTURES/'sch43_full.psv'),
                 '--manifest',str(FIXTURES/'sch43_full.manifest.json'),
                 '--crosswalk',str(FIXTURES/'crosswalk.json')],connect=forbidden,output=output.append) == 0
    report=json.loads(output[-1])
    assert report['state']=='VALIDATED_NOT_PUBLISHED' and report['source_rows']==7
    assert report['production_completeness']=='UNVERIFIED' and 'synthetic-secret-marker' not in output[-1]
