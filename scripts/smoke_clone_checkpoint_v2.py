#!/usr/bin/env python3
"""Disposable native PG18 restore regression; no source, cloud or live DSN.

Run only inside the already-built review-tools CI image as its non-root user.
Three local clusters and two consistent snapshot handoffs, not live acceptance.
"""

import os
from pathlib import Path
import subprocess
import tempfile

import psycopg
from psycopg.rows import dict_row

from mfdataindia.review import clone_checkpoint_v2 as cp


DATABASE = "mfdata_fs13_final_image_261020g"


def main():
    if os.geteuid() == 0 or "MFDATAINDIA_DSN" in os.environ:
        raise ValueError("non-root secret-free disposable CI image required")
    with tempfile.TemporaryDirectory(prefix="mf-checkpoint-native-") as temporary:
        root = Path(temporary)
        password = "checkpoint-native-ci-fixture-only"
        password_file = root / "fixture-password"
        password_file.write_text(password)
        password_file.chmod(0o600)
        pg_env = {
            **os.environ,
            "PGHOST": "127.0.0.1",
            "PGPORT": "55540",
            "PGDATABASE": DATABASE,
            "PGUSER": "postgres",
            "PGPASSWORD": password,
            "PGCONNECT_TIMEOUT": "5",
        }

        def native(argv):
            result = subprocess.run(argv, env=pg_env, capture_output=True, timeout=240)
            if result.returncode:
                raise RuntimeError("synthetic native checkpoint command failed: " + argv[0])
            return result.stdout.decode()

        if "PostgreSQL) 18." not in native(["pg_dump", "--version"]):
            raise ValueError("native PG18 image required")

        def connect(database=DATABASE, **kwargs):
            return psycopg.connect(
                host="127.0.0.1",
                port=55540,
                dbname=database,
                user="postgres",
                password=password,
                row_factory=dict_row,
                **kwargs,
            )

        def init(name):
            data = root / name
            native(
                [
                    "initdb",
                    "--pgdata=" + str(data),
                    "--username=postgres",
                    "--auth-local=trust",
                    "--auth-host=scram-sha-256",
                    "--pwfile=" + str(password_file),
                    "--encoding=UTF8",
                    "--no-locale",
                ]
            )
            native(
                [
                    "pg_ctl",
                    "--pgdata=" + str(data),
                    "--log=" + str(root / (name + ".log")),
                    "--options=-h 127.0.0.1 -p 55540 -k " + str(root),
                    "--wait",
                    "start",
                ]
            )
            with connect("postgres", autocommit=True) as conn:
                conn.execute("CREATE ROLE checkpoint_reader NOLOGIN NOINHERIT")
                conn.execute("CREATE DATABASE " + DATABASE)
            return data

        def snapshot_seals(conn):
            metadata = cp.logical_metadata(conn)
            values = conn.execute(
                "SELECT id,encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS sha256 "
                "FROM checkpoint_fixture.values t ORDER BY id"
            ).fetchall()
            return {"metadata": metadata, "all_original_values": values}

        def seals():
            with connect() as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
                conn.execute("SET LOCAL TimeZone='UTC'")
                conn.execute("SET LOCAL statement_timeout='5s'")
                return snapshot_seals(conn)

        data = init("phase1")
        active = data
        try:
            with connect() as conn:
                conn.execute("CREATE SCHEMA checkpoint_fixture")
                conn.execute("CREATE SEQUENCE checkpoint_fixture.review_events START WITH 7")
                conn.execute("SELECT nextval('checkpoint_fixture.review_events')")
                conn.execute(
                    "CREATE TABLE checkpoint_fixture.values(id integer PRIMARY KEY,"
                    "amount numeric(50,28) NOT NULL,raw bytea,note text,days date[],j jsonb,"
                    "CONSTRAINT sane CHECK(id>0 AND amount>=0))"
                )
                conn.execute(
                    "INSERT INTO checkpoint_fixture.values VALUES"
                    "(1,12345678901234567890.1234567890123456789012345678,"
                    "decode('00ff007f','hex'),'₹',ARRAY['2026-10-19'::date,NULL],"
                    '\'{"null":null,"integer":9223372036854775808}\'),'
                    "(2,0,NULL,NULL,ARRAY[]::date[],'[]')"
                )
                conn.execute("GRANT USAGE ON SCHEMA checkpoint_fixture TO checkpoint_reader")
                conn.execute("GRANT SELECT ON checkpoint_fixture.values TO checkpoint_reader")
                conn.execute(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA checkpoint_fixture "
                    "GRANT SELECT ON TABLES TO checkpoint_reader"
                )
                conn.execute("ALTER TABLE checkpoint_fixture.values ENABLE ROW LEVEL SECURITY")
                conn.execute(
                    "CREATE POLICY reading ON checkpoint_fixture.values FOR SELECT "
                    "TO checkpoint_reader USING(id>0)"
                )
                conn.execute(
                    "CREATE VIEW checkpoint_fixture.derived AS "
                    "SELECT id,amount,'not-a-source-number'::text AS note "
                    "FROM checkpoint_fixture.values"
                )
            original = seals()
            dump = root / "snapshot.dump"
            native(["pg_dump", "--format=custom", "--file=" + str(dump)])
            sha = hashlib_file(dump)
            native(["pg_ctl", "--pgdata=" + str(data), "--mode=fast", "--wait", "stop"])
            active = None
            data = init("phase2")
            active = data
            if hashlib_file(dump) != sha:
                raise ValueError("synthetic checkpoint bytes changed")
            native(["pg_restore", "--exit-on-error", "--dbname=" + DATABASE, str(dump)])
            cp.require_restored_seals(original, seals())
            # A second independent snapshot/dump/cluster, not reused physical IDs.
            with connect() as conn:
                conn.execute(
                    "INSERT INTO checkpoint_fixture.values VALUES(3,1,NULL,'phase2',ARRAY[]::date[],'{}')"
                )
            second_dump = root / "second-snapshot.dump"
            with connect() as proof:
                proof.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
                proof.execute("SET LOCAL TimeZone='UTC'")
                second_seals = snapshot_seals(proof)
                snapshot = proof.execute("SELECT pg_export_snapshot() AS snapshot").fetchone()[
                    "snapshot"
                ]
                child = subprocess.Popen(
                    [
                        "pg_dump",
                        "--format=custom",
                        "--snapshot=" + snapshot,
                        "--file=" + str(second_dump),
                    ],
                    env=pg_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    # Synthetic concurrent write must NOT enter the checkpoint.
                    with connect() as writer:
                        writer.execute(
                            "INSERT INTO checkpoint_fixture.values VALUES(4,2,NULL,'after-export',ARRAY[]::date[],'{}')"
                        )
                    cp.require_restored_seals(second_seals, snapshot_seals(proof))
                    child.communicate(timeout=240)
                    if child.returncode:
                        raise RuntimeError("native same-snapshot export failed")
                finally:
                    if child.poll() is None:
                        child.terminate()
                        child.communicate(timeout=15)
            second_sha = hashlib_file(second_dump)
            native(["pg_ctl", "--pgdata=" + str(data), "--mode=fast", "--wait", "stop"])
            active = None
            data = init("phase3")
            active = data
            if hashlib_file(second_dump) != second_sha:
                raise ValueError("second checkpoint immutable bytes changed")
            native(["pg_restore", "--exit-on-error", "--dbname=" + DATABASE, str(second_dump)])
            cp.require_restored_seals(second_seals, seals())
            if seals()["all_original_values"][:2] != original["all_original_values"]:
                raise ValueError("original rows changed during second handoff")
            original = second_seals

            def must_reject():
                try:
                    cp.require_restored_seals(original, seals())
                except ValueError:
                    return
                raise ValueError("native checkpoint authority/value drift was admitted")

            with connect() as conn:
                conn.execute("GRANT UPDATE ON checkpoint_fixture.values TO checkpoint_reader")
            must_reject()
            with connect() as conn:
                conn.execute("REVOKE UPDATE ON checkpoint_fixture.values FROM checkpoint_reader")
                conn.execute("ALTER ROLE checkpoint_reader INHERIT")
            must_reject()
            with connect() as conn:
                conn.execute("ALTER ROLE checkpoint_reader NOINHERIT")
            cp.require_restored_seals(original, seals())
            with connect() as conn:
                conn.execute("UPDATE checkpoint_fixture.values SET amount=amount+1 WHERE id=1")
            must_reject()
        finally:
            if active is not None:
                native(["pg_ctl", "--pgdata=" + str(active), "--mode=fast", "--wait", "stop"])
    print(
        "native PG18 THREE-cluster checkpoint restore and tamper rejection passed; synthetic only",
        flush=True,
    )


def hashlib_file(path):
    import hashlib

    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            sha.update(block)
    return sha.hexdigest()


if __name__ == "__main__":
    main()
