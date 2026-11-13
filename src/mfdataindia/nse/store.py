"""Atomic offline SCH publication. No enrollment/grants in routine ingestion.

One caller-supplied direct importer connection; one COMMIT. Ambiguous commit is
inspect-only. Neither rollback nor close errors may erase the strongest effect.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid5

from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from mfdataindia.datasets import enrollment, policy as datasets
from . import contract as c, mapping, parser

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / 'sql/optional/045_nse_sch_offline.sql'
if not MIGRATION.is_file():  # Existing non-editable APP/TOOLS layout.
    MIGRATION = Path('/app/sql/optional/045_nse_sch_offline.sql')
DATASET_ID = datasets.dataset_id('mf_nse', 'current_sch')
NAMESPACE = UUID('7106831f-68c1-43cf-b21e-cbcd2ef791da')
FUNDS = """SELECT f.amfi_scheme_code,a.amfi_amc_name,f.plan_type,f.option_type,f.in_scope,
    f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text
    FROM mf.funds f JOIN mf.amcs a ON a.amc_id=f.amc_id ORDER BY f.amfi_scheme_code"""
PRESERVE = """SELECT relation,value FROM (SELECT 'funds' AS relation,to_jsonb(t)::text AS value FROM mf.funds t
    UNION ALL SELECT 'amcs',to_jsonb(t)::text FROM mf.amcs t
    UNION ALL SELECT 'nav',to_jsonb(t)::text FROM mf.nav_history t) preserved
    ORDER BY 1,value"""
LIMITS = "SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='5s'; SET LOCAL transaction_timeout='15s'"


class OutcomeError(c.ImportError):
    def __init__(self, report):
        super().__init__(report['reason'])
        self.report = report


def _identity(conn, owner=False):
    row = conn.execute("""SELECT current_database() AS database,current_user AS principal,
        session_user AS session,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,
        EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS membership
        FROM pg_roles r WHERE rolname=current_user""").fetchone()
    c.require(row and row['database'] == 'mfdataindia'
              and row['principal'] == row['session'] == ('mfdata_admin' if owner else 'mfdata_ingest')
              and not any(row[k] for k in ('rolsuper', 'rolcreatedb', 'rolcreaterole', 'rolreplication',
                                          'rolbypassrls', 'membership')), 'IMPORTER_IDENTITY_UNSAFE')


def install(conn):
    """Explicit direct-owner transaction hook, never called by import/main()."""
    c.require(conn.info.transaction_status == TransactionStatus.INTRANS, 'INSTALL_TRANSACTION_REQUIRED')
    _identity(conn, owner=True)
    expected = sha256(MIGRATION.read_bytes()).hexdigest()
    exists = conn.execute("SELECT to_regnamespace('mf_nse') AS value").fetchone()['value']
    if exists:
        _configuration(conn, expected)
    else:
        conn.execute(MIGRATION.read_text())
        conn.execute('INSERT INTO mf_nse.configuration VALUES(true,%s,%s)', ('nse01-sch-v1', expected))
    registered = enrollment.enroll(conn, 'mf_nse', 'current_sch', classification='fund_research',
                                   title='Offline NSE SCH reference (fixture verified; eligibility unknown)')
    report = enrollment.reconcile(conn)
    own = next(r for r in report['results'] if r['dataset_id'] == DATASET_ID)
    c.require(own['permissions_verified'], 'READER_RECONCILIATION_FAILED')
    return {'dataset_id': DATASET_ID, 'metadata_writes': registered['metadata_writes'],
            'reader_permissions_verified': True, 'consumer_ready': False}


def _configuration(conn, expected=None):
    expected = expected or sha256(MIGRATION.read_bytes()).hexdigest()
    row = conn.execute('SELECT version,definition_hash FROM mf_nse.configuration WHERE singleton').fetchone()
    c.require(row and row == {'version': 'nse01-sch-v1', 'definition_hash': expected}, 'INSTALLATION_DIFFERS')


def _fingerprint(conn):
    # Cursor iteration avoids retaining a second full NAV history in memory.
    h = sha256()
    for row in conn.execute(PRESERVE):
        h.update(c.canonical(row))
        h.update(b'\n')
    return h.hexdigest()


def _stage(conn, parsed, walk, operation_id, expected_head):
    _identity(conn)
    _configuration(conn)
    # REPEATABLE READ pins mapping/preservation values. ACCESS SHARE protects
    # their definitions without requiring new canonical write privileges or
    # blocking unrelated ingestion. This is a versioned snapshot, not a claim
    # that canonical identities cannot change after this transaction commits.
    conn.execute('LOCK TABLE mf.funds,mf.amcs,mf.nav_history IN ACCESS SHARE MODE')
    head = conn.execute("SELECT snapshot_id,generation FROM mf_nse.family_heads WHERE family='SCH' FOR UPDATE").fetchone()
    c.require(head is not None, 'INSTALLATION_DIFFERS')
    actual_head = str(head['snapshot_id']) if head['snapshot_id'] else None
    artifact = conn.execute('SELECT manifest FROM mf_nse.source_artifacts WHERE content_hash=%s',
                            (parsed.manifest['expected_sha256'],)).fetchone()
    c.require(not artifact or artifact['manifest']['source_date'] == parsed.manifest['source_date'],
              'UNCHANGED_BYTES_DATE_CONFLICT')
    funds = conn.execute(FUNDS).fetchall()
    mapping_hash = c.digest(funds)
    work_meta = {k: v for k, v in parsed.manifest.items() if k != 'reference'}
    work = c.digest({'manifest': work_meta, 'crosswalk': walk, 'mapping_input': mapping_hash,
                     'target': 'mfdataindia.mf_nse.SCH', 'parser': 'nse01-sch-v1'})
    prior = conn.execute('SELECT operation_id,snapshot_id,report FROM mf_nse.import_attempts WHERE work_key=%s', (work,)).fetchone()
    # Same work may be observed again, but an operation identity is never reset.
    c.require(not conn.execute('SELECT 1 FROM mf_nse.observations WHERE operation_id=%s', (operation_id,)).fetchone(), 'OPERATION_SPENT')
    if prior:
        conn.execute('INSERT INTO mf_nse.observations(operation_id,snapshot_id,manifest,disposition) VALUES(%s,%s,%s,%s)',
                     (operation_id, prior['snapshot_id'], Jsonb(parsed.manifest), 'UNCHANGED'))
        return {**prior['report'], 'operation_id': operation_id, 'state': 'UNCHANGED', 'business_rows_created': 0,
                'head_advanced': False}
    c.require(actual_head == expected_head, 'HEAD_CHANGED')
    before = _fingerprint(conn)
    resolved = [mapping.resolve(row, walk, funds) for row in parsed.rows]
    artifact_id = uuid5(NAMESPACE, parsed.manifest['expected_sha256'])
    snapshot_id = uuid5(NAMESPACE, work)
    current = {}
    for row in resolved:
        if row['status'] != 'MAPPED':
            continue
        cells, candidate = row['cells'], row['candidates'][0]
        key = c.digest([cells[5], cells[1], cells[4], cells[7], cells[10], cells[17]])
        current.setdefault(key, (row, candidate))  # Identical duplicate evidence retained below.
    withheld = Counter(r['row_reason'] for r in resolved if r['status'] != 'MAPPED')
    field_holds = Counter(reason for r in resolved for reason in r['field_holds'].values())
    source_date = parsed.manifest['source_date']
    old_date = conn.execute('SELECT source_date FROM mf_nse.snapshots WHERE snapshot_id=%s', (head['snapshot_id'],)).fetchone() if head['snapshot_id'] else None
    historical = bool(old_date and source_date < old_date['source_date'].isoformat())
    state = 'HISTORICAL_ONLY' if historical else 'PUBLISHED_WITH_LIMITATIONS' if withheld or field_holds else 'PUBLISHED'
    report = {'format': 'nse01-import-result-v1', 'operation_id': operation_id, 'snapshot_id': str(snapshot_id),
              'state': state, 'source_rows': len(resolved), 'exposed_rows': len(current),
              'withheld_rows': sum(withheld.values()), 'duplicate_rows': sum(r['status'] == 'MAPPED' for r in resolved) - len(current),
              'row_reasons': dict(withheld), 'withheld_fields': sum(field_holds.values()),
              'field_reasons': dict(field_holds), 'source_sha256': parsed.manifest['expected_sha256'],
              'mapping_input_sha256': mapping_hash, 'crosswalk_sha256': c.digest(walk),
              'manifest_sha256': c.digest(parsed.manifest), 'business_rows_created': len(current),
              'head_advanced': not historical, 'dataset_id': DATASET_ID,
              'consumer_acceptance': 'NOT_OBSERVED', 'live_source_verified': False}
    conn.execute('INSERT INTO mf_nse.source_artifacts(artifact_id,content_hash,original_bytes,manifest) VALUES(%s,%s,%s,%s) ON CONFLICT(content_hash) DO NOTHING',
                 (artifact_id, parsed.manifest['expected_sha256'], parsed.raw, Jsonb(parsed.manifest)))
    conn.execute('INSERT INTO mf_nse.import_attempts(operation_id,work_key,snapshot_id,state,report) VALUES(%s,%s,%s,%s,%s)',
                 (operation_id, work, snapshot_id, state, Jsonb(report)))
    conn.execute('''INSERT INTO mf_nse.snapshots(snapshot_id,artifact_id,operation_id,source_date,schema_version,
        semantic_hash,mapping_input_hash,crosswalk,manifest,source_rows,exposed_rows,withheld_rows)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', (snapshot_id, artifact_id, operation_id,
            source_date, parsed.manifest['schema'], parsed.semantic_hash, mapping_hash, Jsonb(walk),
            Jsonb(parsed.manifest), len(resolved), len(current), sum(withheld.values())))
    with conn.cursor() as cursor:
        cursor.executemany('INSERT INTO mf_nse.sch_rows VALUES(%s,%s,%s,%s,%s)',
                           [(snapshot_id, r['ordinal'], Jsonb(r['cells']), Jsonb(r['normalized']), Jsonb(r['field_holds'])) for r in resolved])
        cursor.executemany('INSERT INTO mf_nse.variant_mappings VALUES(%s,%s,%s,%s,%s)',
                           [(snapshot_id, r['ordinal'], r['status'], r['row_reason'], Jsonb(r['candidates'])) for r in resolved])
    if not historical:
        conn.execute('DELETE FROM mf_nse.current_sch')
        with conn.cursor() as cursor:
            cursor.executemany('INSERT INTO mf_nse.current_sch VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                [(uuid5(snapshot_id, key), snapshot_id, source_date, candidate['amfi_scheme_code'], row['cells'][1],
                  row['cells'][4], candidate['isin_slot'], row['plan'], candidate['option'],
                  Jsonb(row['normalized']), Jsonb(row['field_holds']), 'UNKNOWN')
                 for key, (row, candidate) in current.items()])
        changed = conn.execute("UPDATE mf_nse.family_heads SET snapshot_id=%s,generation=generation+1 WHERE family='SCH' AND generation=%s",
                               (snapshot_id, head['generation']))
        c.require(changed.rowcount == 1, 'HEAD_CHANGED')
    conn.execute('INSERT INTO mf_nse.observations(operation_id,snapshot_id,manifest,disposition) VALUES(%s,%s,%s,%s)',
                 (operation_id, snapshot_id, Jsonb(parsed.manifest), state))
    c.require(_fingerprint(conn) == before, 'CANONICAL_PRESERVATION_FAILED')
    return report


def publish(conn, parsed, walk, operation_id, *, expected_head):
    """Caller durably reserves operation_id BEFORE entering this boundary.

    No commit retry. A connection that has any existing transaction is refused.
    The original connection is rolled back only for known pre-COMMIT failure.
    """
    c.operation(operation_id)
    parsed = parser.parse_full(parsed.raw, parsed.manifest)
    walk = mapping.crosswalk(walk)
    c.require(conn.info.transaction_status == TransactionStatus.IDLE, 'IMPORT_IDLE_CONNECTION_REQUIRED')
    report = {'format': 'nse01-import-result-v1', 'operation_id': operation_id, 'stage': 'transaction',
              'effect': 'NO_PUBLICATION', 'state': 'VALIDATED_NOT_PUBLISHED', 'disposition': 'STOP',
              'consumer_acceptance': 'NOT_OBSERVED'}
    committing = False
    try:
        conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ')
        conn.execute(LIMITS)
        staged = _stage(conn, parsed, walk, operation_id, expected_head)
        report.update(staged, stage='commit', effect='UNKNOWN', state='COMMIT_UNKNOWN', disposition='INSPECT_ONLY')
        committing = True
        conn.commit()
        report.update(effect='COMMITTED_UNVERIFIED', state='COMMITTED_UNVERIFIED', stage='readback')
        # No reconnect. Read original durable result without making a second
        # write/COMMIT. Failure here must retain known commit, not "no import".
        conn.execute('BEGIN READ ONLY')
        conn.execute(LIMITS)
        observed = conn.execute('SELECT snapshot_id,disposition FROM mf_nse.observations WHERE operation_id=%s', (operation_id,)).fetchone()
        c.require(observed and str(observed['snapshot_id']) == staged['snapshot_id']
                  and observed['disposition'] == staged['state'], 'READBACK_DIFFERS')
        conn.rollback()
        report.update(state=staged['state'], effect='COMMITTED_VERIFIED', stage='complete', disposition='STOP')
        return report
    except BaseException as error:
        reason = str(error) if isinstance(error, c.ImportError) else 'DATABASE_OPERATION_FAILED'
        if not committing:
            try:
                conn.rollback()
                report.update(state='PUBLICATION_ROLLED_BACK', effect='ROLLBACK_CONFIRMED')
            except BaseException:
                report.update(state='ROLLBACK_UNCONFIRMED', effect='UNKNOWN', disposition='INSPECT_ONLY')
        report['reason'] = reason
        raise OutcomeError(report) from None


def inspect(conn, operation_id):
    """Original identity only; read-only, never resume, mutate or declare absence safe."""
    c.operation(operation_id)
    c.require(conn.info.transaction_status == TransactionStatus.IDLE, 'IMPORT_IDLE_CONNECTION_REQUIRED')
    conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
    conn.execute(LIMITS)
    try:
        _identity(conn)
        row = conn.execute('SELECT snapshot_id,disposition FROM mf_nse.observations WHERE operation_id=%s', (operation_id,)).fetchone()
        return {'operation_id': operation_id, 'state': row['disposition'] if row else 'UNKNOWN',
                'snapshot_id': str(row['snapshot_id']) if row else None,
                'effect': 'COMMITTED_VERIFIED' if row else 'UNKNOWN', 'disposition': 'INSPECT_ONLY',
                'consumer_acceptance': 'NOT_OBSERVED'}
    finally:
        conn.rollback()
