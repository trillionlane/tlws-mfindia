"""One owner transaction installs the operational gate and current composition.

This is not an ingestion worker migration. Re-entry checks the sealed installed
catalogue and returns; drift is rejected before another mutation. No business
records, financial rules, consumer ACLs or historical numbered SQL are changed.
"""
import hashlib
from pathlib import Path

from mfdataindia.enrichment import book_sources, book_store, kotak_method_upgrade
from mfdataindia.enrichment.monthly_projection import owner
from mfdataindia.review.policy import fingerprint

FILE = 'sql/operations/amc_ingestion_gate_v1.sql'
SHA256 = 'b755104f8d24074302e370353650b42e2361db48ef7e55a699ce4051dd4ef690'
PREFIX = 'amc-ingestion-gate-v1:'

CATALOGUE = '''SELECT jsonb_build_object(
 'schema',(SELECT jsonb_build_array(nspname,nspowner,nspacl) FROM pg_namespace WHERE nspname='mf_ingestion_ops'),
 'relations',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.relname) FROM
   (SELECT c.relname,c.relkind,c.relowner,c.relacl,c.relrowsecurity,c.relforcerowsecurity
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_ingestion_ops') c),
 'columns',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.relname,a.attnum) FROM
   (SELECT c.relname,a.attnum,a.attname,a.atttypid,a.atttypmod,a.attnotnull,a.attacl,a.attidentity,a.attgenerated,
      pg_get_expr(d.adbin,d.adrelid) AS expression
    FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
    LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
    WHERE n.nspname='mf_ingestion_ops' AND a.attnum>0 AND NOT a.attisdropped) a),
 'constraints',(SELECT jsonb_agg(to_jsonb(q) ORDER BY q.relname,q.conname) FROM
   (SELECT c.relname,x.conname,pg_get_constraintdef(x.oid) AS definition,x.convalidated
    FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='mf_ingestion_ops') q),
 'indexes',(SELECT jsonb_agg(to_jsonb(q) ORDER BY q.indexname) FROM
   (SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='mf_ingestion_ops') q),
 'functions',(SELECT jsonb_agg(to_jsonb(q) ORDER BY q.proname) FROM
   (SELECT p.proname,p.proowner,p.proacl,p.proconfig,p.prosecdef,pg_get_functiondef(p.oid) AS definition
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mf_ingestion_ops') q),
 'triggers',(SELECT jsonb_agg(to_jsonb(q) ORDER BY q.relname,q.tgname) FROM
   (SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid) AS definition
    FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='mf_ingestion_ops' AND NOT t.tgisinternal) q),
 'policies',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.tablename,p.policyname) FROM pg_policies p WHERE schemaname='mf_ingestion_ops'),
 'defaults',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.defaclrole,d.defaclobjtype) FROM pg_default_acl d
    JOIN pg_namespace n ON n.oid=d.defaclnamespace WHERE n.nspname='mf_ingestion_ops')
) AS value'''


def verify(conn):
    row = conn.execute("SELECT pg_get_userbyid(nspowner) AS owner,obj_description(oid,'pg_namespace') AS seal FROM pg_namespace WHERE nspname='mf_ingestion_ops'").fetchone()
    if row is None or row['owner'] != 'mfdata_admin':
        raise ValueError('INGESTION_GATE_NOT_INSTALLED')
    expected = PREFIX+SHA256+':'+fingerprint(conn.execute(CATALOGUE).fetchone()['value'])
    if row['seal'] != expected:
        raise ValueError('INGESTION_GATE_INSTALLED_STATE_DIFFERS')
    return {'status':'INSTALLED_STATE_VERIFIED','catalogue_hash':expected.rsplit(':',1)[1]}


def install(conn, root, *, expected_book_hash, expected_current_hash):
    with conn.transaction():
        owner(conn)
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('amc-ingestion-gate-install-v1',0))")
        installed = book_store.installed_configuration(conn)
        exists = conn.execute("SELECT 1 FROM pg_namespace WHERE nspname='mf_ingestion_ops'").fetchone()
        if installed['document'] == book_sources.configuration(7):
            verify(conn)
            return {'status':'ALREADY_INSTALLED','business_rows_changed':0}
        if exists or installed['document'] != book_sources.configuration(6):
            raise ValueError('INGESTION_GATE_INSTALLATION_BASE_DIFFERS')
        raw = (Path(root)/FILE).read_bytes()
        if hashlib.sha256(raw).hexdigest() != SHA256:
            raise ValueError('INGESTION_GATE_INSTALLATION_SOURCE_DIFFERS')
        transition = kotak_method_upgrade.install(conn, root, expected_book_hash=expected_book_hash,
            expected_current_hash=expected_current_hash, source_version=6, target_version=7)
        conn.execute(raw.decode())
        seal = PREFIX+SHA256+':'+fingerprint(conn.execute(CATALOGUE).fetchone()['value'])
        from psycopg import sql
        conn.execute(sql.SQL('COMMENT ON SCHEMA mf_ingestion_ops IS {}').format(sql.Literal(seal)))
        verify(conn)
        return {'status':'INSTALLED','transition':transition,'business_rows_changed':0}
