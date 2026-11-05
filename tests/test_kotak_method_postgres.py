"""Hosted disposable PostgreSQL publication/projection semantics, no live source.

The provider bytes/extractions are controlled synthetic inputs; actual retained
issuer PDF decoding is verified separately, not claimed as native source proof.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from mfdataindia.enrichment import (
    amfi_summary_identity, book_sources, book_store, book_runtime, kotak_book,
    kotak_batch, layout_book_pdf, current_facts_store as current,
    kotak_method_upgrade, monthly_projection, book_core_projection,
)
from mfdataindia.review import reader_access_profile as profiles
from mfdataindia.review.app import create_review_app
from mfdataindia.review.policy import fingerprint
from reader_execution_native_fixture import populate
from test_ro01_reader_policy_postgres import ro01_db as ro01_db, connect, adopt, generator_capabilities
from test_verified_review_auth import assertion, trust
from test_kotak_book import text as old_text

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.postgres
CURRENT = kotak_book.BOOK_PREFIX + 'Factsheet-for-October-2026/KotakMFFactsheetOctober2026.pdf'
SEPTEMBER = CURRENT.replace('October', 'September')
NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def inputs(identities, *, method, include_extractions=False):
    periods = ((10, CURRENT, '2026-10-31'), (9, SEPTEMBER, '2026-09-30')) if method == 2 else ((9, SEPTEMBER, '2026-09-30'), (8, CURRENT.replace('October','August'), '2026-08-31'))
    catalogues = []
    for month, url, _ in periods:
        catalogues.append({'source_url': kotak_book.CATALOGUE + f'?year=2026&month={month}&type=month',
                          'media_type': 'application/json', 'role': 'catalogue', 'content': json.dumps({
                              'status': 'Success', 'statusCode': '200', 'subHeaderList': [{
                                  'headerId': 219, 'contentType': 'upload',
                                  'content': url.removeprefix('https://vatseelabs-s3.kotakmf.com/')}]}).encode()})
    directory = {'source_url': kotak_book.DIRECTORY, 'media_type': 'application/json',
                 'role': 'directory', 'content': b'[{"scheme_id":"4372","scheme_name":"Kotak Flexicap Fund"}]'}
    index = {'source_url': 'https://www.amfiindia.com/api/schemes/4372/documents',
             'media_type': 'application/json', 'role': 'index', 'content': b'{"data":[{"schemeId":"4372","summaryXmlUrl":"https://portal.amfiindia.com/spages/SSD_4372.xml"}]}'}
    xml = {'source_url': 'https://portal.amfiindia.com/spages/SSD_4372.xml', 'media_type': 'application/xml',
           'role': 'identity', 'content': b'<SchemeSummaryDocument><SchemeSummary><Fund_Name>Kotak Flexicap Fund</Fund_Name><AMFI_Codes_To_be_phased_out>Regular Plan-Growth-112090</AMFI_Codes_To_be_phased_out><ISINs>Regular Plan-Growth-INF174K01336</ISINs></SchemeSummary></SchemeSummaryDocument>'}
    originals = [*catalogues, directory, index, xml,
                 *[{'source_url': url, 'media_type': 'application/pdf', 'role': 'book',
                    'content': ('%PDF-controlled-native-' + url).encode()} for _, url, _ in periods]]
    bridges = amfi_summary_identity.rows(xml['content'], xml['source_url'], hashlib.sha256(index['content']).hexdigest())
    batches, extractions = [], {}
    for _, url, day in periods:
        if method == 2 and day == '2026-10-31':
            real = json.loads((ROOT/'tests/fixtures/kotak/october-2026-panels.json').read_text())['pages']
            texts = [real[0]['text'], real[1]['text']]
            texts += [real[0]['text'].replace('KOTAK FLEXI CAP FUND', f'KOTAK OTHER {n} FUND').replace('Kotak Flexicap Fund', f'Kotak Other {n} Fund') for n in range(19)]
        else:
            value = old_text() if day == '2026-09-30' else old_text().replace('30th September, 2026', '31st August, 2026')
            texts = [value] + [value.replace('KOTAK FLEXI CAP FUND', f'KOTAK OTHER {n} FUND').replace('KOTAK FLEXICAP FUND', f'KOTAK OTHER {n} FUND') for n in range(19)]
        raw = next(o['content'] for o in originals if o['source_url'] == url)
        body = {'source_hash': hashlib.sha256(raw).hexdigest(),
                'policy_hash': fingerprint(layout_book_pdf.policy('kotak')),
                'engine_hash': book_sources.specification('kotak-expanded')['decoder_engine_hash'],
                'pages': [{'page': n+1, 'text': t, 'text_hash': hashlib.sha256(t.encode()).hexdigest()} for n,t in enumerate(texts)],
                'document_pages': len(texts)}
        extraction = {**body, 'extraction_hash': fingerprint(body)}
        extractions[hashlib.sha256(raw).hexdigest()] = extraction
        batch = kotak_batch.compile_batch(identities, {'source_url': url, 'extraction': extraction,
                   'source_acquisition': {'mode': 'live_catalogue', 'source_url': next(c['source_url'] for c in catalogues if url.rsplit('/',1)[1].encode() in c['content'])}},
                bridges, originals, as_of=day, directory_hash=hashlib.sha256(directory['content']).hexdigest(),
                expected_periods=[p[2] for p in periods], method=method)
        batches.append(batch)
    archives = [{'uri':'gs://tlws_mf_data_source/official-monthly-facts/kotak-expanded/' + hashlib.sha256(o['content']).hexdigest() + ('.pdf' if o['media_type']=='application/pdf' else '.xml'), 'sha256':hashlib.sha256(o['content']).hexdigest(), 'size':len(o['content']), 'generation':'1', 'md5':'SYNTHETIC-NOT-LIVE'} for o in originals if o['media_type'] in ('application/pdf','application/xml')]
    return (batches, originals, archives, extractions) if include_extractions else (batches, originals, archives)


@pytest.mark.parametrize('ro01_db', ['core'], indirect=True)
def test_native_kotak_method_transition_publication_september_replay_and_reader_api(ro01_db, monkeypatch):
    populate(ro01_db, monkeypatch, book_version=4)
    with connect(ro01_db, autocommit=False) as conn:
        conn.execute("INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES(%s,%s)", (kotak_book.AMC, kotak_book.AMC.upper()))
        conn.execute("INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout) SELECT 112090,amc_id,'Kotak Flexicap Fund - Regular Plan - Growth','KOTAK FLEXICAP FUND','OPEN_ENDED','Equity','REGULAR','GROWTH','INF174K01336' FROM mf.amcs WHERE amfi_amc_name=%s", (kotak_book.AMC,))
        # Build the pre-existing v4 graph from its real committed SQL and policy
        # constructors. No fake migration row or changed source configuration.
        prior = dict(current.configuration(conn))
        conn.execute((ROOT/'sql/optional/amc_book_current_projection_v2.sql.inc').read_text())
        book_core_projection.install(conn, ROOT)
        definitions = {name: current.view_definition(conn, 'mf_live_facts.'+name) for name in current.VIEWS}
        monthly_projection.seal_configuration(conn, prior['generation']+1, prior['baseline_definition'],
            fingerprint(book_sources.current_policy(4)), definitions)
    adopt(ro01_db)
    capabilities = generator_capabilities(ro01_db)
    with book_sources.select_registry(4):
        with connect(ro01_db) as conn:
            identities = book_store.population(conn, 'kotak-expanded')
        old = inputs(identities, method=1)
        # Publish only September before the upgrade; old October is NOT used.
        with connect(ro01_db, 'mfdata_verified_generator', autocommit=False) as conn:
            old_receipt = book_runtime.publish(conn, 'kotak-expanded', 'native-kotak-september', datetime(2026,10,17,tzinfo=timezone.utc),
                         identities, *old, availability=[])
        with connect(ro01_db) as conn:
            september = current.get(conn, 112090)
            batch_inventory = conn.execute('SELECT batch_id,document FROM mf_book_facts.batches ORDER BY batch_id').fetchall()
            cfg = book_store.installed_configuration(conn)
            graph_hash = fingerprint(dict(current.configuration(conn)))
    with connect(ro01_db, autocommit=False) as conn:
        installed = kotak_method_upgrade.install(conn, ROOT, expected_book_hash=cfg['configuration_hash'], expected_current_hash=graph_hash)
        assert installed['status'] == 'INSTALLED'
    with connect(ro01_db) as conn:
        assert current.get(conn, 112090) == september
        assert conn.execute('SELECT batch_id,document FROM mf_book_facts.batches ORDER BY batch_id').fetchall() == batch_inventory
        assert book_store.installed_configuration(conn)['document']['registry']['sources'][-1]['adapter'] == 'uti-english-monthly-book-v1'
    with book_sources.select_registry(6):
        new = inputs(identities, method=2)
        with connect(ro01_db, 'mfdata_verified_generator', autocommit=False) as conn:
            first = book_runtime.publish(conn, 'kotak-expanded', 'native-kotak-october', NOW, identities, *new, availability=[])
        with connect(ro01_db, 'mfdata_verified_generator', autocommit=False) as conn:
            assert book_runtime.publish(conn, 'kotak-expanded', 'native-kotak-october', NOW, identities, *new, availability=[]) == first
        with connect(ro01_db, 'mfdata_verified_generator', autocommit=False) as conn:
            repeated = book_runtime.publish(conn, 'kotak-expanded', 'native-kotak-october-next-poll', NOW, identities, *new, availability=[])
            assert repeated['status'] == 'unchanged'
        with connect(ro01_db) as conn:
            assert conn.execute('SELECT count(*) AS n FROM mf_book_facts.batches').fetchone()['n'] == 4
            assert conn.execute('SELECT document FROM mf_book_facts.runs WHERE run_id=%s', (old_receipt['run_id'],)).fetchone()['document'] == old_receipt
            assert current.get(conn, 112090)['fields']['aum']['value'] == '52614.58'
    # The existing bounded dataset path must still consume this recognized
    # source revision; no contract/UI change, new dataset or manual grant.
    from mfdataindia.datasets import projections, query_profile
    from psycopg import sql
    with connect(ro01_db, 'mfdata_verified_reader') as conn:
        graph = projections.inspect(conn)
        assert graph.policy_hash == fingerprint(book_sources.current_policy(6))
        entry = {'schema':'mf_live_facts', 'relation':'field_versions'}
        selected = query_profile.source(entry, graph)
        assert selected.profile == query_profile.VERSION
        original = conn.execute('SELECT * FROM mf_live_facts.field_versions').fetchall()
        optimized = conn.execute(selected.prefix + sql.SQL('SELECT * FROM {}').format(selected.relation)).fetchall()
        assert sorted(original,key=repr) == sorted(optimized,key=repr)
    assert generator_capabilities(ro01_db) == capabilities
    from psycopg.conninfo import make_conninfo
    from test_ro01_reader_policy_postgres import PASSWORD
    private, verifier = trust()
    reader_dsn = make_conninfo(ro01_db, user='mfdata_verified_reader', password=PASSWORD)
    app = create_review_app(reader_dsn, amc_books=True, current_facts=True,
                            verifier=verifier, reader_profile=profiles.BROAD)
    with TestClient(app) as client:
        assert client.get('/api/enrichment/v9/funds/112090/facts').status_code == 401
        client.headers['Authorization'] = 'Bearer ' + assertion(private, scope=['review:read'])
        response = client.get('/api/enrichment/v9/funds/112090/facts')
        assert response.status_code == 200
        assert {row['disclosure_as_of'] for row in response.json()['observations']} == {'2026-08-31','2026-09-30','2026-10-31'}
        core = client.get('/api/enrichment/v3/funds/112090/facts')
        assert core.status_code == 200
        assert core.json()['fields']['aum']['value'] == '52614.58'
    for command in ("UPDATE mf.funds SET scheme_name=scheme_name WHERE false", "CREATE TABLE mf.reader_forbidden(n integer)"):
        with connect(ro01_db, 'mfdata_verified_reader', autocommit=False) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(command)
