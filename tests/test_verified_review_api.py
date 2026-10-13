import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from mfdataindia.review.app import create_review_app
from mfdataindia.review.policy import fingerprint, policy
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.store.locks import acquire_nav_data_operation_lock
from test_verified_review_auth import assertion, trust
from test_verified_review_store import verified_db as _verified_db, connection, generate, source_state, ROLES

verified_db = _verified_db

ROOT=Path(__file__).resolve().parents[1]


def runtime_dsn(dsn, role):
    return make_conninfo(dsn,user=role,password='verified-local-fixture-only')


@pytest.mark.postgres
def test_actual_api_feature_end_to_end_and_preservation(verified_db):
    result=generate(verified_db)
    private,verifier=trust()
    headers={'Authorization':'Bearer '+assertion(private),'Idempotency-Key':'api-review-001'}
    body={'expected_version':0,'expected_input_hash':result['input_hash'],'result_id':result['result_id'],
          'status':'reviewed','disposition':'consistent_with_evidence','reason':'Fixture review','evidence':['fixture://original']}
    with connection(verified_db) as conn:
        before=source_state(conn)
    with TestClient(create_review_app(runtime_dsn(verified_db,ROLES[2]),verifier=verifier)) as client:
        assert client.get('/api/review/v1/funds').status_code==401
        assert client.get('/api/review/v1/funds',headers={'X-Role':'admin'}).status_code==401
        analytics=client.get('/api/review/v1/funds/1/analytics',headers=headers)
        assert analytics.status_code==200,analytics.text
        assert analytics.json()['result']['result_id']==result['result_id']
        policy_response=client.get('/api/review/v1/policies/'+result['policy_hash'],headers=headers)
        assert policy_response.status_code==200
        assert policy_response.json()['document']==policy()
        first=client.post('/api/review/v1/funds/1/reviews',json=body,headers=headers)
        assert first.status_code==200,first.text
        assert first.json()['version']==1
        assert client.post('/api/review/v1/funds/1/reviews',json=body,headers=headers).json()==first.json()
        assert client.post('/api/review/v1/funds/1/reviews',json=body,headers={**headers,'Idempotency-Key':'api-review-002'}).status_code==409
        assert client.post('/api/review/v1/funds/1/reviews',json={**body,'actor':'forged'},headers=headers).status_code==422
        reader_headers={'Authorization':'Bearer '+assertion(private,scope=['review:read']), 'Idempotency-Key':'api-review-003'}
        assert client.post('/api/review/v1/funds/1/reviews',json=body,headers=reader_headers).status_code==403
        detail=client.get('/api/review/v1/funds/1',headers=headers)
        assert detail.status_code==200,detail.text
        assert detail.json()['history'][0]['actor']=='admin-1'
        proposal={k:v for k,v in body.items() if k not in ('status','disposition')}
        proposal.update(expected_version=1,fields=[{'field':'aum','proposed_value':'321'}])
        proposed=client.post('/api/review/v1/funds/1/enrichment-proposals',json=proposal,headers={**headers,'Idempotency-Key':'api-proposal-001'})
        assert proposed.status_code==200,proposed.text
        queue=client.get('/api/review/v1/funds?status=reviewed&limit=1',headers=headers)
        assert queue.status_code==200,queue.text
        assert queue.json()['items'][0]['code']==1
        assert queue.json()['items'][0]['needs_revalidation'] is None
        assert client.get('/api/review/v1/funds?limit=101',headers=headers).status_code==422
        assert client.get('/api/review/v1/funds/999',headers=headers).status_code==404
        assert client.get('/api/review/v1/funds/1/analytics?result_id='+('a'*64),headers=headers).status_code==404
        assert all(r.headers['Cache-Control']=='no-store' for r in (analytics,detail,queue,first))
    with connection(verified_db) as conn:
        assert source_state(conn)==before
    # New session/app still sees the persisted review and non-executable proposal.
    with TestClient(create_review_app(runtime_dsn(verified_db,ROLES[2]),verifier=verifier)) as client:
        detail=client.get('/api/review/v1/funds/1',headers=headers).json()
        assert detail['version']==2
        assert detail['history'][1]['document']['execution']=='not_executable'


@pytest.mark.postgres
def test_concurrent_review_writes_do_not_both_succeed(verified_db):
    result=generate(verified_db)
    private,verifier=trust()
    body={'expected_version':0,'expected_input_hash':result['input_hash'],'result_id':result['result_id'],
          'status':'in_review','disposition':'not_determined','reason':'Started inspection','evidence':[]}
    with TestClient(create_review_app(runtime_dsn(verified_db,ROLES[2]),verifier=verifier)) as client:
        def post(key):
            return client.post('/api/review/v1/funds/1/reviews',json=body,
                               headers={'Authorization':'Bearer '+assertion(private),'Idempotency-Key':key}).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(post,['concurrent-review-a','concurrent-review-b']))==[200,409]


@pytest.mark.postgres
def test_busy_source_operation_fails_closed(verified_db):
    result=generate(verified_db)
    request={'expected_version':0,'expected_input_hash':result['input_hash'],'result_id':result['result_id'],
             'status':'in_review','disposition':'not_determined','reason':'Inspect','evidence':[]}
    private,verifier=trust()
    with connection(verified_db) as busy:
        acquire_nav_data_operation_lock(busy)
        with TestClient(create_review_app(runtime_dsn(verified_db,ROLES[2]),verifier=verifier)) as client:
            response=client.post('/api/review/v1/funds/1/reviews',json=request,
                                 headers={'Authorization':'Bearer '+assertion(private),'Idempotency-Key':'busy-review-001'})
            assert response.status_code==409


@pytest.mark.postgres
def test_actual_runtime_verifier_rejects_column_source_grant(verified_db):
    with psycopg.connect(runtime_dsn(verified_db,ROLES[2]),row_factory=psycopg.rows.dict_row) as conn:
        verify_runtime(conn,'api')
    with connection(verified_db) as conn:
        conn.execute('GRANT UPDATE(nav) ON mf.nav_history TO mfdata_verified_admin')
    with psycopg.connect(runtime_dsn(verified_db,ROLES[2]),row_factory=psycopg.rows.dict_row) as conn:
        with pytest.raises(ValueError,match='source-write'):
            verify_runtime(conn,'api')
    with connection(verified_db) as conn:
        conn.execute((ROOT/'scripts/sql/reconcile_verified_review_privileges.sql').read_text())
    with psycopg.connect(runtime_dsn(verified_db,ROLES[2]),row_factory=psycopg.rows.dict_row) as conn:
        verify_runtime(conn,'api')


@pytest.mark.postgres
def test_generation_command_finite_replay_and_no_source_writes(verified_db,monkeypatch,capsys):
    spec=importlib.util.spec_from_file_location('verified_review_cli',ROOT/'scripts/verified_fund_review.py')
    cli=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    dsn=runtime_dsn(verified_db,ROLES[0])
    monkeypatch.setenv('MFDATAINDIA_DSN',dsn)
    with connection(verified_db) as conn:
        db=conn.execute('SELECT current_database() AS name').fetchone()['name']
        before=source_state(conn)
    args=['generate','--confirm-database',db,'--codes','1','--expected-policy-hash',fingerprint(policy())]
    assert cli.main(args)==0
    first=json.loads(capsys.readouterr().out)
    assert cli.main(args)==0
    assert json.loads(capsys.readouterr().out)==first
    assert first['source_writes']==0 and len(first['result_ids'])==1
    with pytest.raises(ValueError,match='confirmation'):
        cli.main([*args[:2],'wrong-database',*args[3:]])
    with pytest.raises(ValueError,match='unique'):
        cli.main(['generate','--confirm-database',db,'--codes','1','1','--expected-policy-hash',fingerprint(policy())])
    with connection(verified_db) as conn:
        assert source_state(conn)==before


def test_separate_review_contract_is_pinned():
    subprocess.run([sys.executable,'scripts/export_review_openapi.py','--check'],cwd=ROOT,check=True)


@pytest.mark.postgres
def test_unassessed_null_signals_are_explicit_and_never_eligible(verified_db):
    with connection(verified_db) as conn:
        conn.execute('DELETE FROM mf.nav_quality_assessments WHERE amfi_scheme_code=1')
        before=source_state(conn)
    generate(verified_db)
    private,verifier=trust()
    with TestClient(create_review_app(runtime_dsn(verified_db,ROLES[1]),verifier=verifier)) as client:
        response=client.get('/api/review/v1/funds/1/analytics',headers={'Authorization':'Bearer '+assertion(private)})
        assert response.status_code==200,response.text
        payload=response.json()
        quality=payload['result']['context']['nav_quality']
        assert 'signals' in quality and quality['signals'] is None
        assert quality['assessment_status']=='not_assessed'
        assert payload['comparison_eligible'] is False
    with connection(verified_db) as conn:
        assert source_state(conn)==before


@pytest.mark.postgres
def test_generator_cannot_inherit_review_execution_capability(verified_db):
    dsn=runtime_dsn(verified_db,ROLES[0])
    with psycopg.connect(dsn,row_factory=psycopg.rows.dict_row) as conn:
        verify_runtime(conn,'generator')
    with connection(verified_db) as conn:
        conn.execute('GRANT EXECUTE ON FUNCTION mf_review.append_event(integer,text,text,text,text,text,jsonb) TO mfdata_verified_generator')
    with psycopg.connect(dsn,row_factory=psycopg.rows.dict_row) as conn:
        with pytest.raises(ValueError,match='review-execution'):
            verify_runtime(conn,'generator')
