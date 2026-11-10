"""Provider-free contract/cursor/main-factory tests; not native or IAM proof."""
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from mfdataindia.tagging import app, policy as p, reads, schemas
from test_fund_tagging import row

ROOT = Path(__file__).resolve().parents[1]


def test_snapshot_bound_cursor_rejects_republication_query_change_and_bad_members():
    snapshot, query = 'a'*64, {'operation':'funds','tags':['b'*64],'limit':1}
    value = reads.cursor_encode(snapshot,query,7)
    assert reads.cursor_decode(value,snapshot,query,'code') == 7
    with pytest.raises(p.TaggingError,match='TAG_SNAPSHOT_CHANGED'):
        reads.cursor_decode(value,'c'*64,query,'code')
    with pytest.raises(p.TaggingError,match='INVALID_TAG_CURSOR'):
        reads.cursor_decode(value,snapshot,{**query,'limit':2},'code')
    for invalid in ('*', 'x'*2049, reads.cursor_encode(snapshot,query,True),
                    reads.cursor_encode(snapshot,query,'1')):
        with pytest.raises(p.TaggingError,match='INVALID_TAG_CURSOR'):
            reads.cursor_decode(invalid,snapshot,query,'code')
    with pytest.raises(p.TaggingError,match='INVALID_TAG_CURSOR'):
        reads.unique([('after',1),('after',2)])


@pytest.mark.parametrize('change', [
    {'extra':'synthetic-secret-marker'}, {'code':True}, {'as_of':'2026-99-99'},
    {'value':'Different exact value'}, {'tag_id':'0'*64},
    {'dimension':'values-screen'}, {'state':'not-assessed'},
    {'mapping_state':'FAMILY_MISSING'}, {'snapshot_id':'broken'},
])
def test_malformed_or_false_positive_stored_assessment_rejected(change):
    document = p.prepare([row()])['assessments'][0]
    with pytest.raises(ValidationError):
        schemas.TagAssessment.model_validate({**document,**change})


def test_all_current_factual_and_unassessed_documents_fit_closed_contract():
    document = p.prepare([row(),row(code=2,in_scope=False),row(code=3,last_seen=None)])
    for assessment in document['assessments']:
        assert schemas.TagAssessment.model_validate(assessment).model_dump() == assessment
    for definition in document['definitions']:
        assert schemas.TagDefinition.model_validate(definition).model_dump() == definition


def test_missing_evidence_cannot_be_relabelled_a_contradiction():
    document = next(a for a in p.prepare([row(last_seen=None)])['assessments'] if a['dimension']=='amc')
    assert document['state']=='insufficient-evidence' and document['tag_id'] is None
    with pytest.raises(ValidationError):
        schemas.TagAssessment.model_validate({**document,'state':'contradicted'})


def test_discovery_cannot_publish_invalid_aggregate_contradiction_counts(monkeypatch):
    monkeypatch.setattr(reads,'checked',lambda *args:None)
    monkeypatch.setattr(reads,'snapshot',lambda *args:'a'*64)
    class Connection:
        def execute(self,*args):
            return SimpleNamespace(fetchall=lambda:[{'state':'contradicted','assessments':20,
                'funds':1,'first_snapshot':'a'*64,'last_snapshot':'a'*64,'valid':False}])
    with pytest.raises(p.TaggingError,match='TAGGING_READ_MODEL_DIFFERS'):
        reads.discovery(Connection())


@pytest.mark.parametrize('codes', [[],[True],[1,1],[0],[2**31],list(range(1,52)),[{}]])
def test_invalid_batch_is_rejected_before_reader_or_query(codes):
    class Never:
        def execute(self,*args): pytest.fail('invalid input queried database')
    with pytest.raises(p.TaggingError,match='INVALID_TAG_QUERY'):
        reads.batch(Never(),codes)


@pytest.mark.parametrize('tags,match', [(['bad'],'all'),(['a'*64]*2,'all'),([{}],'all'),
                                      (['a'*64]*11,'any'),([], 'none')])
def test_filter_input_never_supplies_sql_or_unknown_semantics(tags,match):
    with pytest.raises(p.TaggingError,match='INVALID_TAG_QUERY'):
        reads.funds(None,tags=tags,match=match)


def test_batch_preserves_order_missing_codes_all_dimensions_and_exact_grain(monkeypatch):
    candidate = p.prepare([row(),row(code=2,option_type='IDCW')])
    documents = [{'code':a['code'],'dimension':a['dimension'],'document':a} for a in candidate['assessments']]
    calls = []
    class Connection:
        def execute(self,query,params=None):
            calls.append((query,params))
            if 'LIMIT 1' in query:
                return SimpleNamespace(fetchone=lambda: {'document':candidate['assessments'][0]})
            return SimpleNamespace(fetchall=lambda: documents)
    checks = []
    monkeypatch.setattr(reads,'checked',lambda conn,table:checks.append(table))
    result = reads.batch(Connection(),[2,999,1])
    assert checks == ['current'] and len(calls) == 2
    assert [f['code'] for f in result['funds']] == [2,999,1]
    assert result['funds'][1] == {'code':999,'state':'not-found','assessments':[]}
    assert len(result['funds'][0]['assessments']) == len(p.DIMENSIONS)
    assert next(a for a in result['funds'][0]['assessments'] if a['dimension']=='option')['value']=='IDCW'
    damaged = deepcopy(documents)
    documents.pop()
    with pytest.raises(p.TaggingError,match='TAGGING_READ_MODEL_DIFFERS'):
        reads.batch(Connection(),[2,999,1])
    documents[:] = damaged
    documents[0]['document'] = {**documents[0]['document'],'snapshot_id':'0'*64}
    with pytest.raises(p.TaggingError,match='TAGGING_READ_MODEL_DIFFERS'):
        reads.batch(Connection(),[2,999,1])


def test_sensitive_values_and_response_budget_not_bypassed(monkeypatch):
    with pytest.raises(ValueError):
        reads.bounded({'value':'password=synthetic-secret-marker'})
    monkeypatch.setattr(reads.datasets,'MAX_RESPONSE_BYTES',4)
    with pytest.raises(p.TaggingError,match='TAG_RESPONSE_TOO_LARGE'):
        reads.bounded({'normal':'research'})


@pytest.mark.parametrize('exception', [ValueError, p.TaggingError, reads.datasets.DatasetError])
def test_actual_factory_closed_routes_value_free_errors_and_no_startup_install(monkeypatch,exception):
    class Connection:
        def transaction(self): return nullcontext()
        def execute(self,*args): pass
    pool = SimpleNamespace(connection=lambda:nullcontext(Connection()))
    api = app.create_app('not-a-live-dsn')
    api.state.db_pool = pool  # Deliberately no lifespan/database in this provider-free case.
    client = TestClient(api)
    monkeypatch.setattr(reads,'discovery',lambda conn: (_ for _ in ()).throw(exception('synthetic-secret-marker')))
    response = client.get(reads.PREFIX)
    assert response.status_code == 503 and response.json()=={'detail':'TAGGING_READ_UNAVAILABLE'}
    assert 'synthetic-secret-marker' not in response.text
    for suffix in ('?profile=broad','?limit=1', '/batch?codes=1&codes=2',
                   '/batch?codes=1,01','/batch?codes=1,-2','/funds?limit=0',
                   '/funds?match=all&match=any','/definitions?limit=101',
                   '/funds?match=unknown','/funds?tag=invalid','/batch?codes=1,1',
                   '/funds/0','/definitions?dimension=unknown'):
        response = client.get(reads.PREFIX+suffix)
        assert response.status_code == 422
        assert response.json()=={'detail':'INVALID_TAG_QUERY'}
    for verb in ('post','put','delete','patch'):
        assert getattr(client,verb)(reads.PREFIX+'/funds').status_code == 405
    assert client.get('/api/fund-families/not-an-id/association-tags').status_code==404


def test_new_contract_matches_committed_paths_and_does_not_redefine_legacy_routes():
    from mfdataindia.datasets.app import create_app as old_app
    before, after = old_app('offline').openapi(), app.create_app('offline').openapi()
    assert all(after['paths'][path] == document for path,document in before['paths'].items())
    contract = {'openapi':after['openapi'],'paths':{k:v for k,v in after['paths'].items() if k.startswith(reads.PREFIX)},
                'components':{'schemas':{k:v for k,v in after['components']['schemas'].items()
                                        if k.startswith(('Tag','HTTPValidationError','ValidationError'))}}}
    assert contract == json.loads((ROOT/'contracts/mfdataindia-fund-tags-v1.json').read_text())


def test_openapi_declares_closed_query_vocabularies_not_unrestricted_strings():
    document = app.create_app('offline').openapi()
    def parameter(path,name):
        return next(p['schema'] for p in document['paths'][path]['get']['parameters'] if p['name']==name)
    dimension = parameter(reads.PREFIX+'/definitions','dimension')
    values = next(branch['enum'] for branch in dimension['anyOf'] if 'enum' in branch)
    assert values == list(p.DIMENSIONS)
    assert parameter(reads.PREFIX+'/funds','match')['enum'] == ['all','any']


def test_actual_request_keeps_existing_pool_and_database_telemetry_on_failure(monkeypatch,capsys):
    from itertools import count
    class Connection:
        def transaction(self): return nullcontext()
        def execute(self,*args): pass
    api = app.create_app('offline')
    api.state.db_pool = SimpleNamespace(connection=lambda:nullcontext(Connection()))
    # Synthetic clock tests wiring only, not a real database performance claim.
    monkeypatch.setattr(app,'perf_counter',count(0,0.1).__next__)
    monkeypatch.setattr(reads,'discovery',lambda conn: (_ for _ in ()).throw(ValueError('synthetic-secret-marker')))
    assert TestClient(api).get(reads.PREFIX).status_code == 503
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{')]
    event = next(r for r in rows if r.get('event')=='api_request_completed')
    assert event['pool_wait_ms'] > 0 and event['db_ms'] > 0
    assert 'synthetic-secret-marker' not in json.dumps(event)
