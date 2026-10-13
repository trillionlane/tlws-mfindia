"""DATA-03/04 integrated safety proofs on independently created scratch databases.

The restore approval fixture is synthetic: these tests NEVER certify a live
backup restore or authorize an operational correction.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from conftest import certify_comparison_fixture
from mfdataindia.api import queries
from mfdataindia.api.app import create_app
from mfdataindia.audit.nav_corrections import REVIEWER, WRITER, execute, record_approval, validate_approval
from mfdataindia.audit.remediation import canonical, export_register, fingerprint, record_decision, record_proposal, register_snapshots, seal
from mfdataindia.audit.remediation_evidence import archive_evidence, build_proposal
from mfdataindia.audit.remediation_rehearsal import rehearse
from mfdataindia.jobs.nav_calculations import recompute
from mfdataindia.store.locks import DataOperationBusy, acquire_nav_data_operation_lock
from mfdataindia.store.postgres import PostgresStore

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2025, 1, 1)
LAST = DAY + timedelta(days=399)


class PrivateFixture(dict):
    def __repr__(self):
        return "<isolated correction fixture; credentials omitted>"


@pytest.fixture
def correction_db(pg_dsn, tmp_path):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    name = "mfdata_remediation_rehearsal_" + uuid4().hex[:12]
    dsn = make_conninfo(pg_dsn, dbname=name)
    roles = [REVIEWER, WRITER, "mfdata_app", "mfdata_ingest", "mfdata_admin", "mfdata_association_writer", "mfdata_audit"]
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (roles,)).fetchone():
            pytest.skip("test roles preexist; refusing to change shared cluster privileges")
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created = []
        try:
            for role in roles:
                admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
                created.append(role)
            with PostgresStore(dsn, use_copy=False) as store:
                store.apply_migrations(ROOT / "sql")
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                conn.execute((ROOT / "scripts/sql/reconcile_nav_correction_privileges.sql").read_text())
                conn.execute("INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES('Correction fixture','CORRECTION FIXTURE')")
                for code in range(1, 13):
                    conn.execute(
                        "INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,"
                        "scheme_category,plan_type,option_type,last_seen_in_source) "
                        "VALUES(%s,1,%s,%s,'OPEN_ENDED','Debt','REGULAR',%s,%s)",
                        (code, f"Fund {code}", f"FUND {code}", "IDCW" if code == 3 else "GROWTH", LAST),
                    )
                    conn.execute("INSERT INTO mf.fund_variants(amfi_scheme_code,group_key,base_scheme_name) VALUES(%s,%s,%s)",
                                 (code, str(code), f"Fund {code}"))
                    conn.execute("INSERT INTO mf.fund_facts(amfi_scheme_code,source,sebi_category_name,return_1year,sharpe_ratio) "
                                 "VALUES(%s,'MANUAL','Fixture Debt',999,3)", (code,))
                    with conn.cursor() as cur:
                        cur.executemany("INSERT INTO mf.nav_history(amfi_scheme_code,nav_date,nav) VALUES(%s,%s,%s)",
                            [(code, DAY + timedelta(days=i),
                              Decimal('10') if code == 4 and i == 399 else Decimal('12.5') if code == 4 else
                              Decimal(12) + Decimal(code)/100 + Decimal(i)/10000) for i in range(400)])
                certify_comparison_fixture(conn)
                recompute(conn, list(range(1, 13)))
                from mfdataindia.audit import nav_integrity
                summary = nav_integrity.assess(conn)
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                register = export_register(conn, expected_dataset_version=summary['dataset_version'],
                                           expected_findings_sha256=summary['findings_sha256'])
            from mfdataindia.load.fund_family import build_fund_family

            with PostgresStore(dsn, use_copy=False) as store:
                build_fund_family(store)
            case = next(c for c in register['cases'] if c['scheme_codes'] == [4])
            raw = tmp_path / 'source.txt'
            raw.write_text('Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date\n'
                           'Open Ended Schemes ( Debt )\nCorrection fixture\n'
                           f'4;Fund 4;Regular;Growth;;;14.0000;{LAST-timedelta(days=1):%d-%b-%Y}\n'
                           f'4;Fund 4;Regular;Growth;;;15.0000;{LAST:%d-%b-%Y}\n')
            archive = tmp_path / 'archive'
            evidence = archive_evidence(raw, archive, source='AMFI_NAV_HISTORY',
                url=f'https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt={LAST-timedelta(days=1):%d-%b-%Y}&todt={LAST:%d-%b-%Y}&tp=1&mc=0',
                fetched_at=datetime.now(timezone.utc).isoformat())
            decision = seal({'case_snapshot_sha256': case['sha256'], 'supersedes_sha256': None,
                             'disposition': 'confirmed_nav_discrepancy', 'reason': 'SYNTHETIC fixture only',
                             'reviewer_reference': 'FIXTURE', 'evidence': [evidence]})
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                conn.execute('SET TRANSACTION READ ONLY')
                proposal = build_proposal(conn, case=case, decision=decision, reference=evidence,
                                         archive=archive, expected_dataset_version=register['dataset_version'])
            trial = rehearse(dsn, proposal, archive, confirm_scratch_database=name)
            restore = seal({'format': 'mfdata-nav-restore-proof-v1', 'status': 'RESTORE_AND_REHEARSAL_VERIFIED',
                'purpose': 'SYNTHETIC_FIXTURE_NOT_OPERATIONAL_BACKUP_PROOF', 'backup_id': 'SYNTHETIC-NOT-A-BACKUP',
                'restored_database': name, 'dataset_version': register['dataset_version'], 'proposal_sha256': proposal['sha256'],
                'independent_verifier_reference': 'TEST FIXTURE ONLY', 'rehearsal': trial})
            approval = seal({'format': 'mfdata-nav-correction-approval-v1', 'status': 'APPROVED_FINITE_BATCH',
                'proposal_sha256': proposal['sha256'], 'restore_proof_sha256': restore['sha256'],
                'approver_reference': 'TEST FIXTURE ONLY', 'reason': 'SYNTHETIC apply and conditional rollback'})
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(REVIEWER)))
                register_snapshots(conn, register)
                record_decision(conn, decision)
                record_proposal(conn, proposal)
                record_approval(conn, approval, proposal, restore, archive)
            yield PrivateFixture(dsn=dsn, proposal=proposal, approval=approval, archive=archive,
                                 restore=restore, name=name, decision=decision)
        finally:
            admin.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s', (name,))
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            for role in reversed(created):
                admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def operation(db, **kwargs):
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(WRITER)))
        version = kwargs.pop('expected_version', db['proposal']['expected_dataset_version'])
        return execute(conn, db['proposal'], db['archive'], expected_version=version,
                       approval_sha256=kwargs.pop('approval_sha256', db['approval']['sha256']), **kwargs)


def state(db):
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        return fingerprint([conn.execute('SELECT * FROM mf.'+t+' ORDER BY 1,2').fetchall() for t in
            ('nav_history','nav_source_revisions','nav_calculations','dataset_summary','nav_quality_assessments',
             'nav_remediation_operations','nav_correction_heads','nav_correction_commit_checks')])


@pytest.mark.postgres
@pytest.mark.parametrize('rollback', [False, True])
def test_direct_batch_cannot_commit_without_publication_and_receipt(correction_db, rollback):
    db = correction_db
    version = db['proposal']['expected_dataset_version']
    if rollback:
        version = operation(db)['dataset_version']
    baseline = state(db)
    with psycopg.connect(db['dsn']) as conn:
        conn.execute('SET LOCAL ROLE mfdata_nav_correction_writer')
        conn.execute('SELECT mf.correct_nav_batch(%s,%s,%s)', (db['proposal']['sha256'], version, rollback))
        with pytest.raises(psycopg.errors.RaiseException, match='same-transaction publication and receipt'):
            conn.commit()
    assert state(db) == baseline


@pytest.mark.postgres
@pytest.mark.parametrize('missing', ['version', 'calculations', 'audit', 'receipt_calculations', 'transaction_id'])
def test_commit_guard_rejects_incomplete_or_mismatched_publication(correction_db, monkeypatch, missing):
    from mfdataindia.audit import nav_corrections
    from psycopg.types.json import Jsonb

    db = correction_db
    baseline = state(db)
    if missing == 'calculations':
        # Let the Python path reach receipt insertion without publishing.
        def fake_recompute(conn, codes):
            return {str(r['amfi_scheme_code']): {'source_revision': r['source_revision'],
                'as_of': r['as_of'].isoformat(), 'metrics': r['metrics'], 'profile': r['profile']}
                for r in conn.execute('SELECT * FROM mf.nav_calculations WHERE amfi_scheme_code=ANY(%s)', (codes,))}
        monkeypatch.setattr(nav_corrections, 'recompute', fake_recompute)
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute('SET LOCAL ROLE mfdata_nav_correction_writer')
        receipt = execute(conn, db['proposal'], db['archive'],
            expected_version=db['proposal']['expected_dataset_version'], approval_sha256=db['approval']['sha256'])
        # Owner-only sabotage simulates interrupted/misbound downstream code;
        # neither the writer nor ingestion can rewrite the internal obligation.
        conn.execute('RESET ROLE')
        if missing == 'version':
            conn.execute('UPDATE mf.dataset_summary SET dataset_version=dataset_version-1')
        elif missing == 'audit':
            conn.execute('DELETE FROM mf.nav_quality_assessments WHERE amfi_scheme_code=4')
        elif missing in ('receipt_calculations', 'transaction_id'):
            conn.execute('ALTER TABLE mf.nav_remediation_operations DISABLE TRIGGER USER')
            receipt[missing if missing == 'transaction_id' else 'calculations'] = 'wrong' if missing == 'transaction_id' else {}
            conn.execute('UPDATE mf.nav_remediation_operations SET payload=%s', (Jsonb(receipt),))
            conn.execute('ALTER TABLE mf.nav_remediation_operations ENABLE TRIGGER USER')
        conn.execute('SET LOCAL ROLE mfdata_nav_correction_writer')
        with pytest.raises(psycopg.errors.RaiseException, match='correction commit'):
            conn.commit()
    assert state(db) == baseline


@pytest.mark.postgres
@pytest.mark.parametrize('counterpart', [False, True])
def test_reassessment_cannot_replace_exact_reviewed_dependencies(correction_db, counterpart):
    from mfdataindia.audit import nav_integrity

    db = correction_db
    assert db['proposal']['reviewed_source_revisions'] == {'4': 400}
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        code = 4
        if counterpart:
            # Peer series is outside the correction's target keys and case;
            # conservative family revision invalidation binds that dependency.
            conn.execute("UPDATE mf.fund_variants SET group_key='4' WHERE amfi_scheme_code=2")
            code = 2
        conn.execute('UPDATE mf.nav_history SET nav=nav+1 WHERE amfi_scheme_code=%s AND nav_date=%s',
                     (code, DAY + timedelta(days=10)))
        recompute(conn, [2, 4] if counterpart else [4])
        nav_integrity.assess(conn)
        row = conn.execute('SELECT r.revision,q.assessed_source_revision FROM mf.nav_source_revisions r '
            'JOIN mf.nav_quality_assessments q USING(amfi_scheme_code) WHERE amfi_scheme_code=4').fetchone()
        assert row['revision'] == row['assessed_source_revision'] != 400
    baseline = state(db)
    with pytest.raises(ValueError, match='reviewed source dependencies changed'):
        operation(db)
    with psycopg.connect(db['dsn']) as conn:
        conn.execute('SET LOCAL ROLE mfdata_nav_correction_writer')
        with pytest.raises(psycopg.errors.RaiseException, match='reviewed source dependencies changed'):
            conn.execute('SELECT mf.correct_nav_batch(%s,%s,false)',
                         (db['proposal']['sha256'], db['proposal']['expected_dataset_version']))
    assert state(db) == baseline


@pytest.mark.postgres
def test_unbound_historical_proposal_requires_a_fresh_review(correction_db):
    from mfdataindia.audit.remediation_rehearsal import validate_proposal

    db = correction_db
    unbound = seal({k: v for k, v in db['proposal'].items() if k not in ('sha256', 'reviewed_source_revisions')})
    with pytest.raises(ValueError, match='exact reviewed source dependencies'):
        validate_proposal(unbound, db['archive'])
    mismatched = seal({k: v for k, v in db['proposal'].items() if k != 'sha256'} |
                      {'reviewed_source_revisions': {'4': 399}})
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute('SET LOCAL ROLE mfdata_remediation_review')
        with pytest.raises(ValueError, match='dependencies differ from reviewed case'):
            record_proposal(conn, mismatched)


@pytest.mark.postgres
def test_commit_obligation_cannot_be_removed_or_disabled_by_runtime(correction_db):
    db = correction_db
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        for role in (WRITER, REVIEWER, 'mfdata_app', 'mfdata_ingest', 'mfdata_audit', 'mfdata_association_writer'):
            assert not conn.execute("SELECT has_table_privilege(%s,'mf.nav_correction_commit_checks',"
                "'INSERT,UPDATE,DELETE,TRUNCATE') AS ok", (role,)).fetchone()['ok']
            assert not conn.execute("SELECT has_function_privilege(%s,'mf.enforce_nav_correction_commit()',"
                "'EXECUTE') AS ok", (role,)).fetchone()['ok']
    baseline = state(db)
    with psycopg.connect(db['dsn']) as conn:
        conn.execute('SET LOCAL ROLE mfdata_nav_correction_writer')
        conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
        with pytest.raises(psycopg.errors.RaiseException, match='same-transaction publication and receipt'):
            conn.execute('SELECT mf.correct_nav_batch(%s,%s,false)',
                         (db['proposal']['sha256'], db['proposal']['expected_dataset_version']))
    assert state(db) == baseline


@pytest.mark.postgres
def test_review_export_ignores_revision_entries_without_nav_history(correction_db):
    from mfdataindia.audit import nav_integrity

    db = correction_db
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute("INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,"
                     "scheme_type,scheme_category,plan_type,option_type) "
                     "VALUES(13,1,'No history','NO HISTORY','OPEN_ENDED','Debt','REGULAR','GROWTH')")
        conn.execute("INSERT INTO mf.fund_facts(amfi_scheme_code,source) VALUES(13,'MANUAL')")
        conn.execute("UPDATE mf.fund_facts SET inception_date=%s WHERE amfi_scheme_code=13", (DAY,))
        summary = nav_integrity.assess(conn)
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        register = export_register(conn, expected_dataset_version=summary['dataset_version'],
                                   expected_findings_sha256=summary['findings_sha256'])
    assert all('13' not in case['reviewed_source_revisions'] for case in register['cases'])


@pytest.mark.postgres
def test_apply_replay_rollback_and_live_api_are_consistent(correction_db):
    db = correction_db
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        before_calcs = conn.execute('SELECT * FROM mf.nav_calculations WHERE amfi_scheme_code=1').fetchone()
        assert conn.execute('SELECT return_1year FROM mf.current_fund_facts WHERE amfi_scheme_code=4').fetchone()['return_1year'] == Decimal('-20')
    applied = operation(db)
    applied_state = state(db)
    assert operation(db) == applied and state(db) == applied_state
    with TestClient(create_app(db['dsn'])) as client:
        returns = client.get('/api/funds/4/returns')
        detail = client.get('/api/funds/4')
        peers = client.get('/api/funds/4/peers')
        risk = client.get('/api/funds/4/risk-reward')
        assert returns.status_code == detail.status_code == peers.status_code == risk.status_code == 200
        assert returns.headers['cache-control'] == 'no-store'
        assert returns.json()['horizons']['1Y'] == detail.json()['facts']['return_1year'] == 20.0
        assert returns.json()['methodology']['comparison_eligible'] is True
        assert detail.json()['nav_quality']['assessment_status'] == 'current'
        assert peers.json()['horizons']['1Y']['fund_return'] == 20.0
        assert any(p['amfi_scheme_code'] == 4 for p in risk.json()['points'])
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        assert conn.execute('SELECT * FROM mf.nav_calculations WHERE amfi_scheme_code=1').fetchone() == before_calcs
        # Original published facts were NOT overwritten by a correction.
        assert conn.execute('SELECT return_1year FROM mf.fund_facts WHERE amfi_scheme_code=4').fetchone()['return_1year'] == Decimal('999')
    rolled = operation(db, rollback=True, expected_version=applied['dataset_version'])
    assert rolled['dataset_version'] == applied['dataset_version'] + 1
    assert rolled['after_images'] == [c['before'] for c in db['proposal']['changes']]
    restored = state(db)
    assert operation(db, rollback=True, expected_version=rolled['dataset_version']) == rolled
    assert state(db) == restored
    with pytest.raises(ValueError, match='cannot be reapplied'):
        operation(db)
    with TestClient(create_app(db['dsn'])) as client:
        assert client.get('/api/funds/4/returns').json()['horizons']['1Y'] == -20.0
        assert client.get('/api/funds/4').json()['facts']['return_1year'] == -20.0


@pytest.mark.postgres
@pytest.mark.parametrize('fail_at', ['source','calculations','audit','receipt'])
def test_failure_at_every_publication_boundary_rolls_back_everything(correction_db, fail_at):
    baseline = state(correction_db)
    with pytest.raises(RuntimeError, match='simulated'):
        operation(correction_db, fail_at=fail_at)
    assert state(correction_db) == baseline


@pytest.mark.postgres
def test_wrong_version_hash_and_busy_operation_fail_without_writes(correction_db):
    db = correction_db
    baseline = state(db)
    for overrides in ({'expected_version': 999}, {'approval_sha256': '0'*64}):
        with pytest.raises(ValueError):
            operation(db, **overrides)
    with psycopg.connect(db['dsn'], row_factory=dict_row) as holder:
        acquire_nav_data_operation_lock(holder)
        with pytest.raises(DataOperationBusy):
            operation(db)
    assert state(db) == baseline


@pytest.mark.postgres
def test_ordinary_source_edits_make_metrics_unavailable_until_recomputed(correction_db):
    db = correction_db
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute('UPDATE mf.nav_history SET nav=14 WHERE amfi_scheme_code=4 AND nav_date=%s', (LAST,))
    with TestClient(create_app(db['dsn'])) as client:
        assert client.get('/api/funds/4').json()['facts']['return_1year'] is None
        assert client.get('/api/funds/4/returns').json()['horizons']['1Y'] == 12.0
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        recompute(conn, [4])
    with TestClient(create_app(db['dsn'])) as client:
        assert client.get('/api/funds/4').json()['facts']['return_1year'] == 12.0
    with pytest.raises(ValueError, match='reviewed source dependencies'):
        operation(db)


@pytest.mark.postgres
def test_protected_ingestion_conflicts_are_durable_and_do_not_overwrite(correction_db):
    db = correction_db
    applied = operation(db)
    with PostgresStore(db['dsn'], use_copy=False) as store:
        assert store.upsert_nav([(4,LAST,Decimal('10'),'AMFI_HISTORY',False)]).quarantined == 1
        store.upsert_nav([(4,LAST,Decimal('15'),'AMFI',True)])
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        assert canonical(conn.execute('SELECT * FROM mf.nav_history WHERE amfi_scheme_code=4 AND nav_date=%s', (LAST,)).fetchone()) == canonical(applied['after_images'][-1])
        assert conn.execute('SELECT count(*) AS n FROM mf.nav_ingestion_conflicts').fetchone()['n'] == 1
    assert operation(db) == applied


@pytest.mark.postgres
def test_rollback_preserves_later_legitimate_observations(correction_db):
    db = correction_db
    applied = operation(db)
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute('INSERT INTO mf.nav_history(amfi_scheme_code,nav_date,nav) VALUES(4,%s,16)', (LAST+timedelta(days=1),))
    rolled = operation(db, rollback=True, expected_version=applied['dataset_version'])
    assert rolled['after_images'] == [c['before'] for c in db['proposal']['changes']]
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        assert conn.execute('SELECT nav FROM mf.nav_history WHERE amfi_scheme_code=4 AND nav_date=%s',
                            (LAST+timedelta(days=1),)).fetchone()['nav'] == Decimal('16')
        assert conn.execute('SELECT return_1year FROM mf.current_fund_facts WHERE amfi_scheme_code=4').fetchone()['return_1year'] == Decimal('28')


@pytest.mark.postgres
def test_writer_has_no_direct_nav_privileges_and_cannot_leave_approved_keys(correction_db):
    db = correction_db
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(WRITER)))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('UPDATE mf.nav_history SET nav=999 WHERE amfi_scheme_code=1')
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(WRITER)))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('SELECT mf.correct_nav_observation(%s,1,%s,%s,false)',
                         (db['proposal']['sha256'], LAST, db['proposal']['expected_dataset_version']))
    for role in ('mfdata_app','mfdata_ingest'):
        with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
            assert conn.execute("SELECT has_table_privilege(%s,'mf.nav_correction_approvals','INSERT') AS allowed", (role,)).fetchone()['allowed'] is False
            assert conn.execute("SELECT has_function_privilege(%s,'mf.correct_nav_observation(text,integer,date,bigint,boolean)','EXECUTE') AS allowed", (role,)).fetchone()['allowed'] is False


@pytest.mark.postgres
def test_superseded_decision_cannot_be_applied(correction_db):
    db = correction_db
    decision = db['decision']
    newer = seal({k:v for k,v in decision.items() if k!='sha256'} | {
        'supersedes_sha256': decision['sha256'], 'disposition': 'insufficient_evidence', 'reason': 'Reopened fixture review'})
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(REVIEWER)))
        record_decision(conn, newer)
    baseline = state(db)
    with pytest.raises(psycopg.errors.RaiseException, match='superseded'):
        operation(db)
    assert state(db) == baseline


@pytest.mark.postgres
def test_api_response_is_one_snapshot_during_a_concurrent_source_commit(correction_db, monkeypatch):
    db = correction_db
    original = queries.fund_detail
    interleaved = False

    def read_then_commit(conn, code, **kwargs):
        nonlocal interleaved
        # Establish the request's snapshot before the independent commit.
        old = conn.execute('SELECT nav FROM mf.nav_history WHERE amfi_scheme_code=1 AND nav_date=%s', (LAST,)).fetchone()['nav']
        if not interleaved:
            with psycopg.connect(db['dsn']) as writer:
                writer.execute('UPDATE mf.nav_history SET nav=14 WHERE amfi_scheme_code=1 AND nav_date=%s', (LAST,))
            interleaved = True
            result = original(conn, code, **kwargs)
            assert result['latest_nav'] == float(old)
            assert result['facts']['return_1year'] is not None
            return result
        return original(conn, code, **kwargs)

    monkeypatch.setattr(queries, 'fund_detail', read_then_commit)
    with TestClient(create_app(db['dsn'])) as client:
        assert client.get('/api/funds/1').status_code == 200
        after = client.get('/api/funds/1').json()
        assert after['latest_nav'] == 14.0
        assert after['facts']['return_1year'] is None
        assert after['nav_quality']['assessment_status'] == 'stale'


@pytest.mark.postgres
def test_restored_broad_grants_do_not_reopen_correction_capabilities(correction_db, monkeypatch):
    db = correction_db
    spec = importlib.util.spec_from_file_location('restore_grants_combined', ROOT/'scripts/restore_snapshot.py')
    restore = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(restore)
    statements = []
    monkeypatch.setattr(restore, 'run', lambda *args, **kwargs: statements.append(kwargs['input_text']))
    restore.apply_runtime_grants(env={})
    grants = statements[0].replace('ON DATABASE mfdataindia', f'ON DATABASE "{db["name"]}"')
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute(grants)
        for role in ('mfdata_app','mfdata_ingest'):
            assert conn.execute("SELECT has_function_privilege(%s,'mf.correct_nav_observation(text,integer,date,bigint,boolean)','EXECUTE') AS ok",(role,)).fetchone()['ok'] is False
            assert not conn.execute("SELECT has_function_privilege(%s,'mf.enforce_nav_correction_commit()',"
                "'EXECUTE') AS ok", (role,)).fetchone()['ok']
            for table in ('nav_correction_approvals','nav_remediation_operations','nav_remediation_proposals','nav_correction_heads','nav_correction_commit_checks'):
                assert conn.execute('SELECT has_table_privilege(%s,%s,\'INSERT,UPDATE,DELETE,TRUNCATE\') AS ok',(role,'mf.'+table)).fetchone()['ok'] is False
        assert conn.execute("SELECT has_table_privilege('mfdata_ingest','mf.nav_quality_assessments','UPDATE') AS ok").fetchone()['ok'] is False
        conn.execute('SET LOCAL ROLE mfdata_app')
        assert queries.fund_detail(conn,4)['facts']['return_1year'] == -20.0
    with PostgresStore(db['dsn'], use_copy=False) as store:
        store.connect().execute('SET ROLE mfdata_ingest')
        store.upsert_nav([(1,LAST,Decimal('14'),'AMFI_HISTORY',False)])
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        assert conn.execute('SELECT return_1year FROM mf.current_fund_facts WHERE amfi_scheme_code=1').fetchone()['return_1year'] is not None


@pytest.mark.postgres
def test_inception_changes_invalidate_only_affected_fund(correction_db):
    db = correction_db
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute('UPDATE mf.fund_facts SET inception_date=%s WHERE amfi_scheme_code=1',(DAY,))
        assert conn.execute('SELECT return_1year FROM mf.current_fund_facts WHERE amfi_scheme_code=1').fetchone()['return_1year'] is None
        assert conn.execute('SELECT return_1year FROM mf.current_fund_facts WHERE amfi_scheme_code=2').fetchone()['return_1year'] is not None
        recompute(conn,[1])
        assert conn.execute('SELECT return_since_launch FROM mf.current_fund_facts WHERE amfi_scheme_code=1').fetchone()['return_since_launch'] is not None


@pytest.mark.postgres
def test_target_image_conflict_rejects_rollback_without_overwriting(correction_db):
    db = correction_db
    applied = operation(db)
    # Simulate a later independently governed change at the SAME key using the
    # scratch administrator; the correction writer cannot do this directly.
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute('DELETE FROM mf.nav_correction_heads WHERE amfi_scheme_code=4 AND nav_date=%s', (LAST,))
        conn.execute('UPDATE mf.nav_history SET nav=16 WHERE amfi_scheme_code=4 AND nav_date=%s', (LAST,))
    baseline = state(db)
    with pytest.raises(psycopg.errors.RaiseException, match='image conflict'):
        operation(db, rollback=True, expected_version=applied['dataset_version'])
    assert state(db) == baseline


@pytest.mark.postgres
def test_audit_does_not_skip_a_stale_per_fund_revision(correction_db):
    from mfdataindia.audit import nav_integrity

    db = correction_db
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute('UPDATE mf.nav_history SET nav=14 WHERE amfi_scheme_code=1 AND nav_date=%s',(LAST,))
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute('SET LOCAL ROLE mfdata_audit')
        acquire_nav_data_operation_lock(conn)
        result = nav_integrity.assess(conn, skip_if_current=True)
        assert result['assessments_written'] == 12


@pytest.mark.postgres
def test_all_19_pinned_read_endpoints_accept_consistent_fixture(correction_db):
    db = correction_db
    operation(db)
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        families = [str(r['tlws_mf_id']) for r in conn.execute('SELECT tlws_mf_id FROM mf.fund_family ORDER BY group_key').fetchall()]
    contract = json.loads((ROOT/'contracts/mfdataindia-openapi-v2.json').read_text())
    with TestClient(create_app(db['dsn'])) as client:
        checked = 0
        for path, operations in contract['paths'].items():
            if not path.startswith('/api/'):
                continue
            url = path.replace('{code}','4').replace('{tlws_mf_id}',families[0])
            params = {}
            for parameter in operations['get'].get('parameters',[]):
                if parameter['in'] == 'query' and parameter.get('required'):
                    key = parameter['name']
                    params[key] = ','.join(families[:2]) if 'famil' in path and key == 'ids' else '1,4' if key in ('codes','ids') else 'Fund'
            response = client.get(url, params=params)
            assert response.status_code == 200, (path,response.text)
            assert response.headers['cache-control'] == 'no-store'
            checked += 1
        assert checked == 19


@pytest.mark.postgres
def test_family_counterpart_dependencies_are_invalidated_and_republished(correction_db):
    from mfdataindia.audit import nav_integrity

    db = correction_db
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute("UPDATE mf.fund_variants SET group_key='1' WHERE amfi_scheme_code=2")
        recompute(conn,[1,2])
        nav_integrity.assess(conn)
        conn.execute('UPDATE mf.nav_history SET nav=14 WHERE amfi_scheme_code=1 AND nav_date=%s',(LAST,))
        assert conn.execute('SELECT return_1year FROM mf.current_fund_facts WHERE amfi_scheme_code=2').fetchone()['return_1year'] is None
        assert queries._nav_quality(conn,2,dataset_version=db['proposal']['expected_dataset_version'])['assessment_status'] == 'stale'
    with PostgresStore(db['dsn'], use_copy=False) as store:
        store.upsert_nav([(1,LAST,Decimal('14'),'AMFI_HISTORY',False)])
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        assert conn.execute('SELECT return_1year FROM mf.current_fund_facts WHERE amfi_scheme_code=2').fetchone()['return_1year'] is not None


@pytest.mark.postgres
def test_column_grant_drift_is_rejected_and_reconciled(correction_db):
    db = correction_db
    with psycopg.connect(db['dsn']) as conn:
        conn.execute('GRANT UPDATE(nav) ON mf.nav_history TO mfdata_nav_correction_writer')
    baseline = state(db)
    with pytest.raises(ValueError, match='direct source write'):
        operation(db)
    assert state(db) == baseline
    with psycopg.connect(db['dsn']) as conn:
        conn.execute((ROOT/'scripts/sql/reconcile_nav_correction_privileges.sql').read_text())
    operation(db)


@pytest.mark.postgres
def test_replay_does_not_certify_changed_calculations(correction_db):
    db = correction_db
    operation(db)
    with psycopg.connect(db['dsn']) as conn:
        conn.execute("UPDATE mf.nav_calculations SET metrics=jsonb_set(metrics,'{return_1year}','999'::jsonb) WHERE amfi_scheme_code=4")
    with pytest.raises(ValueError, match='later calculation changes'):
        operation(db)


@pytest.mark.postgres
def test_database_capability_rejects_reassessed_but_unreviewed_identity(correction_db):
    from mfdataindia.audit import nav_integrity

    db = correction_db
    with psycopg.connect(db['dsn'], row_factory=dict_row) as conn:
        conn.execute("UPDATE mf.funds SET option_type='IDCW' WHERE amfi_scheme_code=4")
        nav_integrity.assess(conn)
    baseline = state(db)
    with psycopg.connect(db['dsn']) as conn:
        conn.execute('SET LOCAL ROLE mfdata_nav_correction_writer')
        with pytest.raises(psycopg.errors.RaiseException, match='identity changed|reviewed source dependencies changed'):
            conn.execute('SELECT mf.correct_nav_batch(%s,%s,false)',
                         (db['proposal']['sha256'],db['proposal']['expected_dataset_version']))
    assert state(db) == baseline


def test_missing_or_mismatched_operational_restore_proof_is_rejected():
    proposal = seal({'expected_dataset_version': 1})
    restore = seal({'status': 'PASS'})
    approval = seal({'format': 'mfdata-nav-correction-approval-v1','status': 'APPROVED_FINITE_BATCH',
                     'proposal_sha256': proposal['sha256'], 'restore_proof_sha256': restore['sha256'],
                     'approver_reference': 'test', 'reason': 'test'})
    with pytest.raises(ValueError, match='operational restore'):
        validate_approval(approval, proposal, restore)
    altered = copy.deepcopy(approval)
    altered['reason'] = 'changed'
    with pytest.raises(ValueError, match='hash'):
        validate_approval(altered, proposal, restore)
