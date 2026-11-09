"""Real hosted SQL/claims/API with controlled source/decoder transport, no providers."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import copy
import hashlib
import psycopg
import pytest
from fastapi.testclient import TestClient
from mfdataindia.enrichment import (multi_facts,current_facts_store as current,
    ingestion_postgres,ingestion_adapters,hdfc_discovery)
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.app import create_review_app
from mfdataindia.review import reader_access_profile as profiles
from test_ro01_reader_policy_postgres import ro01_db as ro01_db,connect,generator_capabilities,PASSWORD
from hdfc_dynamic_native_fixture import install_hdfc
from test_hdfc_dynamic_v2 import PANELS,panels
from test_verified_review_auth import assertion,trust

pytestmark=pytest.mark.postgres
ROOT=Path(__file__).resolve().parents[1]
NOW=datetime(2026,11,5,tzinfo=timezone.utc)
PREFIX=multi_facts.JOB+'-'


def setup(dsn,monkeypatch):
    from test_ingestion_gate_postgres import install_current_gate
    install_current_gate(dsn,monkeypatch)
    return install_hdfc(dsn,ROOT)


def inputs(*,october=True):
    summary=(ROOT/'tests/fixtures/hdfc/identity-original-september-2026.pdf').read_bytes()
    url=multi_facts.source()['edition']['url'].replace('2026-10/','2026-11/').replace('September','October')
    listing=('<html><a href="'+url+'">Controlled native next edition</a></html>').encode()
    if not october:
        url=multi_facts.source()['edition']['url']
        listing=('<a href="'+url+'">Controlled native September</a>').encode()
    book=b'%PDF-controlled-native-hdfc-'+(b'october' if october else b'september')+b'-not-live'
    originals={hdfc_discovery.LISTING:{'source_url':hdfc_discovery.LISTING,'content':listing},
        url:{'source_url':url,'content':book},multi_facts.source()['identity_source']['url']:{
            'source_url':multi_facts.source()['identity_source']['url'],'content':summary}}
    texts=panels(october=october)
    def extract(raw,*,start,end):
        if raw==summary:
            pages=[{'page':1,'text':PANELS['identity_page']['text'],'text_hash':hashlib.sha256(PANELS['identity_page']['text'].encode()).hexdigest()}]
        else:
            assert raw==book and (start,end)==(1,24)
            pages=[{'page':n,'text':texts[n-7] if n in (7,8) else 'Controlled unrelated page',
                    'text_hash':hashlib.sha256((texts[n-7] if n in (7,8) else 'Controlled unrelated page').encode()).hexdigest()} for n in range(1,25)]
        from mfdataindia.enrichment import monthly_pdf
        from importlib.metadata import version
        worker=Path(monthly_pdf.__file__)
        body={'source_hash':hashlib.sha256(raw).hexdigest(),'policy_hash':fingerprint(monthly_pdf.policy()),
            'engine_hash':hashlib.sha256(worker.read_bytes()+worker.with_name('_monthly_pdf_worker.py').read_bytes()).hexdigest(),
            'extractor_version':version('pypdf'),'document_pages':24 if raw==book else 2,
            'whole_document_coverage':False,'pages':pages}
        return {**body,'extraction_hash':fingerprint(body)}
    def archive(session,raw):
        return {'uri':'gs://tlws_mf_data_source/official-monthly-facts/hdfc-flexi-cap/'+hashlib.sha256(raw).hexdigest()+'.pdf',
                'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'generation':'1','md5':'SYNTHETIC-NOT-LIVE'}
    return originals,extract,archive


@pytest.mark.parametrize('ro01_db',['core'],indirect=True)
def test_native_dynamic_hdfc_claim_publish_repeat_real_reader_api_and_preservation(ro01_db,monkeypatch,capsys):
    installation=setup(ro01_db,monkeypatch)
    capabilities=generator_capabilities(ro01_db)
    with connect(ro01_db) as conn:
        prior_monthly=conn.execute('SELECT document FROM mf_monthly_facts.snapshots ORDER BY snapshot_id').fetchall()
    calls,decodes=[],[]
    provider=[inputs(october=False)]
    def transport(url,**kwargs):
        calls.append(url)
        return copy.deepcopy(provider[0][0][url])
    def decoder(*args,**kwargs):
        decodes.append(hashlib.sha256(args[0]).hexdigest())
        return provider[0][1](*args,**kwargs)
    def archive(*args,**kwargs):
        return provider[0][2](*args,**kwargs)
    from test_ingestion_workload import entrypoint,set_environment
    from mfdataindia.enrichment import allocated_amc_workload
    from psycopg.conninfo import make_conninfo
    import os
    set_environment(monkeypatch,CLOUD_RUN_TASK_INDEX='1')
    fake_dsn=os.environ['MFDATAINDIA_DSN']
    native_dsn=make_conninfo(ro01_db,user='mfdata_verified_generator',password=PASSWORD)
    original_connect=psycopg.connect
    adapter_class,store_class=ingestion_adapters.Adapter,ingestion_postgres.Store
    def cycle(suffix,day):
        monkeypatch.setenv('CLOUD_RUN_EXECUTION',PREFIX+suffix)
        def route(dsn,*args,**kwargs):
            assert dsn==fake_dsn
            return original_connect(native_dsn,*args,**kwargs)
        def adapter(*args,**kwargs):
            return adapter_class(*args,now=NOW,transport=transport,extractor=decoder,archive=archive,**kwargs)
        with monkeypatch.context() as boundary:
            boundary.setattr(psycopg,'connect',route)
            boundary.setattr(allocated_amc_workload,'_connect',route)
            boundary.setattr(ingestion_adapters,'Adapter',adapter)
            boundary.setattr(ingestion_postgres,'Store',lambda factory:store_class(factory,now=lambda:day))
            entrypoint().main([],source_root=ROOT)
        result=__import__('json').loads(capsys.readouterr().out)
        assert result['status']=='completed'
        return result['discovery']
    september=cycle('native-hdfc-september',NOW)
    assert september['state']=='PUBLISHED' and september['latest_period_published']=='2026-09-30'
    with connect(ro01_db) as conn:
        september_rows=conn.execute('SELECT snapshot_id,document FROM mf_multi_facts.snapshots ORDER BY snapshot_id').fetchall()
    provider[0]=inputs(october=True)
    first=cycle('native-hdfc-october',NOW+timedelta(hours=25))
    assert first['state']=='PUBLISHED' and first['publication_effect']=='PUBLICATION_VERIFIED'
    assert first['latest_period_published']=='2026-10-31'
    assert len(calls)==6 and len(decodes)==4
    again=cycle('native-hdfc-repeat',NOW+timedelta(hours=50))
    assert again['state']=='UNCHANGED' and len(decodes)==4 and len(calls)==9
    assert generator_capabilities(ro01_db)==capabilities
    with connect(ro01_db) as conn:
        assert conn.execute('SELECT document FROM mf_monthly_facts.snapshots ORDER BY snapshot_id').fetchall()==prior_monthly
        assert conn.execute('SELECT count(*) AS n FROM mf_multi_facts.snapshots').fetchone()['n']==2
        assert conn.execute("SELECT snapshot_id,document FROM mf_multi_facts.snapshots WHERE disclosure_as_of='2026-09-30' ORDER BY snapshot_id").fetchall()==september_rows
        assert conn.execute("SELECT count(*) AS n FROM mf_multi_facts.runs WHERE status<>'failed'").fetchone()['n']==2
        assert conn.execute('SELECT count(*) AS n FROM mf_ingestion_ops.claims').fetchone()['n']==2
        assert current.get(conn,101763)['fields']['aum']['value']=='108546.98'
    private,verifier=trust()
    from psycopg.conninfo import make_conninfo
    app=create_review_app(make_conninfo(ro01_db,user='mfdata_verified_reader',password=PASSWORD),
        multi_amc=True,current_facts=True,verifier=verifier,reader_profile=profiles.BROAD)
    with TestClient(app) as client:
        route='/api/enrichment/v8/funds/101763/facts'
        assert client.get(route).status_code==401
        client.headers['Authorization']='Bearer '+assertion(private,scope=['review:read'])
        response=client.get(route)
        assert response.status_code==200
        assert response.json()['snapshots'][0]['snapshot']['disclosure_as_of']=='2026-10-31'
        assert client.get('/api/enrichment/v8/policy').json()['document']['version']==2
    for sql in ('UPDATE mf_multi_facts.snapshots SET document=document WHERE false',
                'CREATE TABLE mf_multi_facts.reader_forbidden(n integer)'):
        with connect(ro01_db,'mfdata_verified_reader') as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)

    with capsys.disabled():
        print(__import__('json').dumps({'native_hdfc_install_measurements':installation},sort_keys=True))


@pytest.mark.parametrize('ro01_db',['core'],indirect=True)
def test_native_populated_installer_complete_transaction_rollback_reentry_and_preservation(ro01_db,monkeypatch,capsys):
    """Full production installer, not a constructor benchmark or empty schema.

    Supported migrated graph with broad reader, real retained monthly/portfolio
    values, two sealed Kotak batches, 551 originals (one 2MiB), 122 book receipts
    and 37 multi receipts. Controlled source bytes, no provider or forged ACL.
    This representative fixture is not a byte-for-byte DEV clone.
    """
    from datetime import timedelta
    from mfdataindia.enrichment import (book_sources,book_store,book_runtime,
        multi_store,hdfc_install)
    from test_ingestion_gate_postgres import install_current_gate
    from test_kotak_method_postgres import inputs as kotak_inputs
    from hdfc_dynamic_native_fixture import Measured
    from psycopg import sql
    install_current_gate(ro01_db,monkeypatch)
    with book_sources.select_registry(7),connect(ro01_db,'mfdata_verified_generator',autocommit=False) as conn:
        identities=book_store.population(conn,'kotak-expanded')
        batches,originals,archives=kotak_inputs(identities,method=2)
        first=book_runtime.publish(conn,'kotak-expanded',PREFIX+'size-baseline',NOW,
            identities,batches,originals,archives,availability=[])
        for n in range(121):
            row={k:v for k,v in first.items() if k!='run_id'}
            row.update(execution_id=PREFIX+f'size-repeat-{n}',status='unchanged',
                observed_at=(NOW+timedelta(seconds=n+1)).isoformat())
            book_store.append_run(conn,row)
        count=conn.execute('SELECT count(*) AS n FROM mf_book_facts.sources').fetchone()['n']
        for n in range(551-count):
            raw=(b'%PDF-synthetic-size-original-'+str(n).encode()+
                (b' '*(2*1024*1024) if n==0 else b' '))
            book_runtime.retain_source(conn,'absl-expanded',{'content':raw,
                'source_url':'https://mutualfund.adityabirlacapital.com/-/media/bsl/files/resources/factsheets/size-fixture.pdf',
                'media_type':'application/pdf'})
        for n in range(37):
            multi_store.append_run(conn,{'format':'registered-facts-run-v1',
                'source_key':'hdfc-flexi-cap','execution_id':PREFIX+f'size-failed-{n}',
                'status':'failed','observed_at':NOW.isoformat(),
                'policy_hash':fingerprint(multi_facts.policy()),'snapshot_ids':[],
                'source_rows_changed':0,'all_amc_data_complete':False})
    tables=('mf_book_facts.sources','mf_book_facts.batches','mf_book_facts.runs',
        'mf_multi_facts.sources','mf_multi_facts.snapshots','mf_multi_facts.runs',
        'mf_monthly_facts.sources','mf_monthly_facts.snapshots','mf.fund_facts',
        'mf_fact_refresh.catalogs','mf_fact_refresh.runs','mf_fact_refresh.publications')
    def inventory(conn):
        return {name:fingerprint(conn.execute(sql.SQL(
            'SELECT to_jsonb(t)::text AS row FROM {} t ORDER BY to_jsonb(t)::text'
        ).format(sql.Identifier(*name.split('.')))).fetchall()) for name in tables}
    with connect(ro01_db) as conn:
        before=inventory(conn)
        cfg=book_store.installed_configuration(conn)
        prior=dict(current.configuration(conn))
        multi=dict(conn.execute('SELECT * FROM mf_multi_facts.configuration ORDER BY generation DESC LIMIT 1').fetchone())
        pins={'expected_multi_hash':fingerprint(multi),'expected_book_hash':cfg['configuration_hash'],
            'expected_current_hash':fingerprint(prior)}
        # Fail after real SQL transition but before commit; same production
        # post-write guard is still called, never skipped or replaced by PASS.
        real=current.verify_acl
        calls=[]
        def fail_after_transition(c):
            real(c)
            calls.append(1)
            if len(calls)==2:
                raise ValueError('CONTROLLED_FINAL_PRESERVATION_FAILURE')
        with monkeypatch.context() as scope:
            scope.setattr(current,'verify_acl',fail_after_transition)
            with pytest.raises(ValueError,match='CONTROLLED_FINAL_PRESERVATION_FAILURE'):
                hdfc_install.install(Measured(conn),ROOT,**pins)
        assert len(calls)==2
        assert inventory(conn)==before
        assert book_store.installed_configuration(conn)==cfg
        assert dict(current.configuration(conn))==prior
        assert dict(conn.execute('SELECT * FROM mf_multi_facts.configuration ORDER BY generation DESC LIMIT 1').fetchone())==multi
    result=install_hdfc(ro01_db,ROOT)
    with connect(ro01_db) as conn:
        assert inventory(conn)==before
        assert book_store.installed_configuration(conn)['document']==book_sources.configuration(8)
        assert current.policy_document(conn)==book_sources.current_policy(8)
        hdfc_install.verify_seal(conn)
    with capsys.disabled():
        print(__import__('json').dumps({'native_representative_hdfc_install':result,
            'fixture':{'source_rows':551,'book_batches':2,'book_receipts':122,'multi_receipts':37,
                'largest_synthetic_original_bytes':2*1024*1024,'installer_cpu_affinity':1},
            'full_business_preservation':True,'controlled_failure_rollback':True},sort_keys=True))
