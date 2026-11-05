"""Real hosted PostgreSQL claim/concurrency/publication; no live providers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import Barrier
import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from mfdataindia.enrichment import (book_sources, book_store, book_runtime, kotak_book,
    ingestion_discovery as discovery, ingestion_gate as gate,
    ingestion_postgres, ingestion_runner as runner)
from mfdataindia.review import reader_access_profile as profiles
from mfdataindia.review.app import create_review_app
from reader_execution_native_fixture import populate
from test_ro01_reader_policy_postgres import ro01_db as ro01_db, connect, adopt, generator_capabilities, PASSWORD
from test_kotak_method_postgres import inputs
from test_verified_review_auth import assertion, trust

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'kotak-expanded'
PREFIX = 'tlws-mf-official-facts-refresh-dev-'
NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_claim_atomic_publication_concurrency_replay_and_independent_progress(ro01_db, monkeypatch):
    populate(ro01_db, monkeypatch, book_version=4)
    with connect(ro01_db, autocommit=False) as conn:
        conn.execute('INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES(%s,%s)', (kotak_book.AMC, kotak_book.AMC.upper()))
        conn.execute("INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout) SELECT 112090,amc_id,'Kotak Flexicap Fund - Regular Plan - Growth','KOTAK FLEXICAP FUND','OPEN_ENDED','Equity','REGULAR','GROWTH','INF174K01336' FROM mf.amcs WHERE amfi_amc_name=%s", (kotak_book.AMC,))
        conn.execute((ROOT/'sql/operations/amc_ingestion_gate_v1.sql').read_text())
    adopt(ro01_db)
    capabilities = generator_capabilities(ro01_db)
    with book_sources.select_registry(4):
        with connect(ro01_db) as conn:
            identities = book_store.population(conn, SOURCE)
        batches, originals, archives = inputs(identities, method=1)
    from mfdataindia.review.policy import fingerprint
    candidate = runner.Candidate([{'role': f'part{n}', 'content': o['content']} for n,o in enumerate(originals)],
                                  'a'*64, fingerprint(identities))
    barrier = Barrier(2)
    calls = []

    class Adapter:
        def __init__(self, execution, state=discovery.State.READY, synchronize=False):
            self.execution, self.state, self.synchronize = execution, state, synchronize
            self.preparations = 0

        def discover(self, deadline):
            reason = {discovery.State.WAITING: 'CHECKED_ISSUER_CATALOGUE_EMPTY',
                      discovery.State.INCOMPLETE: 'COMPLETE_ISSUER_LINKED_ENGLISH_PAIR_REQUIRED'}.get(self.state, '')
            return discovery.Discovery(SOURCE, self.state, reason=reason), candidate if self.state == discovery.State.READY else None

        def prepare(self, selected, deadline):
            self.preparations += 1
            if self.synchronize:
                barrier.wait(timeout=30)
            def publish(conn):
                calls.append(self.execution)
                return book_runtime.publish(conn, SOURCE, self.execution, NOW,
                    identities, batches, originals, archives, availability=[])
            return runner.Prepared('2026-09-30', publish)

    tick = iter(range(100))
    def store():
        instant = NOW+timedelta(hours=24*next(tick))
        return ingestion_postgres.Store(lambda: connect(ro01_db, 'mfdata_verified_generator'), now=lambda: instant)

    for n, state in enumerate((discovery.State.WAITING, discovery.State.INCOMPLETE)):
        execution = PREFIX+f'waiting{n}'
        observed = runner.execute(SOURCE, execution, Adapter(execution, state), store())
        assert observed['state'] == state.value and observed['publication_effect'] == 'NOT_SUBMITTED'
    with connect(ro01_db) as conn:
        assert not conn.execute('SELECT 1 FROM mf_ingestion_ops.claims').fetchone()
        assert not conn.execute('SELECT 1 FROM mf_book_facts.batches').fetchone()

    def delivery(n):
        execution = PREFIX+f'concurrent{n}'
        with book_sources.select_registry(4):
            return runner.execute(SOURCE, execution, Adapter(execution, synchronize=True), store())
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(delivery, (1, 2)))
    assert len(calls) == 1
    assert sorted(r['state'] for r in outcomes) in (
        ['HELD_FOR_RECONCILIATION', 'PUBLISHED'], ['PUBLISHED', 'UNCHANGED'])
    repeated = Adapter(PREFIX+'repeat')
    with book_sources.select_registry(4):
        result = runner.execute(SOURCE, PREFIX+'repeat', repeated, store())
    assert result['state'] == 'UNCHANGED' and repeated.preparations == 0 and len(calls) == 1
    assert result['latest_period_published'] == '2026-09-30'
    with connect(ro01_db) as conn:
        assert conn.execute('SELECT count(*) AS n FROM mf_book_facts.batches').fetchone()['n'] == 2
        assert conn.execute("SELECT count(*) AS n FROM mf_ingestion_ops.events WHERE kind='PUBLISHED'").fetchone()['n'] == 1
        saved = conn.execute('SELECT document FROM mf_book_facts.batches ORDER BY batch_id').fetchall()
    with connect(ro01_db, 'mfdata_verified_generator') as conn:
        # Same period, revised bytes are another candidate; they never overwrite
        # the old work/batches. A pending claim remains held without any lease.
        changed = discovery.work(SOURCE, '2026-09-30', discovery.manifest([
            {'role': 'part0', 'content': b'%PDF-synthetic-revision'}]), 'a'*64, fingerprint(identities))
        one = gate.claim(conn, changed, PREFIX+'revised')
        two = gate.claim(conn, changed, PREFIX+'revised-next')
        assert one.state == discovery.State.CLAIMED and two.state == discovery.State.HELD
        inspected = gate.inspect(conn, SOURCE, PREFIX+'revised')
        assert inspected['state'] == 'UNKNOWN'
    assert generator_capabilities(ro01_db) == capabilities
    with connect(ro01_db) as conn:
        assert conn.execute('SELECT document FROM mf_book_facts.batches ORDER BY batch_id').fetchall() == saved

    # A UTI discovery failure does not acquire the Kotak work or suppress a
    # subsequent successful unchanged observation of its committed publication.
    class Failing:
        def discover(self, deadline):
            raise OSError('SYNTHETIC_SECRET_NEVER_REPORT')
    failed = runner.execute('uti-expanded', PREFIX+'independent', Failing(), store())
    with book_sources.select_registry(4):
        other = runner.execute(SOURCE, PREFIX+'independent', Adapter(PREFIX+'independent'), store())
    assert failed['state'] == 'FETCH_FAILED' and other['state'] == 'UNCHANGED'
    assert 'SYNTHETIC_SECRET' not in json.dumps([failed, other])
    assert len(calls) == 1

    from psycopg.conninfo import make_conninfo
    private, verifier = trust()
    app = create_review_app(make_conninfo(ro01_db, user='mfdata_verified_reader', password=PASSWORD),
        amc_books=True, verifier=verifier, reader_profile=profiles.BROAD)
    with TestClient(app) as client:
        route = '/api/enrichment/v9/funds/112090/facts'
        assert client.get(route).status_code == 401
        client.headers['Authorization'] = 'Bearer '+assertion(private, scope=['review:read'])
        response = client.get(route)
        assert response.status_code == 200
        assert {r['disclosure_as_of'] for r in response.json()['observations']} == {'2026-08-31', '2026-09-30'}
    for statement in ('SELECT * FROM mf_ingestion_ops.claims',
                      'UPDATE mf.funds SET scheme_name=scheme_name WHERE false',
                      'CREATE TABLE mf.reader_ingestion_forbidden(n integer)'):
        with connect(ro01_db, 'mfdata_verified_reader') as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    with connect(ro01_db, 'mfdata_verified_generator') as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('UPDATE mf_ingestion_ops.work SET work_id=work_id WHERE false')


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_actual_current_main_preparation_publication_and_repeat(ro01_db, monkeypatch, capsys):
    """Only provider bytes/decoder and CloudSQL socket transport are synthetic.

    Real main allocation/environment validation, source orchestration, owner
    install, claim, source SQL/seals, COMMIT and authenticated API run normally.
    The TCP fixture replaces the unavailable CloudSQL socket; no live job runs.
    """
    from mfdataindia.enrichment import (current_facts_store as current, monthly_projection,
        book_core_projection, kotak_method_upgrade, ingestion_install,
        ingestion_adapters, allocated_amc_workload)
    from mfdataindia.review.policy import fingerprint
    from test_ingestion_workload import entrypoint, set_environment
    import hashlib
    import copy
    populate(ro01_db, monkeypatch, book_version=4)
    with connect(ro01_db, autocommit=False) as conn:
        conn.execute('INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES(%s,%s)', (kotak_book.AMC, kotak_book.AMC.upper()))
        conn.execute("INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout) SELECT 112090,amc_id,'Kotak Flexicap Fund - Regular Plan - Growth','KOTAK FLEXICAP FUND','OPEN_ENDED','Equity','REGULAR','GROWTH','INF174K01336' FROM mf.amcs WHERE amfi_amc_name=%s", (kotak_book.AMC,))
        prior = dict(current.configuration(conn))
        conn.execute((ROOT/'sql/optional/amc_book_current_projection_v2.sql.inc').read_text())
        book_core_projection.install(conn,ROOT)
        definitions = {name:current.view_definition(conn,'mf_live_facts.'+name) for name in current.VIEWS}
        monthly_projection.seal_configuration(conn,prior['generation']+1,prior['baseline_definition'],
            fingerprint(book_sources.current_policy(4)),definitions)
    adopt(ro01_db)
    with connect(ro01_db,autocommit=False) as conn:
        cfg=book_store.installed_configuration(conn)
        kotak_method_upgrade.install(conn,ROOT,expected_book_hash=cfg['configuration_hash'],
            expected_current_hash=fingerprint(dict(current.configuration(conn))))
    with connect(ro01_db,autocommit=False) as conn:
        cfg=book_store.installed_configuration(conn)
        base_hash=fingerprint(dict(current.configuration(conn)))
        installed=ingestion_install.install(conn,ROOT,expected_book_hash=cfg['configuration_hash'],expected_current_hash=base_hash)
        assert installed['status']=='INSTALLED'
    with connect(ro01_db,autocommit=False) as conn:
        assert ingestion_install.install(conn,ROOT,expected_book_hash=cfg['configuration_hash'],expected_current_hash=base_hash)['status']=='ALREADY_INSTALLED'
    capabilities=generator_capabilities(ro01_db)
    with book_sources.select_registry(7),connect(ro01_db) as conn:
        identities=book_store.population(conn,SOURCE)
        batches,originals,archives,extractions=inputs(identities,method=2,include_extractions=True)
    provider={o['source_url']:o for o in originals}
    decodes,requests=[],[]
    def transport(url,**kwargs):
        requests.append(url)
        return copy.deepcopy(provider[url])
    def extractor(raw):
        checksum=hashlib.sha256(raw).hexdigest()
        decodes.append(checksum)
        return copy.deepcopy(extractions[checksum])
    def archive(session,raw,*,media_type):
        return copy.deepcopy(next(r for r in archives if r['sha256']==hashlib.sha256(raw).hexdigest()))
    from psycopg.conninfo import make_conninfo
    real_connect=psycopg.connect
    native_dsn=make_conninfo(ro01_db,user='mfdata_verified_generator',password=PASSWORD)
    adapter_class,store_class=ingestion_adapters.Adapter,ingestion_postgres.Store
    clock=[NOW]
    set_environment(monkeypatch,CLOUD_RUN_TASK_INDEX='4')
    fake_dsn=__import__('os').environ['MFDATAINDIA_DSN']
    def route(dsn,*args,**kwargs):
        assert dsn==fake_dsn
        return real_connect(native_dsn,*args,**kwargs)
    def adapter(*args,**kwargs):
        return adapter_class(*args,transport=transport,extractor=extractor,archive=archive,now=NOW,**kwargs)
    with monkeypatch.context() as boundary:
        boundary.setattr(psycopg,'connect',route)
        boundary.setattr(allocated_amc_workload,'_connect',route)
        boundary.setattr(ingestion_adapters,'Adapter',adapter)
        boundary.setattr(ingestion_postgres,'Store',lambda factory:store_class(factory,now=lambda:clock[0]))
        entrypoint().main([],source_root=ROOT)
        first=json.loads(capsys.readouterr().out)
        assert first['status']=='completed' and first['discovery']['state']=='PUBLISHED'
        assert first['discovery']['publication_effect']=='PUBLICATION_VERIFIED'
        assert first['discovery']['latest_period_published']=='2026-10-31'
        first_decode_count=len(decodes)
        assert first_decode_count==2
        clock[0]=NOW+timedelta(hours=25)
        monkeypatch.setenv('CLOUD_RUN_EXECUTION',PREFIX+'next-poll')
        entrypoint().main([],source_root=ROOT)
        repeat=json.loads(capsys.readouterr().out)
        assert repeat['discovery']['state']=='UNCHANGED'
        assert len(decodes)==first_decode_count
    with connect(ro01_db) as conn:
        assert conn.execute('SELECT count(*) AS n FROM mf_book_facts.batches').fetchone()['n']==2
        assert conn.execute("SELECT count(*) AS n FROM mf_book_facts.runs WHERE source_key=%s AND status<>'failed'",(SOURCE,)).fetchone()['n']==1
        assert conn.execute('SELECT count(*) AS n FROM mf_ingestion_ops.claims').fetchone()['n']==1
    assert generator_capabilities(ro01_db)==capabilities
    private,verifier=trust()
    app=create_review_app(make_conninfo(ro01_db,user='mfdata_verified_reader',password=PASSWORD),
        amc_books=True,current_facts=True,verifier=verifier,reader_profile=profiles.BROAD)
    with TestClient(app) as client:
        client.headers['Authorization']='Bearer '+assertion(private,scope=['review:read'])
        for route in ('/api/enrichment/v9/funds/112090/facts','/api/enrichment/v3/funds/112090/facts'):
            assert client.get(route).status_code==200
