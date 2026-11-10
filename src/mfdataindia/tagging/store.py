"""Bounded atomic factual snapshot; no provider calls, hidden grants or mutation retry."""
from __future__ import annotations

from pathlib import Path

from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from mfdataindia.datasets import enrollment
from . import policy as p

FILE = 'sql/operations/fund_tagging_v1.sql'
SQL_SHA256 = '8b9809c3feecc6f54ac3ca145b111288566215c43c1e69af4e37b6516441c047'
TABLES = ('assessments', 'current', 'definitions', 'head', 'observations', 'snapshots')
CENSUS_SQL = '''SELECT f.amfi_scheme_code AS code,f.amc_id,a.amfi_amc_name AS amc,
 f.scheme_name,f.scheme_type,f.scheme_category,f.category_source,f.plan_type,f.plan_source,
 f.option_type,f.is_etf,f.in_scope,f.last_seen_in_source::text AS last_seen,
 v.group_key,ff.tlws_mf_id AS family_id
 FROM mf.funds f JOIN mf.amcs a ON a.amc_id=f.amc_id
 LEFT JOIN mf.fund_variants v ON v.amfi_scheme_code=f.amfi_scheme_code
 LEFT JOIN mf.fund_family ff ON ff.group_key=v.group_key
 ORDER BY f.amfi_scheme_code LIMIT 50001'''


def principal(conn, role):
    who = conn.execute('SELECT current_user AS actor,session_user AS session').fetchone()
    p.require(who['actor'] == who['session'] == role, 'TAGGING_DIRECT_PRINCIPAL_REQUIRED')
    row = conn.execute('''SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication
        OR rolbypassrls OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS unsafe
        FROM pg_roles r WHERE rolname=current_user''').fetchone()
    if role != p.OWNER:
        p.require(row and row['unsafe'] is False, 'TAGGING_UNSAFE_WRITER')
        writer_safety(conn, role)


def writer_safety(conn, role):
    # Fixed dedicated writer only. Inherited/public or column DML is also unsafe.
    unsafe = conn.execute("""SELECT
        EXISTS(SELECT 1 FROM pg_database WHERE datdba=(SELECT oid FROM pg_roles WHERE rolname=%s))
        OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspname NOT LIKE 'pg_temp_%%'
            AND has_schema_privilege(%s,oid,'CREATE'))
        OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind IN('r','p','v','m') AND n.nspname NOT IN('pg_catalog','information_schema','mf_tagging')
            AND (has_table_privilege(%s,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(%s,c.oid,'INSERT,UPDATE,REFERENCES')))
        AS unsafe""", (role, role, role, role)).fetchone()
    p.require(unsafe and unsafe['unsafe'] is False, 'TAGGING_UNSAFE_WRITER')


def limits(conn):
    conn.execute("SET LOCAL statement_timeout='5s'")
    conn.execute("SET LOCAL lock_timeout='5s'")
    conn.execute("SET LOCAL transaction_timeout='15s'")
    row = conn.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('mf-fund-tagging-v1',0)) AS ok").fetchone()
    p.require(row['ok'], 'TAGGING_PUBLICATION_BUSY')


def installed(conn):
    expected = 'fund-tagging-v1:' + SQL_SHA256 + ':' + p.digest(p.CONTRACT)
    namespace = conn.execute("SELECT pg_get_userbyid(nspowner) AS owner,obj_description(oid,'pg_namespace') AS seal FROM pg_namespace WHERE nspname='mf_tagging'").fetchone()
    p.require(namespace and namespace['owner'] == p.OWNER and namespace['seal'] == expected,
              'TAGGING_INSTALLATION_DIFFERS')
    rows = conn.execute("SELECT relname,relkind,pg_get_userbyid(relowner) AS owner FROM pg_class WHERE relnamespace='mf_tagging'::regnamespace AND relkind IN('r','p','v','m','S') ORDER BY relname").fetchall()
    p.require(rows == [{'relname': t, 'relkind': 'r', 'owner': p.OWNER} for t in TABLES],
              'TAGGING_INSTALLATION_DIFFERS')
    return expected


def install(conn, root):
    """Explicit direct-owner installation in caller transaction, never on startup."""
    import hashlib
    p.require(conn.info.transaction_status == TransactionStatus.INTRANS,
              'TAGGING_OUTER_TRANSACTION_REQUIRED')
    principal(conn, p.OWNER)
    limits(conn)
    raw = (Path(root) / FILE).read_bytes()
    p.require(hashlib.sha256(raw).hexdigest() == SQL_SHA256, 'TAGGING_INSTALLATION_SOURCE_DIFFERS')
    if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname='mf_tagging'").fetchone():
        installed(conn)
        return {'state': 'ALREADY_INSTALLED', 'source_rows_changed': 0}
    writer = conn.execute('''SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
        OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS unsafe
        FROM pg_roles r WHERE rolname=%s''', (p.WRITER,)).fetchone()
    p.require(writer and writer['unsafe'] is False, 'TAGGING_NARROW_WRITER_REQUIRED')
    writer_safety(conn, p.WRITER)
    conn.execute(raw.decode())
    seal = 'fund-tagging-v1:' + SQL_SHA256 + ':' + p.digest(p.CONTRACT)
    conn.execute(sql.SQL('COMMENT ON SCHEMA mf_tagging IS {}').format(sql.Literal(seal)))
    for table in ('definitions', 'current'):
        enrollment.enroll(conn, 'mf_tagging', table, classification='fund_research',
                          title='Fund tag ' + table)
    installed(conn)
    return {'state': 'INSTALLED', 'source_rows_changed': 0, 'commit_performed': False}


def census(conn):
    return p.prepare([dict(row) for row in conn.execute(CENSUS_SQL).fetchall()])


def _insert(conn, table, rows):
    for offset in range(0, len(rows), 500):
        conn.execute(sql.SQL('INSERT INTO mf_tagging.{} SELECT * FROM jsonb_populate_recordset(NULL::mf_tagging.{},%s) ON CONFLICT DO NOTHING').format(
            sql.Identifier(table), sql.Identifier(table)), (Jsonb(rows[offset:offset+500]),))


def publish(conn):
    """Own exactly one transaction; an escaping COMMIT error is not replayable.

    Snapshot all stored schemes, including out-of-scope/unknowns. The run hash
    binds the complete database observation and policy, never a caller payload.
    No partial cohort, arbitrary classifier result or alternate identity accepted.
    """
    p.require(conn.info.transaction_status == TransactionStatus.IDLE,
              'TAGGING_IDLE_CONNECTION_REQUIRED')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        limits(conn)
        principal(conn, p.WRITER)
        installed(conn)
        candidate = census(conn)
        snapshot = candidate['snapshot_id']
        head = conn.execute('SELECT snapshot_id FROM mf_tagging.head WHERE singleton').fetchone()
        if head and head['snapshot_id'] == snapshot:
            return {'state': 'UNCHANGED', 'snapshot_id': snapshot, 'source_rows_changed': 0,
                    'consumer_acceptance': 'NOT_OBSERVED'}
        # An old snapshot cannot be restored as a silent head rollback.
        p.require(not conn.execute('SELECT 1 FROM mf_tagging.snapshots WHERE snapshot_id=%s',
                                  (snapshot,)).fetchone(), 'TAGGING_PRIOR_SNAPSHOT_REQUIRES_REVIEW')
        report = {k: v for k, v in candidate.items() if k not in {'definitions', 'assessments', 'observations'}}
        conn.execute('INSERT INTO mf_tagging.snapshots(snapshot_id,source_hash,policy_hash,funds,report) VALUES(%s,%s,%s,%s,%s)',
                     (snapshot, candidate['source_hash'], candidate['policy_hash'], candidate['funds'], Jsonb(report)))
        _insert(conn, 'definitions', candidate['definitions'])
        _insert(conn, 'observations', [{'snapshot_id': snapshot, 'code': r['code'],
                                      'document': r} for r in candidate['observations']])
        historical = [{'snapshot_id': snapshot, 'code': a['code'], 'dimension': a['dimension'],
                       'document': a} for a in candidate['assessments']]
        _insert(conn, 'assessments', historical)
        conn.execute('DELETE FROM mf_tagging.current')
        _insert(conn, 'current', [{k: r[k] for k in ('code', 'dimension', 'document')} for r in historical])
        actual = conn.execute('SELECT code,dimension,document FROM mf_tagging.current ORDER BY code,dimension').fetchall()
        expected = sorted(({k: r[k] for k in ('code', 'dimension', 'document')} for r in historical),
                          key=lambda r: (r['code'], r['dimension']))
        p.require(actual == expected, 'TAGGING_PUBLICATION_INCOMPLETE')
        conn.execute('INSERT INTO mf_tagging.head VALUES(true,%s) ON CONFLICT(singleton) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id', (snapshot,))
        return {'state': 'PUBLISHED', **report, 'source_rows_changed': 0}
