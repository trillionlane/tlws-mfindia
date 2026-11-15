"""Hosted disposable PostgreSQL only. ASGI evidence is not live OIDC evidence."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from time import perf_counter
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from mfdataindia.datasets.app import create_app
from mfdataindia.datasets import enrollment
from mfdataindia.nse import contract as c, store
from mfdataindia.nse.cli import main
from nse01_fixtures import FIXTURES, MANIFEST, RAW, WALK, candidate, change, write_full_fixture
from test_ro01_reader_policy_postgres import (
    ro01_db as ro01_db, connect, adopt, PASSWORD, generator_capabilities,
)

pytestmark = pytest.mark.postgres


# Independent, precomputed manifest pins for the other disposable transaction
# scenarios. None is installed in the packaged importer.
@pytest.fixture
def trusted_scenarios(monkeypatch):
    def enable():
        pins = {
            '1272721f4f453d508c849c7009b5bda2c6877058bc2fd5546b3f4798df716251',  # replay reference
            '199c150e56f2c21a538069d2906fb4bf8dff5d85a2edf534071a7b7a5d04baaf',  # revision
            '21ece0050a8b526a920f66dcbe7a75207945a4805fbcedd5a3bcf0f3ad968a68',  # unknown activity
            'a971d73e40907139088fda4880685095c9e7a7baaeee63ce54d28ad4bc6efe3d',  # removal
            '3a93289bd39d68b2c9f142997aa3013f6c7ac8d44ff48d1004f83bcf4acfa5c4',  # historical
            '4e25e80b8fdf4069fe492c7b5b45ce5019b45ecb21b7361c80e1443a85d0c3f6',  # stale head
            '0e7ff50c96d261a97551715c262a64717bd9836bc7c90f2abe620d4234e55589',  # date conflict
            '35e13df91e87b73277645277f394c6b8063183c0850a75ca4ce464338e08f878',  # duplicates
        }
        monkeypatch.setattr(c, '_TRUSTED_FULL_MANIFESTS', c._TRUSTED_FULL_MANIFESTS | pins)

    return enable


@pytest.fixture
def nse_db(ro01_db):
    adopt(ro01_db)
    with connect(ro01_db,autocommit=False) as conn:
        conn.execute("UPDATE mf.funds SET isin_growth_or_div_payout='INFX00000001' WHERE amfi_scheme_code=1")
        conn.execute("UPDATE mf.funds SET isin_growth_or_div_payout='INFX00000002',isin_div_reinvest='INFX00000003' WHERE amfi_scheme_code=2")
        conn.execute("""INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,
            scheme_type,scheme_category,plan_type,plan_source,option_type,isin_growth_or_div_payout)
            VALUES(3,1,'Direct fixture','DIRECT FIXTURE','OPEN_ENDED','Equity','DIRECT','COLUMN','GROWTH','INFX00000004')""")
        conn.execute("INSERT INTO mf.nav_history(amfi_scheme_code,nav_date,nav,source) VALUES(1,'2026-11-09',12.3456,'AMFI')")
        conn.execute("INSERT INTO mf.nav_history(amfi_scheme_code,nav_date,nav,source) VALUES(2,'2026-11-09',12345678901234.1234,'AMFI')")
        first = store.install(conn)
        second = store.install(conn)
        assert first['reader_permissions_verified'] and second['metadata_writes']==0
    return ro01_db


def ingest(dsn, value=None, *, previous=None, operation=None):
    conn=connect(dsn,'mfdata_ingest')
    conn.autocommit=False
    try:
        return store.publish(conn, value or candidate(), WALK, operation or str(uuid4()),expected_head=previous)
    finally:
        conn.close()


def head(dsn):
    with connect(dsn) as conn:
        result=conn.execute("SELECT snapshot_id,generation FROM mf_nse.family_heads WHERE family='SCH'").fetchone()
        return str(result['snapshot_id']) if result['snapshot_id'] else None,result['generation']


def api(dsn):
    return TestClient(create_app(make_conninfo(dsn,user='mfdata_verified_reader',password=PASSWORD)))


def page(client):
    reply=client.get('/api/datasets/v1/'+store.DATASET_ID+'/rows',params={'limit':100})
    assert reply.status_code==200,reply.json()
    return reply.json()


@pytest.mark.parametrize('ro01_db',['core'],indirect=True)
def test_native_first_import_reentry_revision_removal_full_gate_and_api(nse_db,tmp_path,monkeypatch,trusted_scenarios):
    dsn=nse_db
    # Exact NUMERIC(18,4) preservation, including values whose adjacent decimal
    # lexemes collapse to the same binary float. This fixture mutation is rolled
    # back; it is not a correction of any real NAV or a financial calculation.
    with connect(dsn,autocommit=False) as conn:
        exact_before=store._fingerprint(conn)
        conn.execute("UPDATE mf.nav_history SET nav=12345678901234.1235 WHERE amfi_scheme_code=2")
        assert store._fingerprint(conn)!=exact_before
        conn.rollback()
    with connect(dsn) as conn:
        canonical_before=store._fingerprint(conn)
    generator_before=generator_capabilities(dsn)
    start=perf_counter()
    # Real main() + real connection + parser/crosswalk + production publication.
    full_file, full_manifest = write_full_fixture(tmp_path)
    output=[]
    op=str(uuid4())
    code=main(['--import-file','--file',str(full_file),
        '--manifest',str(full_manifest), '--crosswalk',str(FIXTURES/'crosswalk.json'),
        '--operation',op,'--journal-dir',str(tmp_path)],
        environ={'MFDATAINDIA_DSN':make_conninfo(dsn,user='mfdata_ingest',password=PASSWORD)},output=output.append)
    assert code==0,output
    first=json.loads(output[-1])
    assert first['effect']=='COMMITTED_VERIFIED' and first['state']=='PUBLISHED_WITH_LIMITATIONS'
    assert first['source_rows']==7 and first['exposed_rows']==3 and first['withheld_rows']==4
    original=head(dsn)
    assert original==(first['snapshot_id'],1)
    with connect(dsn) as conn:
        assert conn.execute('SELECT count(*) AS n FROM mf_nse.sch_rows').fetchone()['n']==7
        assert conn.execute('SELECT count(*) AS n FROM mf_nse.variant_mappings').fetchone()['n']==7
        assert conn.execute('SELECT original_bytes FROM mf_nse.source_artifacts').fetchone()['original_bytes']==RAW
        assert conn.execute("SELECT original_cells->>0 AS value FROM mf_nse.sch_rows WHERE row_ordinal=1").fetchone()['value']=='00000000001'
    with api(dsn) as client:
        discovery=client.get('/api/datasets/v1')
        assert discovery.status_code==200 and any(r['id']==store.DATASET_ID and r['state']=='available' for r in discovery.json()['datasets'])
        metadata=client.get('/api/datasets/v1/'+store.DATASET_ID)
        assert metadata.status_code==200
        assert not {'original_cells','manifest','crosswalk','operation_id'} & {r['name'] for r in metadata.json()['columns']}
        original_page=page(client)
        assert len(original_page['rows'])==3
        assert {r['isin_slot'] for r in original_page['rows']}=={'isin_growth_or_div_payout','isin_div_reinvest'}
        assert all(r['reference_fields']['maximum_purchase_amount'] is None and r['eligibility']=='UNKNOWN' for r in original_page['rows'])
        assert {r['reference_fields']['source_scheme_name'] for r in original_page['rows']}=={'FIXTURE SCH_G','FIXTURE SCH_P','FIXTURE SCH_R'}
        assert all(r['reference_fields']['source_start_date']=='2020-01-01' and
                   r['reference_fields']['purchase_cutoff_text']=='15:00:00' and
                   r['reference_fields']['purchase_cutoff_instant'] is None for r in original_page['rows'])
        assert all('rta_scheme_code' not in r['reference_fields'] and
                   r['limitations']['rta_scheme_code']=='SCH_SOURCE_METADATA' for r in original_page['rows'])
        first_page=client.get('/api/datasets/v1/'+store.DATASET_ID+'/rows',params={'limit':2}).json()
        second=client.get('/api/datasets/v1/'+store.DATASET_ID+'/rows',params={'limit':2,'cursor':first_page['next_cursor']}).json()
        assert len(first_page['rows'])==2 and len(second['rows'])==1 and second['next_cursor'] is None
        assert len({r['id'] for r in first_page['rows']+second['rows']})==3
        assert client.get('/api/datasets/v1/'+store.DATASET_ID+'/rows?limit=101').status_code==422
        assert client.post('/api/datasets/v1/'+store.DATASET_ID+'/rows',json={}).status_code==405
        # Stop after one complete valid row and try both the approved manifest
        # and caller metadata with all three expectations recalculated.
        truncated = tmp_path / 'truncated.psv'
        clean = b'\n'.join(RAW.splitlines()[:2]) + b'\n'
        truncated.write_bytes(clean)
        forged = tmp_path / 'forged.json'
        forged.write_bytes(c.canonical({
            **MANIFEST, 'expected_sha256': sha256(clean).hexdigest(),
            'expected_bytes': len(clean), 'expected_rows': 1,
        }))

        def forbidden(*a, **kw):
            pytest.fail('incomplete file connected')

        for manifest_file, reason in ((full_manifest, 'FULL_BYTES_MISMATCH'),
                                      (forged, 'FULL_MANIFEST_UNTRUSTED')):
            emitted = []
            bad_op = str(uuid4())
            assert main(['--import-file', '--file', str(truncated),
                '--manifest', str(manifest_file), '--crosswalk', str(FIXTURES / 'crosswalk.json'),
                '--operation', bad_op, '--journal-dir', str(tmp_path)],
                environ={'MFDATAINDIA_DSN': make_conninfo(dsn, user='mfdata_ingest', password=PASSWORD)},
                connect=forbidden, output=emitted.append) == 1
            report = json.loads(emitted[-1])
            assert report['reason'] == reason and report['effect'] == 'NO_PUBLICATION'
            assert not (tmp_path / (bad_op + '.json')).exists()
            assert head(dsn) == original and page(client) == original_page
            assert client.get('/api/datasets/v1').json() == discovery.json()
            assert client.get('/api/datasets/v1/' + store.DATASET_ID).json() == metadata.json()
        trusted_scenarios()
        # Simulated preservation mismatch is inside the real transaction AFTER
        # staging deletion/inserts; rollback must restore every previous row.
        original_fingerprint=store._fingerprint
        calls=[]
        def mismatch(conn):
            value=original_fingerprint(conn)
            calls.append(value)
            return value if len(calls)==1 else '0'*64
        with monkeypatch.context() as patch:
            patch.setattr(store,'_fingerprint',mismatch)
            with pytest.raises(store.OutcomeError) as failure:
                ingest(dsn,candidate(change(column=11,value='200')),previous=original[0])
            assert failure.value.report['effect']=='ROLLBACK_CONFIRMED'
            assert failure.value.report['reason']=='CANONICAL_PRESERVATION_FAILED'
        assert head(dsn)==original and page(client)==original_page
    same=ingest(dsn,candidate(reference='new-observation-same-publication'),previous=original[0])
    assert same['state']=='UNCHANGED' and same['business_rows_created']==0 and head(dsn)==original
    with connect(dsn) as conn:
        assert conn.execute('SELECT count(*) AS n FROM mf_nse.snapshots').fetchone()['n']==1
        assert conn.execute('SELECT count(*) AS n FROM mf_nse.sch_rows').fetchone()['n']==7
        assert conn.execute('SELECT count(*) AS n FROM mf_nse.observations').fetchone()['n']==2
    revised=ingest(dsn,candidate(change(column=11,value='200')),previous=original[0])
    assert revised['snapshot_id']!=original[0] and head(dsn)[1]==2
    with connect(dsn) as conn:
        assert conn.execute('SELECT count(*) AS n FROM mf_nse.snapshots').fetchone()['n']==2
        assert conn.execute('SELECT count(*) AS n FROM mf_nse.sch_rows').fetchone()['n']==14
    unknown_activity=ingest(dsn,candidate(change(column=26,value='2')),previous=revised['snapshot_id'])
    assert unknown_activity['exposed_rows']==3 and head(dsn)[1]==3
    with api(dsn) as client:
        data=page(client)['rows']
        held=next(r for r in data if r['nse_scheme_code']=='SCH_G')
        assert held['reference_fields']['amc_active'] is None
        assert held['limitations']['amc_active']=='SCH_FLAGS_MODES'
        assert held['eligibility']=='UNKNOWN' and len(data)==3
    # FULL replacement removes one source row, disables another; old history
    # survives and canonical funds are never "retired" by NSE source absence.
    replacement=change(column=26,value='0')
    lines=replacement.splitlines()
    replacement=b'\n'.join([lines[0],*lines[1:2],*lines[3:]])+b'\n'
    removed=ingest(dsn,candidate(replacement),previous=unknown_activity['snapshot_id'])
    assert removed['exposed_rows']==1 and head(dsn)[1]==4
    with api(dsn) as client:
        assert len(page(client)['rows'])==1
    # Older evidence can be retained but cannot replace the active FULL head.
    active=head(dsn)
    historical=ingest(dsn,candidate(change(column=11,value='300'),source_date='2026-11-09'),previous=active[0])
    assert historical['state']=='HISTORICAL_ONLY' and head(dsn)==active
    with connect(dsn,autocommit=False) as conn:
        assert store._fingerprint(conn)==canonical_before
        assert str(conn.execute('SELECT nav FROM mf.nav_history WHERE amfi_scheme_code=2').fetchone()['nav'])=='12345678901234.1234'
        assert conn.execute('SELECT nav,nav_date FROM mf.nav_history WHERE amfi_scheme_code=1').fetchone()['nav_date']==date(2026,11,9)
        assert store.install(conn)['metadata_writes']==0
    assert generator_capabilities(dsn)==generator_before
    print(json.dumps({'NSE01_A_native_vertical_seconds':round(perf_counter()-start,4),
                      'native_canonical_AMFI_NAV_preserved':True,'live_authentication_or_NSE_evidence':False}))


@pytest.mark.parametrize('ro01_db',['core'],indirect=True)
def test_native_reader_denials_private_evidence_missing_grants_unsafe_identity(nse_db):
    dsn=nse_db
    ingest(dsn)
    statements=("INSERT INTO mf_nse.current_sch(id) VALUES(gen_random_uuid())",
                'UPDATE mf_nse.current_sch SET eligibility=\'UNKNOWN\'',
                'DELETE FROM mf_nse.current_sch','CREATE TABLE mf_nse.forbidden(id int)',
                'ALTER TABLE mf_nse.current_sch ADD COLUMN forbidden int',
                'ALTER ROLE mfdata_verified_reader CREATEDB',
                'SELECT * FROM mf_nse.sch_rows','SELECT * FROM mf_nse.source_artifacts',
                'SELECT * FROM mf_nse.variant_mappings','SELECT * FROM mf_nse.import_attempts')
    for statement in statements:
        with connect(dsn,'mfdata_verified_reader') as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    with api(dsn) as client:
        assert client.get('/api/datasets/v1/d_'+'0'*32+'/rows').status_code==404
        with connect(dsn) as conn:
            conn.execute('REVOKE SELECT ON mf_nse.current_sch FROM mfdata_verified_reader')
        assert client.get('/api/datasets/v1/'+store.DATASET_ID+'/rows').status_code==503
        with connect(dsn,autocommit=False) as conn:
            enrollment.reconcile(conn)
        assert len(page(client)['rows'])==3
        with connect(dsn) as conn:
            conn.execute('GRANT UPDATE ON mf_nse.current_sch TO mfdata_verified_reader')
        assert client.get('/api/datasets/v1/'+store.DATASET_ID+'/rows').status_code==503
        with connect(dsn) as conn:
            conn.execute('REVOKE UPDATE ON mf_nse.current_sch FROM mfdata_verified_reader')
    with TestClient(create_app(make_conninfo(dsn,user='mfdata_verified_generator',password=PASSWORD))) as client:
        assert client.get('/api/datasets/v1/'+store.DATASET_ID+'/rows').status_code==503
    with connect(dsn,'mfdata_verified_reader',autocommit=False) as conn:
        with pytest.raises(c.ImportError,match='INSTALL_TRANSACTION_REQUIRED|IMPORTER_IDENTITY_UNSAFE'):
            conn.execute('SELECT 1')
            store.install(conn)


@pytest.mark.parametrize('ro01_db',['core'],indirect=True)
def test_native_concurrent_same_work_one_publisher_and_no_duplicate(nse_db):
    def run():
        try:
            return ingest(nse_db)
        except store.OutcomeError as error:
            return error.report
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs=list(pool.map(lambda _:run(),range(2)))
    assert sum(r['effect']=='COMMITTED_VERIFIED' and r['state']=='PUBLISHED_WITH_LIMITATIONS' for r in outputs)==1
    assert all(r['state'] in {'PUBLISHED_WITH_LIMITATIONS','UNCHANGED','PUBLICATION_ROLLED_BACK'} for r in outputs)
    assert head(nse_db)[1]==1
    with connect(nse_db) as conn:
        assert conn.execute('SELECT count(*) AS n FROM mf_nse.current_sch').fetchone()['n']==3
        assert conn.execute('SELECT count(*) AS n FROM mf_nse.sch_rows').fetchone()['n']==7
    assert ingest(nse_db,previous=head(nse_db)[0])['state']=='UNCHANGED'


@pytest.mark.parametrize('ro01_db',['core'],indirect=True)
def test_native_commit_unknown_inspect_only_and_stale_head_preservation(nse_db,trusted_scenarios):
    op=str(uuid4())
    real=connect(nse_db,'mfdata_ingest')
    real.autocommit=False
    class LostCommitResponse:
        def __getattr__(self,name):
            return getattr(real,name)
        def commit(self):
            real.commit()  # Real PostgreSQL commits, response loss is synthetic.
            raise OSError('synthetic-secret-marker')
    with pytest.raises(store.OutcomeError) as failure:
        store.publish(LostCommitResponse(),candidate(),WALK,op,expected_head=None)
    assert failure.value.report['state']=='COMMIT_UNKNOWN' and failure.value.report['effect']=='UNKNOWN'
    assert 'synthetic-secret-marker' not in json.dumps(failure.value.report)
    real.close()
    with connect(nse_db,'mfdata_ingest') as conn:
        conn.autocommit=False
        observed=store.inspect(conn,op)
        assert observed['effect']=='COMMITTED_VERIFIED'
    original=head(nse_db)
    trusted_scenarios()
    with pytest.raises(store.OutcomeError) as stale:
        ingest(nse_db,candidate(change(column=11,value='400')),previous=None)
    assert stale.value.report['reason']=='HEAD_CHANGED' and head(nse_db)==original
    with pytest.raises(store.OutcomeError) as spent:
        ingest(nse_db,operation=op,previous=original[0])
    assert spent.value.report['reason']=='OPERATION_SPENT' and head(nse_db)==original
    with pytest.raises(store.OutcomeError) as date_conflict:
        ingest(nse_db,candidate(source_date='2026-11-11'),previous=original[0])
    assert date_conflict.value.report['reason']=='UNCHANGED_BYTES_DATE_CONFLICT' and head(nse_db)==original


@pytest.mark.parametrize('ro01_db',['core'],indirect=True)
def test_native_exact_mapping_ambiguity_and_duplicate_accounting(nse_db,trusted_scenarios):
    trusted_scenarios()
    walk=deepcopy(WALK)
    walk['targets'].append({**walk['targets'][0],'amfi_scheme_code':4})
    with connect(nse_db,autocommit=False) as conn:
        conn.execute("""INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,
            scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout)
            VALUES(4,1,'Duplicate exact identity','DUPLICATE EXACT IDENTITY','OPEN_ENDED','Equity','REGULAR','GROWTH','INFX00000001')""")
    data=candidate(RAW+RAW.splitlines()[2]+b'\n')
    with connect(nse_db,'mfdata_ingest') as conn:
        conn.autocommit=False
        result=store.publish(conn,data,walk,str(uuid4()),expected_head=None)
    assert result['source_rows']==8 and result['exposed_rows']==2
    assert result['duplicate_rows']==1 and result['withheld_rows']==5
    assert result['source_rows']==result['exposed_rows']+result['duplicate_rows']+result['withheld_rows']
    with connect(nse_db) as conn:
        assert conn.execute("SELECT disposition FROM mf_nse.variant_mappings WHERE row_ordinal=1").fetchone()['disposition']=='AMBIGUOUS'
        assert conn.execute('SELECT count(*) AS n FROM mf_nse.sch_rows').fetchone()['n']==8
