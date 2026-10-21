#!/usr/bin/env python3
"""Disposable native PG18 restore regression; no source, cloud or live DSN.

Run only inside the already-built review-tools CI image as its non-root user.
Three local clusters and two consistent snapshot handoffs, not live acceptance.
"""

import base64
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import time
from urllib.parse import unquote

import psycopg
from psycopg.rows import dict_row

from mfdataindia.review import clone_checkpoint_v2 as cp
from mfdataindia.review.clone_final_snapshot import DUMP_START, DUMP_EXPORT


DATABASE = "mfdata_fs13_final_image_261021k"


class FixtureResponse:
    """Synthetic transport only; no network client or external calls."""
    status_code = 200

    def __init__(self, raw=b"", document=None):
        self.raw, self.document = raw, document

    def json(self):
        return self.document

    def iter_content(self, size):
        for offset in range(0, len(self.raw), size):
            yield self.raw[offset:offset + size]

    def close(self):
        pass


class FixtureTransport:
    """Exercise real cp export/import code, preserving all generation/MD5 checks."""
    def __init__(self):
        self.objects = {}
        self.posts = 0
        self.gets = 0

    def post(self, url, **kwargs):
        assert url == "https://storage.googleapis.com/upload/storage/v1/b/" + cp.BUCKET + "/o"
        assert kwargs["timeout"] == 180 and kwargs["allow_redirects"] is False
        assert kwargs["params"]["ifGenerationMatch"] == "0"
        name = kwargs["params"]["name"]
        if name in self.objects:
            raise ValueError("synthetic duplicate create-only upload")
        raw = kwargs["data"].read()
        md5 = base64.b64encode(hashlib.md5(raw, usedforsecurity=False).digest()).decode()
        assert kwargs["headers"]["Content-MD5"] == md5
        self.posts += 1
        generation = str(self.posts)
        self.objects[name] = (raw, generation)
        return FixtureResponse(document={"bucket": cp.BUCKET, "name": name,
            "generation": generation, "size": str(len(raw)), "md5Hash": md5})

    def get(self, url, **kwargs):
        assert url.startswith("https://storage.googleapis.com/storage/v1/b/" + cp.BUCKET + "/o/")
        assert kwargs["timeout"] == 180 and kwargs["allow_redirects"] is False
        raw, generation = self.objects[unquote(url.split("/o/", 1)[1])]
        assert kwargs["params"] == {"alt": "media", "generation": generation}
        self.gets += 1
        return FixtureResponse(raw=raw)


def fixture_context():
    return {"format": "mfdata-disposable-clone-checkpoint-v2", "manifest_sha256": "a" * 64,
        "backup": {"uri": "gs://" + cp.BUCKET + "/verified-review-rollouts/dev-uti-gap-pre-261021k/snapshot.dump",
                   "generation": "1", "size": 1, "sha256": "b" * 64, "md5": "AAAAAAAAAAAAAAAAAAAAAA=="},
        "app_image": "asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia@sha256:" + "c" * 64,
        "tools_image": "asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia-review-tools@sha256:" + "d" * 64,
        "git_sha": "e" * 40, "helper_hashes": {"checkpoint_worker.py": "f" * 64},
        "reference_sha256": "1" * 64,
        "checkpoint_prefix": "verified-review-rollouts/dev-uti-gap-261021k-acquire-checkpoint/",
        "phase": "acquire", "previous_checkpoint_sha256": None, "previous_execution_sha256": None}


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
            first_output = root / "first-checkpoint"
            first_output.mkdir()
            dump = first_output / "checkpoint.dump"
            with connect() as first_proof:
                first_proof.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
                first_proof.execute("SET LOCAL TimeZone='UTC'")
                original = snapshot_seals(first_proof)
                first_snapshot = first_proof.execute("SELECT pg_export_snapshot() AS snapshot").fetchone()["snapshot"]
                native(["pg_dump", "--format=custom", "--snapshot=" + first_snapshot, "--file=" + str(dump)])
            sha = hashlib_file(dump)
            transport = FixtureTransport()
            first_context = fixture_context()
            state = {"original_values_and_metadata": original, "fixture_only": True}
            first_certificate = cp.export_checkpoint(transport, "synthetic-only", first_output,
                                                     first_context, state, original)
            native(["pg_ctl", "--pgdata=" + str(data), "--mode=fast", "--wait", "stop"])
            active = None
            data = init("phase2")
            active = data
            incoming = root / "phase2-input"
            incoming.mkdir()
            imported = cp.import_checkpoint(transport, "synthetic-only", incoming,
                                             first_context, first_certificate)
            if imported["state"] != state or imported["logical_seals"] != original:
                raise ValueError("first complete transport state changed")
            input_hashes = {name: hashlib_file(incoming / name)
                            for name in ("checkpoint.dump", "checkpoint-state.json")}
            if input_hashes["checkpoint.dump"] != sha:
                raise ValueError("synthetic checkpoint bytes changed")
            native(["pg_restore", "--exit-on-error", "--dbname=" + DATABASE, str(incoming / "checkpoint.dump")])
            cp.require_restored_seals(original, seals())
            # A second independent snapshot/dump/cluster, not reused physical IDs.
            with connect() as conn:
                conn.execute(
                    "INSERT INTO checkpoint_fixture.values VALUES(3,1,NULL,'phase2',ARRAY[]::date[],'{}')"
                )
            # H imported artifacts REALLY exist in this input workspace. The
            # exact shared setup must choose a different output for both files.
            second_root = incoming
            # Reproduce actual G: frozen source snapshot=True established RR
            # but deliberately allowed local install probes. The real metadata
            # guard MUST reject that mode, not silently seal a writable proof.
            with connect() as formerly_writable:
                formerly_writable.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                try:
                    cp.logical_metadata(formerly_writable)
                except ValueError as error:
                    if str(error) != "checkpoint metadata requires RR READONLY":
                        raise
                else:
                    raise ValueError("read-write repeatable-read checkpoint was admitted")
            with connect() as proof:
                proof.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                proof.execute("SET LOCAL TimeZone='UTC'")
                # Execute the EXACT prefix used by the generated H operator,
                # not an independently coded setup that could mask drift.
                owned = {"conn": proof, "time": time, "subprocess": subprocess,
                         "CLONE_ROOT": second_root, "pg_env": pg_env}
                exec(compile(textwrap.dedent(DUMP_START), "<owned-checkpoint-prefix>", "exec"), owned)
                child = owned["dump_child"]
                second_dump = owned["checkpoint_output"] / "checkpoint.dump"
                try:
                    second_seals = snapshot_seals(proof)
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
            second_context = {**first_context, "phase": "rehearsal",
                "checkpoint_prefix": first_context["checkpoint_prefix"].replace("-acquire-", "-rehearsal-"),
                "previous_checkpoint_sha256": cp.digest(first_certificate), "previous_execution_sha256": "2" * 64}
            # Same export bytes as the generated operator, not a separate
            # smoke-only implementation that can mask its destination drift.
            owned.update({"cp": cp, "session": transport,
                          "identity": FixtureResponse(document={"access_token": "synthetic-only"}),
                          "NEXT_CHECKPOINT_CONTEXT": second_context, "checkpoint_state": state,
                          "checkpoint_logical": second_seals})
            exec(compile(textwrap.dedent(DUMP_EXPORT), "<owned-checkpoint-export>", "exec"), owned)
            second_certificate = owned["checkpoint"]
            if input_hashes != {name: hashlib_file(incoming / name) for name in input_hashes}:
                raise ValueError("incoming checkpoint bytes were overwritten")
            native(["pg_ctl", "--pgdata=" + str(data), "--mode=fast", "--wait", "stop"])
            active = None
            data = init("phase3")
            active = data
            third_input = root / "phase3-input"
            third_input.mkdir()
            final = cp.import_checkpoint(transport, "synthetic-only", third_input,
                                         second_context, second_certificate)
            if final["state"] != state or final["logical_seals"] != second_seals:
                raise ValueError("second complete transport state changed")
            if hashlib_file(third_input / "checkpoint.dump") != second_sha:
                raise ValueError("second checkpoint immutable bytes changed")
            if transport.posts != 4 or transport.gets != 4:
                raise ValueError("two complete create-only transport handoffs required")
            native(["pg_restore", "--exit-on-error", "--dbname=" + DATABASE, str(third_input / "checkpoint.dump")])
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
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            sha.update(block)
    return sha.hexdigest()


if __name__ == "__main__":
    main()
