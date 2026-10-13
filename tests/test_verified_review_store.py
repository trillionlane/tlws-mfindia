"""Disposable PostgreSQL proofs. Never certifies a live restore or touches DEV."""
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from conftest import certify_comparison_fixture
from mfdataindia.review.policy import fingerprint, policy
from mfdataindia.review import store as review
from mfdataindia.store.postgres import PostgresStore

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("mfdata_verified_generator", "mfdata_verified_reader", "mfdata_verified_admin")


def source_state(conn):
    tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='mf' ORDER BY tablename").fetchall()
    hashes = {}
    for row in tables:
        # The new migration may append its own ledger row; every pre-existing
        # migration record, including checksum/timestamp, must remain unchanged.
        condition = sql.SQL(" WHERE migration_name<>'023_verified_fund_review.sql'") if row['tablename']=='schema_migrations' else sql.SQL('')
        records = conn.execute(sql.SQL("SELECT to_jsonb(t)::text AS row FROM mf.{} t{} ORDER BY to_jsonb(t)::text").format(sql.Identifier(row['tablename']),condition)).fetchall()
        hashes[row['tablename']] = fingerprint(records)
    return hashes


@pytest.fixture
def verified_db(pg_dsn):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    name = "mfdata_verified_" + uuid4().hex[:12]
    dsn = make_conninfo(pg_dsn, dbname=name)
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        fixture_roles=(*ROLES,'mfdata_ingest')
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (list(fixture_roles),)).fetchone():
            pytest.skip("optional role names preexist; refusing to modify shared roles")
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created = []
        try:
            with PostgresStore(dsn, use_copy=False) as base:
                base.apply_migrations(ROOT / "sql")
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                conn.execute("INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES('Verified fixture','VERIFIED FIXTURE')")
                for code, option in ((1,'GROWTH'),(2,'IDCW')):
                    conn.execute("INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type) "
                                 "VALUES(%s,1,%s,%s,'OPEN_ENDED','Equity','REGULAR',%s)", (code,f'Fund {code}',f'FUND {code}',option))
                    conn.execute("INSERT INTO mf.fund_variants(amfi_scheme_code,group_key,base_scheme_name) VALUES(%s,%s,%s)", (code,str(code),f'Fund {code}'))
                    conn.execute("INSERT INTO mf.fund_facts(amfi_scheme_code,source,aum,return_1year) VALUES(%s,'MANUAL',123.45,999)", (code,))
                    day = date(2024,1,1)
                    for i in range(270):
                        while day.weekday()>=5:
                            day += timedelta(days=1)
                        conn.execute("INSERT INTO mf.nav_history(amfi_scheme_code,nav_date,nav) VALUES(%s,%s,%s)", (code,day,'100' if i%2==0 else '110'))
                        day += timedelta(days=1)
                certify_comparison_fixture(conn)
            for role in fixture_roles:
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'verified-local-fixture-only'").format(sql.Identifier(role)))
                created.append(role)
            with psycopg.connect(dsn,row_factory=dict_row) as conn:
                # Same broad table defaults as restore, configured BEFORE the
                # optional objects are created, not masked by plain-role fixtures.
                conn.execute('ALTER DEFAULT PRIVILEGES GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO mfdata_ingest')
                conn.execute('CREATE TABLE mf.verified_default_control(value integer)')
                assert conn.execute("SELECT has_table_privilege('mfdata_ingest','mf.verified_default_control','INSERT') AS allowed").fetchone()['allowed']
                preserved=source_state(conn)
            with PostgresStore(dsn, use_copy=False) as base:
                base.apply_migrations(ROOT / "sql/optional", files=("023_verified_fund_review.sql",))
            with psycopg.connect(dsn, row_factory=dict_row) as conn:
                assert source_state(conn) == preserved
                for table in ('policies','snapshots','results','events'):
                    assert not conn.execute("SELECT has_table_privilege('mfdata_ingest',%s,'SELECT,INSERT,UPDATE,DELETE') AS allowed",('mf_review.'+table,)).fetchone()['allowed']
                assert conn.execute("SELECT has_table_privilege('mfdata_ingest','mf.verified_default_control','INSERT') AS allowed").fetchone()['allowed']
            with psycopg.connect(dsn) as conn:
                conn.execute((ROOT / 'scripts/sql/reconcile_verified_review_privileges.sql').read_text())
            yield dsn
        finally:
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (name,))
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            for role in reversed(created):
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def connection(dsn, role=None, readonly=False):
    conn = psycopg.connect(dsn, row_factory=dict_row)
    conn.execute("SET TRANSACTION ISOLATION LEVEL " + ("REPEATABLE READ, READ ONLY" if readonly else "READ COMMITTED"))
    if role:
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
    return conn


def generate(dsn, code=1):
    with connection(dsn, readonly=True) as conn:
        captured = review.capture(conn,code)
    with connection(dsn, ROLES[0]) as conn:
        return review.publish(conn,[captured],policy())[0]


@pytest.mark.postgres
def test_publication_replay_and_preservation(verified_db):
    with connection(verified_db) as conn:
        before = source_state(conn)
    result = generate(verified_db)
    assert generate(verified_db) == result
    with connection(verified_db,ROLES[1],readonly=True) as conn:
        received = review.get_result(conn,1)
        assert received['input_status']=='current'
        assert received['result']==result
        assert review.get_review(conn,1)['version']==0
    with connection(verified_db) as conn:
        assert source_state(conn)==before
        assert conn.execute('SELECT count(*) AS n FROM mf_review.results').fetchone()['n']==1
        assert conn.execute('SELECT count(*) AS n FROM mf_review.snapshots').fetchone()['n']==1


@pytest.mark.postgres
def test_stale_publication_and_old_result_retention(verified_db):
    old = generate(verified_db)
    with connection(verified_db,readonly=True) as conn:
        captured = review.capture(conn,1)
    with connection(verified_db) as conn:
        conn.execute("UPDATE mf.nav_history SET nav=111 WHERE amfi_scheme_code=1 AND nav_date='2024-01-02'")
    with connection(verified_db,ROLES[0]) as conn:
        with pytest.raises(review.Conflict):
            review.publish(conn,[captured],policy())
    new = generate(verified_db)
    assert new['result_id']!=old['result_id']
    with connection(verified_db,ROLES[1],readonly=True) as conn:
        assert review.get_result(conn,1,old['result_id'])['input_status']=='stale'
        assert review.get_result(conn,1,new['result_id'])['input_status']=='current'
    with connection(verified_db) as conn:
        assert conn.execute('SELECT count(*) AS n FROM mf_review.results').fetchone()['n']==2


@pytest.mark.postgres
def test_runtime_privileges_and_history_rewrite_rejection(verified_db):
    generate(verified_db)
    for role in ROLES:
        with connection(verified_db,role) as conn:
            for table in ('mf.nav_history','mf.funds','mf.fund_facts','mf.nav_calculations','mf.dataset_summary'):
                assert not conn.execute("SELECT has_table_privilege(current_user,%s,'INSERT,UPDATE,DELETE,TRUNCATE') AS allowed",(table,)).fetchone()['allowed']
            assert not conn.execute("SELECT has_table_privilege(current_user,'mf_review.events','INSERT') AS allowed").fetchone()['allowed']
    for table in ('policies','snapshots','results','events'):
        with connection(verified_db) as conn:
            with pytest.raises(psycopg.errors.RaiseException,match='append-only'):
                conn.execute(sql.SQL('DELETE FROM mf_review.{}').format(sql.Identifier(table)))


@pytest.mark.postgres
def test_atomic_batch_and_policy_unknown(verified_db):
    with connection(verified_db,readonly=True) as conn:
        captured = review.capture(conn,1)
    bad = dict(captured,code=999)
    with connection(verified_db,ROLES[0]) as conn:
        with pytest.raises(review.NotFound):
            review.publish(conn,[captured,bad],policy())
    with connection(verified_db) as conn:
        assert conn.execute('SELECT count(*) AS n FROM mf_review.results').fetchone()['n']==0


@pytest.mark.postgres
def test_reviews_replay_conflicts_proposals_and_revalidation(verified_db):
    result = generate(verified_db)
    request = {'expected_version':0,'expected_input_hash':result['input_hash'],'result_id':result['result_id'],
               'status':'reviewed','disposition':'consistent_with_evidence','reason':'Fixture inspection','evidence':['fixture://archive']}
    with connection(verified_db) as conn:
        preserved=source_state(conn)
    def post(payload,kind='review',key='fixture-key-001',actor='admin-1'):
        with connection(verified_db,ROLES[2]) as conn:
            return review.record_event(conn,1,payload,kind=kind,caller='insights',actor=actor,idempotency_key=key)
    first=post(request)
    assert first['version']==1
    assert post(request)==first
    with pytest.raises(review.Conflict):
        post(request,actor='other-admin')
    with pytest.raises(review.Conflict):
        post(request,key='fixture-key-002')
    proposal={k:v for k,v in request.items() if k not in ('status','disposition')}
    proposal.update(expected_version=1,fields=[{'field':'aum','proposed_value':'321'}])
    assert post(proposal,'proposal','fixture-proposal-1')['version']==2
    with connection(verified_db,ROLES[1],readonly=True) as conn:
        state=review.get_review(conn,1)
        assert state['status']=='reviewed' and state['needs_revalidation'] is False
        assert state['history'][1]['document']['execution']=='not_executable'
        queued=review.queue(conn,after_code=0,limit=10,status='reviewed',signal=None)
        assert [item['code'] for item in queued['items']]==[1]
    with connection(verified_db) as conn:
        assert source_state(conn)==preserved
        conn.execute("UPDATE mf.fund_facts SET aum=999 WHERE amfi_scheme_code=1")
    with connection(verified_db,ROLES[1],readonly=True) as conn:
        assert review.get_review(conn,1)['needs_revalidation'] is True
    with pytest.raises(review.Conflict):
        post(dict(request,expected_version=2),key='fixture-key-003')
    # Replay is historical success, not a new review of the changed inputs.
    assert post(request)==first


@pytest.mark.postgres
def test_publication_refuses_preexisting_repeatable_read_snapshot(verified_db):
    with connection(verified_db,readonly=True) as conn:
        captured=review.capture(conn,1)
    with psycopg.connect(verified_db,row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        conn.execute('SELECT count(*) FROM mf.nav_history')
        with pytest.raises(ValueError,match='READ COMMITTED'):
            review.publish(conn,[captured],policy())
    with connection(verified_db) as conn:
        assert conn.execute('SELECT count(*) AS n FROM mf_review.results').fetchone()['n']==0


@pytest.mark.postgres
def test_database_event_function_rejects_forged_replay_and_metadata_drift(verified_db):
    result=generate(verified_db)
    request={'expected_version':0,'expected_input_hash':result['input_hash'],'result_id':result['result_id'],
             'status':'in_review','disposition':'not_determined','reason':'Inspection','evidence':[]}
    hashed=fingerprint({'code':1,'kind':'review','actor':'admin','request':request})
    with connection(verified_db,ROLES[2]) as conn:
        review.record_event(conn,1,request,kind='review',caller='insights',actor='admin',idempotency_key='sql-replay-key')
    from psycopg.types.json import Jsonb
    with connection(verified_db,ROLES[2]) as conn:
        with pytest.raises(psycopg.errors.RaiseException,match='idempotency'):
            conn.execute('SELECT mf_review.append_event(%s,%s,%s,%s,%s,%s,%s)',
                         (1,'review','insights','admin','sql-replay-key',hashed,Jsonb(dict(request,reason='Changed reason'))))
    with connection(verified_db) as conn:
        conn.execute('UPDATE mf.fund_facts SET aum=999 WHERE amfi_scheme_code=1')
    with connection(verified_db,ROLES[2]) as conn:
        with pytest.raises(psycopg.errors.RaiseException,match='facts'):
            conn.execute('SELECT mf_review.append_event(%s,%s,%s,%s,%s,%s,%s)',
                         (1,'review','insights','admin','sql-stale-key',hashed,Jsonb(dict(request,expected_version=1))))


@pytest.mark.postgres
def test_database_and_python_canonical_hash_match_unicode_and_reject_bad_seals(verified_db):
    from psycopg.types.json import Jsonb
    from mfdataindia.review.policy import canonical
    vectors=[{'z':'मुंबई 😀','a':[None,True,False,12,-3,'line\nquote"backslash\\']},policy()]
    with connection(verified_db) as conn:
        for document in vectors:
            row=conn.execute("SELECT mf_review.canonical_json(%s) AS value",(Jsonb(document),)).fetchone()
            assert row['value']==canonical(document)
    with connection(verified_db,ROLES[0]) as conn:
        with pytest.raises(psycopg.errors.RaiseException,match='canonical hash'):
            conn.execute('INSERT INTO mf_review.policies(policy_hash,policy_id,policy_version,document) VALUES(%s,%s,%s,%s)',
                         ('a'*64,policy()['id'],1,Jsonb(policy())))


@pytest.mark.postgres
def test_result_lineage_cannot_relabel_another_fund(verified_db):
    from psycopg.types.json import Jsonb
    result=generate(verified_db)
    forged={k:v for k,v in result.items() if k!='result_id'}
    forged['code']=2
    forged={'result_id':fingerprint(forged),**forged}
    with connection(verified_db,ROLES[0]) as conn:
        with pytest.raises(psycopg.errors.RaiseException,match='lineage'):
            conn.execute('INSERT INTO mf_review.results(result_id,input_hash,policy_hash,engine_version,engine_fingerprint,document) VALUES(%s,%s,%s,%s,%s,%s)',
                         (forged['result_id'],forged['input_hash'],forged['policy_hash'],forged['engine_version'],forged['engine_fingerprint'],Jsonb(forged)))
