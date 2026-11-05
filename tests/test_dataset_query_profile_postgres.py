"""Hosted disposable PostgreSQL equivalence, never live-data acceptance."""
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import hashlib
import json

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from mfdataindia.datasets import catalogue, enrollment, policy as p, projections, query_profile as q, reads
from mfdataindia.datasets.app import create_app
from mfdataindia.enrichment import (book_core_projection, book_projection, book_runtime,
    book_sources, book_store, current_facts_store as current, monthly_projection, portfolio_store)
from mfdataindia.review.policy import fingerprint
from reader_execution_native_fixture import populate
from test_ro01_reader_policy_postgres import (
    ro01_db as ro01_db, connect, adopt, PASSWORD, generator_capabilities,
)
from test_sbi_store import source_inputs

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]


def publication_execution_id(suffix):
    return book_sources.registry(4)['job']+'-dataset-v4-'+suffix


def populated_v4_graph(dsn, monkeypatch):
    inputs = populate(dsn,monkeypatch,book_version=4)
    with connect(dsn,autocommit=False) as conn:
        conn.execute("INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES('SBI Mutual Fund','SBI MUTUAL FUND')")
        amc = conn.execute("SELECT amc_id FROM mf.amcs WHERE amfi_amc_name='SBI Mutual Fund'").fetchone()['amc_id']
        for code, option, isin in ((103215,'GROWTH','INF200K01222'),
                (103216,'IDCW','INF200K01248'),(102272,'GROWTH','INF200K01537')):
            name = ('SBI Nifty Index Fund' if code==102272 else 'SBI Flexicap Fund')+' - Regular Plan - '+option
            conn.execute("""INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,
                scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout)
                VALUES(%s,%s,%s,%s,'OPEN_ENDED','Equity','REGULAR',%s,%s)""",
                (code,amc,name,name.upper(),option,isin))
    # Existing synthetic originals and real v4 source constructor/publication.
    # No registry override, guessed document/hash or provider acquisition.
    with book_sources.select_registry(4):
        with connect(dsn) as conn:
            identities = book_store.population(conn,'sbi-expanded')
        assert len(identities)==3
        for suffix,aum in (('initial','22,109.87'),('corroboration','22,109.870'),
                           ('conflict','22,109.88')):
            batches, originals, archives = source_inputs(identities,september_aum=aum)
            with connect(dsn,'mfdata_verified_generator',autocommit=False) as conn:
                receipt = book_runtime.publish(conn,'sbi-expanded',publication_execution_id(suffix),
                    datetime(2026,10,17,tzinfo=timezone.utc),identities,batches,originals,archives)
                assert receipt['status']=='updated'
        with connect(dsn,autocommit=False) as conn:
            prior = current.configuration(conn)
            name, expected = book_projection.PROJECTIONS[4]
            raw = (ROOT/'sql/optional'/name).read_bytes()
            assert hashlib.sha256(raw).hexdigest()==expected
            # Compile the supported installed fixture from production DDL and
            # configuration constructors; not a live activation/certificate.
            conn.execute(raw.decode())
            book_core_projection.install(conn,ROOT)
            definitions = {name:current.view_definition(conn,'mf_live_facts.'+name)
                           for name in monthly_projection.ORDER}
            monthly_projection.seal_configuration(conn,prior['generation']+1,
                prior['baseline_definition'],fingerprint(book_sources.current_policy(4)),definitions)
            assert current.configuration(conn)['policy_hash']==fingerprint(book_sources.current_policy(4))
            assert book_store.installed_configuration(conn)['document']==book_sources.configuration(4)
            current.verify_acl(conn)
            assert conn.execute("SELECT 1 FROM mf_live_facts.field_versions WHERE sources::text LIKE '%amc-book%' LIMIT 1").fetchone()
            assert conn.execute("SELECT 1 FROM mf_live_facts.field_heads WHERE distinct_values>1 LIMIT 1").fetchone()
            # A full old/prior branch remains present; baseline-only and
            # book-overlay-only rows must survive UNION and anti-join sharing.
            assert conn.execute('SELECT 1 FROM mf_book_facts.prior_field_versions LIMIT 1').fetchone()
    return inputs


@pytest.mark.parametrize('ro01_db',['core'],indirect=True)
def test_sealed_v4_graph_complete_typed_rows_pagination_and_drift(ro01_db,monkeypatch):
    inputs = populated_v4_graph(ro01_db,monkeypatch)
    adopt(ro01_db)
    identifiers = {}
    with connect(ro01_db,autocommit=False) as conn:
        for name in sorted(q.TARGETS):
            schema,relation = name.split('.')
            keys = (['amfi_scheme_code'] if relation in ('resolved','current_fund_facts')
                    else ['code'] if relation=='current_values' else None)
            identifiers[name] = enrollment.enroll(conn,schema,relation,
                classification='fund_research',title=name,key_columns=keys)['dataset_id']
        report = enrollment.reconcile(conn)
        assert all(row['permissions_verified'] for row in report['results'])
    capabilities = generator_capabilities(ro01_db)
    # Generator publication still runs its actual capability guards after
    # reader adoption. No owner promotion or provider acquisition.
    with connect(ro01_db,'mfdata_verified_generator',autocommit=False) as conn:
        first = portfolio_store.publish(conn,inputs['portfolio_selection'])
        assert portfolio_store.publish(conn,inputs['portfolio_selection'])==first
    measurements = []
    expected_pages = {}
    with connect(ro01_db,p.READER) as conn:
        conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout='10s'")
        graph = projections.inspect(conn)
        assert graph.policy_hash==fingerprint(book_sources.current_policy(4)) and len(graph.oids)==9
        literal_query = r"SELECT E'escaped\\value'::text AS value FROM mf.funds LIMIT 1"
        literal_rewrite = q._reference(literal_query,'mf.funds','funds',count=1)
        assert conn.execute(literal_query).fetchall()==conn.execute(
            'WITH funds AS (SELECT * FROM mf.funds) '+literal_rewrite).fetchall()
        for name,identifier in identifiers.items():
            entry,_ = catalogue.checked(catalogue.locate(conn,identifier))
            source = q.source(entry,graph)
            assert source.profile==q.VERSION  # A generic fallback cannot pass.
            started = perf_counter()
            direct = sql.Identifier(*name.split('.'))
            original = conn.execute(sql.SQL('SELECT * FROM {}').format(direct)).fetchall()
            optimized = conn.execute(source.prefix+sql.SQL('SELECT * FROM {}').format(source.relation)).fetchall()
            assert sorted(original,key=repr)==sorted(optimized,key=repr)
            assert original and all(set(row)==set(original[0]) for row in optimized)
            keys = entry['key_columns']
            order = (sql.SQL(',').join(sql.Identifier(k) for k in keys) if keys
                     else sql.SQL('document::text COLLATE "C"'))
            expected_pages[name] = [r['document'] for r in conn.execute(
                sql.SQL('SELECT document FROM (SELECT {} AS document, * FROM {}) d ORDER BY {}').format(
                    reads.projection(entry),direct,order)).fetchall()]
            measurements.append({'relation':name,'full_fixture_rows':len(original),
                'original_and_profile_comparison_seconds':perf_counter()-started})
        # Preserve all 84 columns and actual positive, corroborated and
        # conflicting field records rather than proving an empty graph.
        assert len(expected_pages['mf.current_fund_facts'][0])==84
        versions = conn.execute('SELECT * FROM mf_live_facts.field_versions').fetchall()
        assert any(any(s.get('source_namespace')=='amc-book' for s in row['sources']) for row in versions)
        heads = conn.execute('SELECT * FROM mf_live_facts.field_heads').fetchall()
        assert any(row['distinct_values']>1 for row in heads)
        assert any(len(row['sources'])>1 for row in versions)
    dsn = make_conninfo(ro01_db,user=p.READER,password=PASSWORD)
    with TestClient(create_app(dsn)) as client:
        for name,identifier in identifiers.items():
            path = '/api/datasets/v1/'+identifier
            metadata = client.get(path)
            assert metadata.status_code==200 and 'profile' not in metadata.text
            actual, cursor = [], None
            for _ in range(len(expected_pages[name])+1):
                reply = client.get(path+'/rows',params={'limit':1,**({'cursor':cursor} if cursor else {})})
                assert reply.status_code==200, {'relation':name,'status':reply.status_code}
                actual.extend(reply.json()['rows'])
                cursor = reply.json()['next_cursor']
                if cursor is None:
                    break
            assert cursor is None and actual==expected_pages[name]
            assert client.post(path+'/rows',json={}).status_code==405
        # Recheck every request; a previous successful page is not an
        # authorization cache for later installed seal/owner/options drift.
        with connect(ro01_db,autocommit=False) as conn:
            body = conn.execute("SELECT pg_get_viewdef('mf_live_facts.field_heads'::regclass,true) AS body").fetchone()['body']
            conn.execute('CREATE OR REPLACE VIEW mf_live_facts.field_heads AS SELECT * FROM ('+
                         body.rstrip().removesuffix(';')+') changed WHERE false')
        for identifier in identifiers.values():
            refused = client.get('/api/datasets/v1/'+identifier+'/rows')
            assert refused.status_code==503 and refused.json()['detail']=='UNREVIEWED_DATASET_DEPENDENCY'
    with connect(ro01_db,p.READER) as conn:
        for statement in ('UPDATE mf_book_facts.batches SET document=document',
                          'CREATE TABLE mf_book_facts.unsafe_reader(value int)',
                          'ALTER ROLE mfdata_verified_reader CREATEROLE'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    assert generator_capabilities(ro01_db)==capabilities
    print(json.dumps({'sealed_query_profile_native_measurements':measurements,
                      'live_API_TI_or_performance_SLA':False},sort_keys=True))
