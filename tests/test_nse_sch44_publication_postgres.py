from contextlib import nullcontext, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4, uuid5

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from mfdataindia.nse import contract, sch44_pilot as pilot, sch44_publication, store
from test_nse01_postgres import api, connect, head, nse_db as nse_db, page, ro01_db as ro01_db
from test_ro01_reader_policy_postgres import PASSWORD
from test_nse_sch44_pilot import BUCKET, REV, Storage, fake_capture, payloads, stored_receipts

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]


def fixture(dsn):
    raw = b'\n'.join((ROOT / 'tests/fixtures/nse01/sch44_synthetic.psv').read_bytes().splitlines()[:2]) + b'\n'
    digest = '00a85cad911ecb1bdc50af522d6aa19c75fbfcb79ec976fd8860f9c4e319f838'
    assert len(raw) == 972 and sha256(raw).hexdigest() == digest
    with connect(dsn, autocommit=False) as owner:
        owner.execute((ROOT / 'sql/optional/048_nse_sch44_uat_publication.sql').read_text())
        owner.execute("UPDATE mf.funds SET isin_growth_or_div_payout='INZZ00000019',plan_type='REGULAR',option_type='GROWTH' WHERE amfi_scheme_code=1")
        funds = owner.execute(store.FUNDS).fetchall()
    canonical = next(row for row in funds if row['amfi_scheme_code'] == 1)
    assert canonical['in_scope'] is True
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


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_uat_workflow_publishes_successive_approved_exports_and_holds_unverified(
    nse_db, tmp_path, monkeypatch,
):
    raw, capture_receipt, first_approval, walk = fixture(nse_db)
    parsed = sch44_publication.prepare(raw, capture_receipt, first_approval,
                                       contract.digest(first_approval), walk)
    first_id = str(uuid5(store.NAMESPACE, contract.digest({
        'manifest': parsed.manifest, 'crosswalk': walk,
        'mapping_input': first_approval['catalogue_sha256'], 'target': 'mfdataindia.mf_nse.SCH',
        'parser': parsed.manifest['normalization'],
    })))
    second_raw = raw.replace(b'SYNTHETIC GROWTH', b'SYNTHETIC REVISION')
    unreviewed_raw = raw.replace(b'SYNTHETIC GROWTH', b'SYNTHETIC UNREVIEWED')
    assert len({raw, second_raw, unreviewed_raw}) == 3
    second_approval = {**first_approval, 'expected_head': first_id, 'source_date': '2026-09-21',
                       'expected_sha256': sha256(second_raw).hexdigest(), 'expected_bytes': len(second_raw)}
    index = {'format': sch44_publication.APPROVAL_INDEX_FORMAT, 'exports': {
        entry['expected_sha256']: entry for entry in (first_approval, second_approval)
    }}
    approval_path, walk_path = tmp_path / 'approvals.json', tmp_path / 'crosswalk.json'
    approval_path.write_bytes(contract.canonical(index))
    walk_path.write_bytes(contract.canonical(walk))
    args = ['--bucket', BUCKET, '--connector-root', str(tmp_path), '--connector-revision', REV,
            '--capture-script', str(tmp_path / 'capture.mjs'), '--approval', str(approval_path),
            '--crosswalk', str(walk_path)]
    instance = 'trillionlane-dev:asia-south1:sch44-fixture'
    binding = make_conninfo(host='/cloudsql/' + instance, dbname='mfdataindia', user='mfdata_ingest')
    monkeypatch.setenv('CLOUD_RUN_JOB', pilot.PILOT_JOB)
    monkeypatch.setenv('NSE_SCH44_APPROVAL_SHA256', contract.digest(index))
    monkeypatch.setenv('NSE_SCH44_UAT_DSN', binding)
    monkeypatch.setenv('NSE_SCH44_UAT_CLOUD_SQL_INSTANCE', instance)
    storage = Storage()
    real_connect = psycopg.connect
    database_calls = []

    def publisher_conninfo(dsn, **kwargs):
        assert dsn == binding
        database_calls.append(dsn)
        return real_connect(make_conninfo(nse_db, user='mfdata_ingest', password=PASSWORD), **kwargs)

    def run(execution, data, date):
        def connector(command, **kwargs):
            assert command[:3] == ['node', '--import', 'tsx']
            assert 'NSE_SCH44_UAT_DSN' not in kwargs['env']
            output = command[command.index('--output-dir') + 1]
            return SimpleNamespace(returncode=fake_capture(data, date=date)(Path(output)))

        receipt_output = StringIO()
        with monkeypatch.context() as boundary, redirect_stdout(receipt_output):
            boundary.setenv('CLOUD_RUN_EXECUTION', f'{pilot.PILOT_JOB}-{execution}')
            boundary.setattr(pilot.requests, 'Session', lambda: nullcontext(storage))
            boundary.setattr(pilot.subprocess, 'run', connector)
            boundary.setattr(psycopg, 'connect', publisher_conninfo)
            assert pilot.main(args) == 0
        return json.loads(receipt_output.getvalue())

    before = head(nse_db)
    first = run('first', raw, '2026-09-18')
    assert first['publication_effect'] == 'COMMITTED_VERIFIED'
    assert first['snapshot_id'] == first_id
    assert head(nse_db) == (first_id, before[1] + 1)
    with api(nse_db) as client:
        assert page(client)['rows'][0]['source_date'] == '2026-09-18'
        second = run('second', second_raw, '2026-09-21')
        assert second['publication_effect'] == 'COMMITTED_VERIFIED'
        assert second['snapshot_id'] != first_id
        active = (second['snapshot_id'], before[1] + 2)
        assert head(nse_db) == active
        active_page = page(client)
        assert active_page['rows'][0]['source_date'] == '2026-09-21'

        replay = run('replay', raw, '2026-09-18')
        assert replay['publication'] == 'UNCHANGED' and replay['snapshot_id'] == first_id
        assert replay['payload']['reused'] and replay['payload']['generation'] == first['payload']['generation']
        assert head(nse_db) == active and page(client) == active_page

        undated = run('undated', second_raw, None)
        missing_scope = run('missing-scope', unreviewed_raw, '2026-09-22')
        assert undated['export_date'] is None and undated['payload']['reused']
        assert undated['publication_reason'] == 'SCH44_EXPORT_DATE_UNVERIFIED'
        assert missing_scope['source_scope'] == 'UNVERIFIED'
        assert missing_scope['publication_reason'] == 'SCH44_APPROVAL_UNAVAILABLE'
        assert all(r['result'] == 'CAPTURED' and r['publication'] == 'HELD'
                   for r in (undated, missing_scope))
        assert head(nse_db) == active and page(client) == active_page

    assert len(database_calls) == 3
    assert set(payloads(storage).values()) == {raw, second_raw, unreviewed_raw}
    assert stored_receipts(storage) == [first, second, replay, undated, missing_scope]
    with connect(nse_db) as owner:
        assert owner.execute('SELECT count(*) AS n FROM mf_nse.snapshots').fetchone()['n'] == 2
