"""Provider-free semantic contract/factory; native database evidence is separate."""
from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from mfdataindia.datasets.policy import DatasetError
from mfdataindia.tagging import exposure_app as app, exposure_reads as reads
from mfdataindia.tagging.policy import TaggingError
from test_fund_exposure_policy import derive

ROOT = Path(__file__).resolve().parents[1]


def client():
    class Connection:
        def transaction(self): return nullcontext()
        def execute(self,*args): pass
    api = app.create_app('offline-not-a-live-connection')
    api.state.db_pool = SimpleNamespace(connection=lambda:nullcontext(Connection()))
    return TestClient(api)


def test_closed_real_factory_rejects_payload_flags_duplicates_and_mutations_before_read(monkeypatch):
    def never(*args,**kwargs): pytest.fail('invalid request reached database read')
    monkeypatch.setattr(reads,'summary',never)
    monkeypatch.setattr(reads,'page',never)
    api = client()
    for suffix in ('/0','/1?profile=broad','/1/entities?limit=0','/1/entities?limit=51',
                   '/1/entities?limit=1&limit=2','/1/private','/1/entities?registry=untrusted',
                   '/1/entities?assessment_id=synthetic-secret-marker','/1/entities?after=invalid'):
        response = api.get(reads.PREFIX+'/funds'+suffix)
        assert response.status_code == 422 and response.json()=={'detail':'INVALID_EXPOSURE_QUERY'}
    for method in ('post','put','patch','delete'):
        assert getattr(api,method)(reads.PREFIX+'/funds/1').status_code == 405


@pytest.mark.parametrize('exception',[ValueError,TaggingError,DatasetError])
def test_error_response_never_echoes_underlying_value(monkeypatch,exception):
    def fail(*args):raise exception('password=synthetic-secret-marker')
    monkeypatch.setattr(reads,'summary',fail)
    response = client().get(reads.PREFIX+'/funds/1')
    assert response.status_code == 503 and response.json()=={'detail':'EXPOSURE_READ_UNAVAILABLE'}
    assert 'synthetic-secret-marker' not in response.text


@pytest.mark.parametrize('cause,status',[
    ('EXPOSURE_FUND_NOT_FOUND',404),('EXPOSURE_ASSESSMENT_CHANGED',409),
    ('INVALID_EXPOSURE_QUERY',422),('EXPOSURE_READ_MODEL_DIFFERS',503),
])
def test_safe_known_failure_statuses_remain_actionable(monkeypatch,cause,status):
    def fail(*args):raise TaggingError(cause)
    monkeypatch.setattr(reads,'summary',fail)
    response = client().get(reads.PREFIX+'/funds/1')
    assert response.status_code == status and response.json()=={'detail':cause}


def test_real_read_paging_scope_and_changed_source_rejected(monkeypatch):
    observation = derive()
    monkeypatch.setattr(reads,'_derive',lambda *args:('available',None,observation))
    first = reads.page(None,1,'entities',limit=1)
    assert len(first['items']) == 1 and first['next_after']
    second = reads.page(None,1,'entities',limit=1,assessment_id=first['assessment_id'],after=first['next_after'])
    assert len(second['items']) == 1 and second['next_after'] is None
    assert first['items'][0]['item_id'] != second['items'][0]['item_id']
    with pytest.raises(TaggingError,match='INVALID_EXPOSURE_QUERY'):
        reads.page(None,1,'attributes',assessment_id=first['assessment_id'],after=first['next_after'])
    with pytest.raises(TaggingError,match='EXPOSURE_ASSESSMENT_CHANGED'):
        reads.page(None,1,'entities',assessment_id='0'*64,after=first['next_after'])
    with pytest.raises(TaggingError,match='INVALID_EXPOSURE_QUERY'):
        reads.page(None,1,'entities',assessment_id=first['assessment_id'])


def test_real_summary_and_page_output_fit_actual_closed_response_models(monkeypatch):
    observation = derive()
    monkeypatch.setattr(reads,'_derive',lambda *args:('available',None,observation))
    api = client()
    summary = api.get(reads.PREFIX+'/funds/1')
    assert summary.status_code == 200 and summary.json()['metadata']['absence_or_exclusion_certified'] is False
    assert summary.json()['security_count'] == 2
    assert summary.json()['position_count'] == 5
    for section in ('positions','securities','entities','attributes'):
        response = api.get(reads.PREFIX+'/funds/1/'+section)
        assert response.status_code == 200 and len(response.json()['items']) > 0
        assert 'notes' not in response.text and 'source_url' not in response.text


@pytest.mark.parametrize('state,reason',[
    ('not_captured','no_accepted_complete_portfolio'),
    ('identity_changed','current_identity_differs'),
    ('conflicting','same_date_distinct_retained_sources'),
])
def test_missing_or_conflicting_evidence_is_not_an_empty_complete_exposure(monkeypatch,state,reason):
    monkeypatch.setattr(reads,'_derive',lambda *args:(state,reason,None))
    api = client()
    summary = api.get(reads.PREFIX+'/funds/1').json()
    assert summary['state']==state and summary['metadata'] is None
    page = api.get(reads.PREFIX+'/funds/1/securities').json()
    assert page['items']==[] and page['assessment_id'] is None
    assert page['absence_or_exclusion_certified'] is False


def contract(document):
    paths = {k:v for k,v in document['paths'].items() if k.startswith(reads.PREFIX)}
    return {'openapi':document['openapi'],'paths':paths,
        'components':{'schemas':{k:v for k,v in document['components']['schemas'].items()
                                if k.startswith('Exposure')}}}


def test_current_contract_preserves_tag_dataset_and_legacy_paths_and_closed_section_enum():
    from mfdataindia.tagging.app import create_app as before
    old = before('offline').openapi()
    new = app.create_app('offline').openapi()
    assert all(new['paths'][path]==value for path,value in old['paths'].items())
    section = next(x['schema'] for x in new['paths'][reads.PREFIX+'/funds/{code}/{section}']['get']['parameters'] if x['name']=='section')
    assert section['enum'] == ['positions','securities','entities','attributes']
    assert contract(new)==json.loads((ROOT/'contracts/mfdataindia-fund-exposures-v1.json').read_text())


def test_discovery_is_source_checked_and_reports_retained_not_live_universe(monkeypatch):
    from mfdataindia.tagging import entity_registry
    seen = []
    monkeypatch.setattr(reads,'_checked',lambda conn:seen.append('checked'))
    monkeypatch.setattr(entity_registry,'load',lambda:{'format':'mfdata-security-entities-v1','records':[]})
    class Connection:
        def execute(self,*args):
            return SimpleNamespace(fetchone=lambda:{'snapshots':2,'first_date':'2026-07-31','last_date':'2026-08-31'})
    result = reads.discovery(Connection())
    assert seen==['checked'] and result['retained_snapshots']==2
    assert result['coverage_basis']=='retained_snapshots_not_current_universe_coverage'
    assert result['entity_mapping_records']==0 and result['freshness_certified'] is False
    monkeypatch.setattr(reads,'discovery',lambda conn:result)
    api = client()
    assert api.get(reads.PREFIX).status_code == 200
    assert api.get(reads.PREFIX+'?profile=broad').json()=={'detail':'INVALID_EXPOSURE_QUERY'}
