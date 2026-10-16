"""Fresh snapshot backup/restore and bounded additive database operations."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from mfdataindia.review import store
from mfdataindia.review.policy import policy
from mfdataindia.review.preservation import MIGRATION, capture_preservation, compare_preservation
from mfdataindia.review.privileges import verify_principal, verify_runtime
from mfdataindia.review.rollout import CONNECTION, ROLES, Manifest
from mfdataindia.store.locks import acquire_nav_data_operation_lock


def readonly(dsn):
    conn = psycopg.connect(dsn, row_factory=dict_row)
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    conn.execute("SET LOCAL statement_timeout='120s'")
    return conn


def proxy_dsn(secret: str):
    parsed = conninfo_to_dict(secret)
    if (parsed.get('host') != '/cloudsql/' + CONNECTION or parsed.get('dbname') != 'mfdataindia'
            or parsed.get('user') != 'mfdata_admin' or not parsed.get('password') or parsed.get('hostaddr')):
        raise ValueError('source credential does not identify the approved database/instance/admin')
    return make_conninfo(secret, host='127.0.0.1', port='5434', connect_timeout='10')


class PgTools:
    """PG18 tools on the runner; password is environment-only, never argv."""
    def run(self, tool, dsn, directory, arguments):
        parameters = conninfo_to_dict(dsn)
        environment = {**os.environ, 'PGHOST': parameters['host'], 'PGPORT': parameters.get('port', '5432'),
                       'PGDATABASE': parameters['dbname'], 'PGUSER': parameters['user'],
                       'PGPASSWORD': parameters['password'], 'PGCONNECT_TIMEOUT': '10'}
        argv = ['docker', 'run', '--rm', '--network=host', '-v', str(directory.resolve()) + ':/backup']
        for name in ('PGHOST', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD', 'PGCONNECT_TIMEOUT'):
            argv += ['--env', name]
        argv += ['postgres:18.6-alpine', tool, *arguments]
        result = subprocess.run(argv, env=environment, text=True, capture_output=True, timeout=1800)
        if result.returncode:
            raise RuntimeError('PostgreSQL tool failed: ' + tool)
        return result.stdout


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def fresh_backup(source_dsn, directory: Path, tools: PgTools):
    dump = directory / 'snapshot.dump'
    if dump.exists():
        raise ValueError('backup output already exists; refusing replacement')
    with readonly(source_dsn) as conn:
        identity = conn.execute('SELECT current_database() AS database,current_user AS principal,current_setting(\'server_version_num\')::int AS version').fetchone()
        if identity['database'] != 'mfdataindia' or identity['principal'] != 'mfdata_admin' or identity['version'] // 10000 != 18:
            raise ValueError('source database identity/version mismatch')
        snapshot = conn.execute('SELECT pg_export_snapshot() AS snapshot').fetchone()['snapshot']
        baseline = capture_preservation(conn)
        from mfdataindia.review.publication_operations import capture_review
        review_baseline = capture_review(conn)
        from mfdataindia.analytics.operations import capture_calendar
        analytics_baseline = capture_calendar(conn)
        from mfdataindia.analytics.rolling_operations import capture_rolling
        rolling_baseline = capture_rolling(conn)
        from mfdataindia.enrichment.operations import capture_enrichment
        enrichment_inventory = capture_enrichment(conn, include_rows=True)
        enrichment_baseline = None if enrichment_inventory is None else {
            table: {key: value for key, value in record.items() if key != 'row_hashes'}
            for table, record in enrichment_inventory.items()}
        enrichment_row_baseline = None if enrichment_inventory is None else {
            table: record['row_hashes'] for table, record in enrichment_inventory.items()}
        from mfdataindia.enrichment.facts_store import capture as capture_official_facts
        official_facts_baseline = capture_official_facts(conn)
        from mfdataindia.enrichment.portfolio_store import capture as capture_portfolios
        portfolio_baseline = capture_portfolios(conn)
        from mfdataindia.analytics.overlap_store import capture as capture_overlap
        overlap_baseline = capture_overlap(conn)
        from mfdataindia.enrichment.distribution_store import capture as capture_distributions
        distribution_baseline = capture_distributions(conn)
        from mfdataindia.enrichment.idcw_store import capture as capture_idcw
        idcw_baseline = capture_idcw(conn)
        from mfdataindia.enrichment.monthly_store import capture as capture_monthly
        monthly_baseline = capture_monthly(conn)
        if not baseline['tables']['funds']['rows'] or not baseline['tables']['nav_history']['rows']:
            raise ValueError('operational backup must contain actual fund/NAV records')
        # Role names only. Password hashes and global role attributes are never
        # exported; the scratch server creates nonprivileged placeholders.
        roles = [r['rolname'] for r in conn.execute("SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY rolname").fetchall()]
        tools.run('pg_dump', source_dsn, directory, ['--format=custom', '--snapshot=' + snapshot, '--file=/backup/snapshot.dump'])
    if dump.stat().st_size == 0:
        raise ValueError('empty backup')
    evidence = {'backup_sha256': file_sha256(dump), 'backup_bytes': dump.stat().st_size,
                'source': identity, 'baseline': baseline, 'roles': roles}
    if review_baseline is not None:
        evidence['review_baseline'] = review_baseline
    if analytics_baseline is not None:
        evidence['analytics_baseline'] = analytics_baseline
    if rolling_baseline is not None:
        evidence['rolling_baseline'] = rolling_baseline
    if enrichment_baseline is not None:
        evidence['enrichment_baseline'] = enrichment_baseline
        evidence['enrichment_row_baseline'] = enrichment_row_baseline
    if official_facts_baseline is not None:
        evidence['official_facts_baseline'] = official_facts_baseline
    if portfolio_baseline is not None:
        evidence['portfolio_baseline'] = portfolio_baseline
    if overlap_baseline is not None:
        evidence['overlap_baseline'] = overlap_baseline
    if distribution_baseline is not None:
        evidence['distribution_baseline'] = distribution_baseline
    if idcw_baseline is not None:
        evidence['idcw_baseline'] = idcw_baseline
    if monthly_baseline is not None:
        evidence['monthly_baseline'] = monthly_baseline
    return evidence


def install_atomic(conn, root: Path, manifest: Manifest):
    source = (root / 'sql/optional' / MIGRATION).read_text()
    if hashlib.sha256(source.encode()).hexdigest() != manifest.migration_hash:
        raise ValueError('migration checksum drift')
    previous = conn.execute('SELECT content_sha256 FROM mf.schema_migrations WHERE migration_name=%s', (MIGRATION,)).fetchone()
    if previous:
        if previous['content_sha256'] != manifest.migration_hash:
            raise ValueError('installed migration checksum differs')
        return
    # This one pinned optional file has an outer BEGIN/COMMIT. Remove only those
    # boundaries so the caller's source locks and preservation checks survive.
    if len(re.findall(r'^BEGIN;$', source, re.M)) != 1 or not source.rstrip().endswith('COMMIT;'):
        raise ValueError('optional migration transaction shape differs')
    body = re.sub(r'^BEGIN;\n', '', source, count=1, flags=re.M).rstrip()[:-len('COMMIT;')]
    conn.execute(body)
    conn.execute('INSERT INTO mf.schema_migrations(migration_name,content_sha256) VALUES(%s,%s)', (MIGRATION, manifest.migration_hash))


def create_principal(conn, name, password):
    if name not in ROLES:
        raise ValueError('unexpected optional principal')
    if conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (name,)).fetchone():
        raise ValueError('principal already exists; refusing credential rotation')
    encrypted = conn.pgconn.encrypt_password(password.encode(), name.encode(), b'scram-sha-256').decode()
    conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Identifier(name), sql.Literal(encrypted)))


def install_and_reconcile(dsn, root, manifest, passwords):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='120s'")
        conn.execute("SET LOCAL transaction_timeout='180s'")
        acquire_nav_data_operation_lock(conn)
        relations = conn.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf' AND c.relkind IN('r','p') AND NOT c.relispartition ORDER BY c.relname").fetchall()
        for row in relations:
            conn.execute(sql.SQL('LOCK TABLE mf.{} IN SHARE MODE').format(sql.Identifier(row['relname'])))
        before = capture_preservation(conn)
        for role, password in passwords.items():
            create_principal(conn, role, password)
        install_atomic(conn, root, manifest)
        # Revoke inherited defaults on NEW objects only, including unfamiliar
        # source-role names. Never change the source owner's global defaults.
        inherited = conn.execute("""
            WITH a AS (
              SELECT c.relacl AS acl FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_review'
              UNION ALL SELECT p.proacl FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mf_review'
              UNION ALL SELECT nspacl FROM pg_namespace WHERE nspname='mf_review'
            ) SELECT DISTINCT r.rolname FROM a CROSS JOIN LATERAL aclexplode(acl) x JOIN pg_roles r ON r.oid=x.grantee
              WHERE r.rolname<>current_user AND r.rolname<>ALL(%s)
        """, (list(ROLES),)).fetchall()
        for row in inherited:
            for kind in ('TABLES', 'SEQUENCES', 'FUNCTIONS'):
                conn.execute(sql.SQL('REVOKE ALL ON ALL {} IN SCHEMA mf_review FROM {}').format(sql.SQL(kind), sql.Identifier(row['rolname'])))
            conn.execute(sql.SQL('REVOKE ALL ON SCHEMA mf_review FROM {}').format(sql.Identifier(row['rolname'])))
        conn.execute((root / 'scripts/sql/reconcile_verified_review_privileges.sql').read_text())
        for role in ROLES:
            verify_principal(conn, 'generator' if role == ROLES[0] else 'api', role)
        after = capture_preservation(conn)
        compare_preservation(before, after, manifest.migration_hash)
    return {'before': before, 'after': after, 'source_values_preserved': True, 'bounded_lock_seconds': 180}


def rehearse(source_dsn, clone_admin_dsn, backup, directory, manifest, root, tools):
    if file_sha256(directory / 'snapshot.dump') != backup['backup_sha256']:
        raise ValueError('backup bytes changed before restore')
    parameters = conninfo_to_dict(clone_admin_dsn)
    name = 'mfdata_review_rehearsal_' + manifest.rollout_id.replace('-', '_')
    if parameters.get('host') != '127.0.0.1' or parameters.get('dbname') != 'mfdata_review_clone_control':
        raise ValueError('scratch restore requires the explicitly isolated runner server')
    with psycopg.connect(clone_admin_dsn, autocommit=True, row_factory=dict_row) as admin:
        databases = {r['datname'] for r in admin.execute('SELECT datname FROM pg_database').fetchall()}
        roles = {r['rolname'] for r in admin.execute("SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_'").fetchall()}
        if databases != {'postgres', 'template0', 'template1', 'mfdata_review_clone_control'} or roles != {'postgres'}:
            raise ValueError('scratch server is not empty and isolated; refusing shared-server changes')
        if admin.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone():
            raise ValueError('scratch database exists; never overwrite')
        for role in backup['roles']:
            if not admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        for role in ROLES:
            if admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                raise ValueError('scratch principal names already exist; use a fresh server')
    clone_dsn = make_conninfo(clone_admin_dsn, dbname=name)
    tools.run('pg_restore', clone_dsn, directory, ['--dbname=' + name, '--exit-on-error', '--single-transaction', '/backup/snapshot.dump'])
    with readonly(clone_dsn) as conn:
        restored = capture_preservation(conn)
    compare_preservation(backup['baseline'], restored, manifest.migration_hash)
    # Preserve the production migration owner/default ACLs on the scratch server.
    owner_password = secrets.token_hex(32)
    with psycopg.connect(clone_dsn, row_factory=dict_row) as conn:
        conn.execute(sql.SQL('ALTER ROLE mfdata_admin LOGIN CREATEROLE PASSWORD {}').format(sql.Literal(owner_password)))
        conn.execute(sql.SQL('GRANT ALL ON DATABASE {} TO mfdata_admin').format(sql.Identifier(name)))
    owner_dsn = make_conninfo(clone_dsn, user='mfdata_admin', password=owner_password)
    passwords = {role: secrets.token_hex(32) for role in ROLES}
    preservation = install_and_reconcile(owner_dsn, root, manifest, passwords)
    dsns = {role: make_conninfo(clone_dsn, user=role, password=password) for role, password in passwords.items()}
    verify_logins(dsns)
    with readonly(dsns[ROLES[0]]) as conn:
        snapshots = [store.capture(conn, code) for code in sorted(manifest.cohort)]
    with psycopg.connect(dsns[ROLES[0]], row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        results = store.publish(conn, snapshots, policy())
        if store.publish(conn, snapshots, policy()) != results:
            raise ValueError('restored-clone publication replay differs')
    # Test actual HTTP serialization/auth/replay against restored records, with
    # an ephemeral clone-only signer. This is NOT Cloud IAM or Insights RBAC proof.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi.testclient import TestClient
    from mfdataindia.review.app import create_review_app
    from mfdataindia.review.auth import AssertionVerifier
    from mfdataindia.review.certification import certify
    key = Ed25519PrivateKey.generate()
    trust = {'public_key': key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
             'issuer': 'restored-clone-rehearsal', 'audience': 'restored-clone-rehearsal', 'caller': 'clone-certifier',
             'allowed_scopes': ['review:read', 'review:admin']}
    clone_manifest = manifest.model_copy(update={'trust_hash': store.fingerprint(trust)})
    verifier = AssertionVerifier(**{**trust, 'allowed_scopes': frozenset(trust['allowed_scopes'])})
    class Transport:
        def __init__(self, client):
            self.client = client
        def request(self, method, url, **kwargs):
            kwargs.pop('allow_redirects')
            kwargs.pop('timeout')
            return self.client.request(method, url.removeprefix('https://clone.a.run.app'), **kwargs)
    with TestClient(create_review_app(dsns[ROLES[2]], verifier=verifier)) as client:
        certificate = certify(clone_manifest, base_url='https://clone.a.run.app', audience='https://clone.a.run.app',
                              reader_dsn=dsns[ROLES[1]], trust=trust, private_key=key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(), session=Transport(client),
                              identity_token='clone-only-no-cloud-identity', evidence_level='RESTORED_CLONE_HTTP')
    with readonly(clone_dsn) as conn:
        after = capture_preservation(conn)
    compare_preservation(restored, after, manifest.migration_hash)
    return {'restored_backup_sha256': backup['backup_sha256'], 'restored_database': name,
            'preservation': preservation, 'post_generation': after,
            'cohort_input_hashes': {str(s['code']): store.fingerprint(s) for s in snapshots},
            'replay_verified': True, 'http_endpoints': certificate['evidence']['endpoints'],
            'http_evidence_level': 'RESTORED_CLONE_HTTP', 'level': 'OPERATIONAL_RESTORED_CLONE'}


def verify_logins(dsns):
    for role, dsn in dsns.items():
        with readonly(dsn) as conn:
            verify_runtime(conn, 'generator' if role == ROLES[0] else 'api')
            if conn.execute('SELECT session_user AS role').fetchone()['role'] != role:
                raise ValueError('dedicated login differs')
