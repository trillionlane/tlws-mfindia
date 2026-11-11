"""Hosted-only real portfolio/adoption/reader factory; no live or IAM proof."""
from time import perf_counter

from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
import psycopg
import pytest

from mfdataindia.datasets import enrollment
from mfdataindia.enrichment import portfolio_schemas, portfolio_store
from mfdataindia.tagging import exposure_app, exposure_reads, entity_registry
from test_fund_exposure_policy import registry
from test_ro01_reader_policy_postgres import (
    ro01_db as ro01_db, connect, adopt, generator_capabilities, PASSWORD, values,
)

pytestmark = pytest.mark.postgres


def enroll(dsn):
    with connect(dsn,autocommit=False) as owner:
        enrollment.enroll(owner,'mf_portfolios','snapshots',classification='fund_research',
                          title='Accepted dated complete portfolios')


def client(dsn,role='mfdata_verified_reader'):
    return TestClient(exposure_app.create_app(make_conninfo(dsn,user=role,password=PASSWORD)))


def retained(dsn):
    with connect(dsn) as conn:
        return conn.execute('SELECT document FROM mf_portfolios.snapshots WHERE amfi_scheme_code=1').fetchone()['document']


@pytest.mark.parametrize('ro01_db',['generator'],indirect=True)
def test_native_observed_security_paging_verified_fixture_mapping_and_generator_replay(ro01_db,monkeypatch,capsys):
    dsn = ro01_db
    before = generator_capabilities(dsn)
    snapshot = retained(dsn)
    adopt(dsn)
    enroll(dsn)
    baseline = values(dsn)
    started = perf_counter()
    with client(dsn) as api:
        discovery = api.get(exposure_reads.PREFIX)
        assert discovery.status_code == 200 and discovery.json()['retained_snapshots']==1
        assert discovery.json()['last_reporting_date']=='2026-08-31'
        assert discovery.json()['entity_mapping_records']==0
        summary = api.get(exposure_reads.PREFIX+'/funds/1')
        assert summary.status_code == 200
        data = summary.json()
        assert data['state']=='available' and data['security_count']==2 and data['entity_count']==0
        assert data['metadata']['portfolio_snapshot_id']==snapshot['snapshot_id']
        assert data['metadata']['portfolio_as_of']=='2026-08-31'
        assert data['metadata']['withheld_row_reasons']['SECURITY_ENTITY_NOT_MAPPED_AT_DATE']==2
        assert data['metadata']['absence_or_exclusion_certified'] is False
        first = api.get(exposure_reads.PREFIX+'/funds/1/securities?limit=1').json()
        second = api.get(exposure_reads.PREFIX+'/funds/1/securities',params={
            'limit':1,'assessment_id':first['assessment_id'],'after':first['next_after']}).json()
        assert first['next_after'] and second['next_after'] is None
        assert {first['items'][0]['security_id'],second['items'][0]['security_id']}=={'INE090A01021','INE040A01034'}
        # Only synthetic mapping facts are substituted here, not DB roles,
        # sealed portfolio retrieval, planner grants or route execution.
        monkeypatch.setattr(entity_registry,'load',registry)
        mapped = api.get(exposure_reads.PREFIX+'/funds/1').json()
        assert mapped['entity_count']==2 and mapped['attribute_count']==3
        entities = api.get(exposure_reads.PREFIX+'/funds/1/entities').json()['items']
        assert sorted(x['weight']['lower'] for x in entities)==['40.0','50.0']
        assert api.get('/api/funds/1').status_code == 200
        assert api.get('/api/datasets/v1').status_code == 200
        for method in ('post','put','delete','patch'):
            assert getattr(api,method)(exposure_reads.PREFIX+'/funds/1').status_code == 405
    selection = portfolio_schemas.Selection(source_id=snapshot['source_id'],
        identity_source_id=snapshot['identity_source_id'],
        targets=[portfolio_schemas.Target(code=1,sheet='BSLFEF',identity_page=28)])
    with connect(dsn,'mfdata_verified_generator',autocommit=False) as conn:
        assert portfolio_store.publish(conn,selection)==[snapshot]
    assert generator_capabilities(dsn)==before and values(dsn)==baseline
    with connect(dsn,'mfdata_verified_reader') as conn:
        for statement in ('DELETE FROM mf_portfolios.snapshots','UPDATE mf.funds SET scheme_name=\'denied\'',
                          'CREATE TABLE mf_portfolios.denied(id int)',
                          'ALTER ROLE mfdata_verified_reader CREATEROLE'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    print({'native_exposure_reads_seconds':round(perf_counter()-started,3),
           'source_rows_changed':False,'generator_capabilities_changed':False})
    assert 'native_exposure_reads_seconds' in capsys.readouterr().out


@pytest.mark.parametrize('ro01_db',['generator'],indirect=True)
def test_native_source_registration_missing_unsafe_permissions_and_wrong_identity_fail_closed(ro01_db):
    dsn = ro01_db
    adopt(dsn)
    with client(dsn) as api:
        missing = api.get(exposure_reads.PREFIX+'/funds/1')
        assert missing.status_code == 503 and missing.json()['detail']=='DATASET_NOT_FOUND'
    enroll(dsn)
    with client(dsn,'mfdata_verified_generator') as api:
        response = api.get(exposure_reads.PREFIX+'/funds/1',headers={'X-Reader-Profile':'BROAD'})
        assert response.status_code == 503
        assert response.json()['detail'] in {'DATASET_READER_IDENTITY_UNSAFE','DATASET_READ_PROFILE_UNSAFE'}
    with connect(dsn,autocommit=False) as owner:
        owner.execute('GRANT UPDATE ON mf_portfolios.snapshots TO mfdata_verified_reader')
    with client(dsn) as api:
        assert api.get(exposure_reads.PREFIX+'/funds/1').status_code == 503
    with connect(dsn,autocommit=False) as owner:
        owner.execute('REVOKE UPDATE,SELECT ON mf_portfolios.snapshots FROM mfdata_verified_reader')
    with client(dsn) as api:
        assert api.get(exposure_reads.PREFIX+'/funds/1').status_code == 503
    with connect(dsn,autocommit=False) as owner:
        owner.execute('GRANT SELECT ON mf_portfolios.snapshots TO mfdata_verified_reader')
    with client(dsn) as api:
        assert api.get(exposure_reads.PREFIX+'/funds/1').status_code == 200


@pytest.mark.parametrize('ro01_db',['generator'],indirect=True)
def test_native_actual_new_source_conflict_invalidates_page_and_identity_change_is_visible(ro01_db):
    from test_complete_portfolios import import_source
    dsn = ro01_db
    adopt(dsn)
    enroll(dsn)
    snapshot = retained(dsn)
    with client(dsn) as api:
        first = api.get(exposure_reads.PREFIX+'/funds/1/securities?limit=1').json()
        new_source = import_source(dsn,b'PK\x03\x04revised-exposure-native',suffix='exposure-revised')
        selection = portfolio_schemas.Selection(source_id=new_source,
            identity_source_id=snapshot['identity_source_id'],
            targets=[portfolio_schemas.Target(code=1,sheet='BSLFEF',identity_page=28)])
        with connect(dsn,'mfdata_verified_generator',autocommit=False) as conn:
            assert len(portfolio_store.publish(conn,selection))==1
        summary = api.get(exposure_reads.PREFIX+'/funds/1')
        assert summary.status_code == 200 and summary.json()['state']=='conflicting'
        assert summary.json()['metadata'] is None
        stale = api.get(exposure_reads.PREFIX+'/funds/1/securities',params={
            'assessment_id':first['assessment_id'],'after':first['next_after']})
        assert stale.status_code == 409 and stale.json()['detail']=='EXPOSURE_ASSESSMENT_CHANGED'
    # The existing store resolves current-identity drift after source conflicts;
    # use its pinned prior snapshot to verify that independent boundary too.
    with connect(dsn,autocommit=False) as owner:
        owner.execute("UPDATE mf.funds SET scheme_name='Synthetic changed identity' WHERE amfi_scheme_code=1")
    with connect(dsn,'mfdata_verified_reader',autocommit=False) as reader:
        assert portfolio_store.get(reader,1,snapshot_id=snapshot['snapshot_id'])[0]['state']=='identity_changed'


@pytest.mark.parametrize('ro01_db',['generator'],indirect=True)
def test_native_portfolio_sealing_guards_retained_and_catalogue_work_not_per_item(ro01_db,monkeypatch,capsys):
    from mfdataindia.review import reader_access_profile as profiles
    dsn = ro01_db
    adopt(dsn)
    enroll(dsn)
    calls = []
    original = profiles._verify_broad_catalogue
    def checked(conn):
        calls.append(1)
        return original(conn)
    monkeypatch.setattr(profiles,'_verify_broad_catalogue',checked)
    with client(dsn) as api:
        measurements = []
        for limit in (1,50):
            calls.clear()
            started = perf_counter()
            response = api.get(exposure_reads.PREFIX+'/funds/1/securities',params={'limit':limit})
            assert response.status_code == 200
            measurements.append({'limit':limit,'catalogue_checks':len(calls),
                                 'seconds':round(perf_counter()-started,3)})
        assert measurements[0]['catalogue_checks']==measurements[1]['catalogue_checks']==3
        with connect(dsn,autocommit=False) as owner:
            owner.execute('ALTER TABLE mf_portfolios.snapshots DISABLE TRIGGER portfolio_snapshot_sealed')
        assert api.get(exposure_reads.PREFIX+'/funds/1').status_code == 503
        with connect(dsn,autocommit=False) as owner:
            owner.execute('ALTER TABLE mf_portfolios.snapshots ENABLE TRIGGER portfolio_snapshot_sealed')
        assert api.get(exposure_reads.PREFIX+'/funds/1').status_code == 200
    print({'native_exposure_catalogue_measurements':measurements,'security_cache':False})
    assert 'native_exposure_catalogue_measurements' in capsys.readouterr().out
