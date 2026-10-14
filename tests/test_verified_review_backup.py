"""Real PG18 dump/restore rehearsal of synthetic data, never live GCP proof."""
from datetime import date, timedelta
import socket
import subprocess
import time
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from conftest import certify_comparison_fixture
from mfdataindia.review.rollout_database import PgTools, fresh_backup, rehearse
from mfdataindia.store.postgres import PostgresStore
from test_verified_review_rollout import manifest, ROOT


@pytest.mark.postgres
def test_real_pg18_backup_restores_and_rehearses_all_routes(pg_dsn, tmp_path):
    if not pg_dsn:
        pytest.skip('MF_TEST_DSN not set')
    roles = ('mfdata_admin', 'mfdata_ingest')
    created = []
    container = 'mf-review-backup-clone-' + uuid4().hex[:12]
    started = False
    with psycopg.connect(pg_dsn, autocommit=True, row_factory=dict_row) as admin:
        if admin.execute('SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)', (list(roles),)).fetchone() or admin.execute("SELECT 1 FROM pg_database WHERE datname='mfdataindia'").fetchone():
            pytest.skip('refusing existing shared source database/principals')
        for role in roles:
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN CREATEROLE PASSWORD 'backup-source-fixture-only'").format(sql.Identifier(role)))
            created.append(role)
        admin.execute('CREATE DATABASE mfdataindia OWNER mfdata_admin')
        source = make_conninfo(pg_dsn, dbname='mfdataindia', user='mfdata_admin', password='backup-source-fixture-only')
        try:
            with PostgresStore(source, use_copy=False) as base:
                base.apply_migrations(ROOT / 'sql')
            with psycopg.connect(source, row_factory=dict_row) as conn:
                conn.execute("INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES('Backup fixture','BACKUP FIXTURE')")
                for code, option in ((1, 'GROWTH'), (2, 'IDCW')):
                    conn.execute("INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type) "
                                 "VALUES(%s,1,%s,%s,'OPEN_ENDED','Equity','REGULAR',%s)", (code, f'Fund {code}', f'FUND {code}', option))
                    conn.execute("INSERT INTO mf.fund_variants(amfi_scheme_code,group_key,base_scheme_name) VALUES(%s,%s,%s)", (code, str(code), f'Fund {code}'))
                    day = date(2024, 1, 1)
                    for i in range(270):
                        while day.weekday() >= 5:
                            day += timedelta(days=1)
                        conn.execute('INSERT INTO mf.nav_history(amfi_scheme_code,nav_date,nav) VALUES(%s,%s,%s)', (code, day, '100' if i % 2 == 0 else '110'))
                        day += timedelta(days=1)
                certify_comparison_fixture(conn)
                # Match the broad production future-table defaults BEFORE the
                # optional schema is created under the real migration owner.
                conn.execute('ALTER DEFAULT PRIVILEGES GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO mfdata_ingest')
            tools = PgTools()
            backup = fresh_backup(source, tmp_path, tools)
            assert backup['backup_bytes'] > 0 and backup['baseline']['tables']['nav_history']['rows'] == 540
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            data = tmp_path / 'clone-data'
            data.mkdir()
            subprocess.run(['docker', 'run', '-d', '--name', container, '--label', 'purpose=mfdata-synthetic-backup-test',
                            '-e', 'POSTGRES_DB=mfdata_review_clone_control', '-e', 'POSTGRES_PASSWORD=clone-fixture-only',
                            '-p', f'127.0.0.1:{port}:5432', '-v', str(data) + ':/var/lib/postgresql', 'postgres:18.6-alpine'], check=True, capture_output=True)
            started = True
            clone = make_conninfo(host='127.0.0.1', port=port, user='postgres', password='clone-fixture-only', dbname='mfdata_review_clone_control')
            for attempt in range(30):
                try:
                    with psycopg.connect(clone, connect_timeout=1):
                        break
                except psycopg.OperationalError:
                    if attempt == 29:
                        raise
                    time.sleep(0.2)
            proof = rehearse(source, clone, backup, tmp_path, manifest(), ROOT, tools)
            assert proof['restored_backup_sha256'] == backup['backup_sha256']
            assert proof['http_endpoints'] == 6 and proof['http_evidence_level'] == 'RESTORED_CLONE_HTTP'
            assert proof['preservation']['source_values_preserved'] and proof['replay_verified']
        finally:
            if started:
                subprocess.run(['docker', 'rm', '-f', container], check=True, capture_output=True)
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='mfdataindia'")
            admin.execute('DROP DATABASE mfdataindia')
            for role in reversed(created):
                admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
