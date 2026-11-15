from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from mfdataindia.nse import contract, sch44_publication, store
from test_nse01_postgres import api, connect, head, nse_db as nse_db, page, ro01_db as ro01_db

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]


def fixture(dsn):
    raw = b'\n'.join((ROOT / 'tests/fixtures/nse01/sch44_synthetic.psv').read_bytes().splitlines()[:2]) + b'\n'
    digest = '00a85cad911ecb1bdc50af522d6aa19c75fbfcb79ec976fd8860f9c4e319f838'
    assert len(raw) == 972 and sha256(raw).hexdigest() == digest
    with connect(dsn, autocommit=False) as owner:
        owner.execute((ROOT / 'sql/optional/048_nse_sch44_uat_publication.sql').read_text())
        owner.execute("UPDATE mf.funds SET isin_growth_or_div_payout='INZZ00000019',plan_type='REGULAR',option_type='GROWTH',in_scope=true WHERE amfi_scheme_code=1")
        funds = owner.execute(store.FUNDS).fetchall()
    canonical = next(row for row in funds if row['amfi_scheme_code'] == 1)
    walk = {'format': 'nse01-crosswalk-v1', 'version': 'sch44-synthetic',
            'amcs': {'FIXTURE_AMC': canonical['amfi_amc_name']},
            'targets': [{'amc_code': 'FIXTURE_AMC', 'scheme_code': 'SYNTH_A', 'isin': 'INZZ00000019',
                         'plan': 'REGULAR', 'option': 'GROWTH', 'isin_slot': 'isin_growth_or_div_payout',
                         'amfi_scheme_code': 1, 'source_name': 'sanitized-fixture', 'reinvestment_flag': '0'}]}
    receipt = {'format': 'nse01-sch-api-capture-v1', 'environment': 'UAT', 'method': 'POST',
               'endpoint': '/nsemfdesk/api/v2/reports/MASTER_DOWNLOAD', 'file_type': 'SCH',
               'report_type': 'CONSOLIDATED_SCHEME_MASTER', 'source_scope': 'UNVERIFIED',
               'http_status': 200, 'header_columns': 44, 'provider_transport_started': True,
               'post_invoked': True, 'outcome': 'RETAINED_FOR_OFFLINE_VALIDATION', 'sha256': digest,
               'byte_length': len(raw), 'row_count': 1, 'export_date': '2026-09-18',
               'filename': 'NSE_NSEINVEST_ALL_18092026.txt', 'connector_revision': 'a' * 40}
    approval = {'format': sch44_publication.APPROVAL_FORMAT, 'environment': 'UAT', 'scope': 'FULL',
                'member_scope_reference': 'synthetic-known-complete', 'scope_evidence_sha256': '1'*64,
                'date_evidence_sha256': '2'*64, 'source_date': '2026-09-18', 'expected_sha256': digest,
                'expected_rows': 1, 'expected_bytes': len(raw), 'crosswalk_sha256': contract.digest(walk),
                'catalogue_sha256': contract.digest(funds), 'connector_revision': 'a'*40, 'expected_head': None}
    return raw, receipt, approval, walk


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_known_complete_sch44_publish_replay_and_failed_replacement(nse_db):
    raw, receipt, approval, walk = fixture(nse_db)
    pin = contract.digest(approval)
    before = head(nse_db)
    with connect(nse_db, 'mfdata_ingest', autocommit=False) as conn:
        with pytest.raises(contract.ImportError, match='SCH44_APPROVAL_UNTRUSTED'):
            sch44_publication.publish(conn, raw, receipt, approval, '0'*64, walk, str(uuid4()))
        with pytest.raises(contract.ImportError, match='SCH44_EXPORT_DATE_UNVERIFIED'):
            sch44_publication.publish(conn, raw, {**receipt, 'export_date': None}, approval, pin, walk, str(uuid4()))
    assert head(nse_db) == before
    with connect(nse_db, 'mfdata_ingest', autocommit=False) as conn:
        first = sch44_publication.publish(conn, raw, receipt, approval, pin, walk, str(uuid4()))
        replay = sch44_publication.publish(conn, raw, receipt, approval, pin, walk, str(uuid4()))
    assert first['effect'] == 'COMMITTED_VERIFIED' and first['head_advanced']
    assert first['exposed_rows'] == 1
    assert replay['state'] == 'UNCHANGED' and not replay['head_advanced']
    good_head = head(nse_db)
    assert good_head == (first['snapshot_id'], before[1] + 1)
    with api(nse_db) as client:
        expected = page(client)
    next_bytes = raw.replace(b'SYNTHETIC GROWTH', b'SYNTHETIC REVISION')
    next_digest = sha256(next_bytes).hexdigest()
    next_receipt = {**receipt, 'sha256': next_digest, 'byte_length': len(next_bytes)}
    bad_walk = {**walk, 'targets': []}
    bad_approval = {**approval, 'crosswalk_sha256': contract.digest(bad_walk),
                    'expected_head': first['snapshot_id'],
                    'expected_sha256': next_digest, 'expected_bytes': len(next_bytes)}
    with connect(nse_db, 'mfdata_ingest', autocommit=False) as conn:
        with pytest.raises(store.OutcomeError, match='SCH44_MAPPING_NOT_COMPLETE') as failure:
            sch44_publication.publish(conn, next_bytes, next_receipt, bad_approval, contract.digest(bad_approval),
                                      bad_walk, str(uuid4()))
    assert failure.value.report['effect'] == 'ROLLBACK_CONFIRMED'
    assert head(nse_db) == good_head
    with api(nse_db) as client:
        assert page(client) == expected
    with connect(nse_db, 'mfdata_verified_reader') as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('SELECT * FROM mf_nse.source_artifacts')
