#!/usr/bin/env python3
"""Restore and baseline the one approved MFDataIndia DEV snapshot.

This entry point is intentionally narrow: all target and object coordinates are
immutable constants, the destination must be empty, and any failure is terminal.
The Cloud Run Job that invokes it is configured with maxRetries=0.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

EXPECTED_PROJECT = "trillionlane-dev"
EXPECTED_CONNECTION = "trillionlane-dev:asia-south1:tlws-mf-data-dev"
EXPECTED_DATABASE = "mfdataindia"
EXPECTED_BUCKET = "tlws_mf_data_source"
EXPECTED_OBJECT = "mfdataindia_full_20261008_045418_snapshot.dump"
EXPECTED_GENERATION = 1791431731173325
EXPECTED_SIZE = 47_801_979
EXPECTED_MD5 = "HYE6KQY48HZYqwtA8+w7Cw=="
EXPECTED_CRC32C = "GFcdHg=="

BASELINE_MIGRATIONS = (
    "000_schema_migrations.sql",
    "001_core_schema.sql",
    "002_nav_and_views.sql",
    "003_enrichment_recon.sql",
    "004_groww_enrichment.sql",
    "005_groww_deep_enrichment.sql",
    "006_groww_provenance.sql",
    "008_fund_data_status_v2.sql",
    "009_fund_risk_profile.sql",
    "010_computed_metrics.sql",
    "011_amc_factsheets.sql",
    "012_drop_aggregator_identity.sql",
    "013_purge_aggregator_references.sql",
)


def required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def postgres_env(dsn: str) -> dict[str, str]:
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("admin DSN must use postgres or postgresql")
    query = parse_qs(parsed.query)
    socket_hosts = query.get("host", [])
    if len(socket_hosts) != 1 or socket_hosts[0] != f"/cloudsql/{EXPECTED_CONNECTION}":
        raise RuntimeError("admin DSN does not target the approved Cloud SQL instance")
    database = parsed.path.removeprefix("/")
    if database != EXPECTED_DATABASE or not parsed.username or parsed.password is None:
        raise RuntimeError("admin DSN has an unexpected database or missing credentials")
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": socket_hosts[0],
            "PGDATABASE": database,
            "PGUSER": unquote(parsed.username),
            "PGPASSWORD": unquote(parsed.password),
            "PGCONNECT_TIMEOUT": "20",
        }
    )
    return env


def run(command: list[str], *, env: dict[str, str], input_text: str | None = None) -> str:
    completed = subprocess.run(
        command,
        env=env,
        input=input_text,
        text=True,
        check=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def scalar(sql: str, *, env: dict[str, str]) -> str:
    return run(
        ["psql", "-X", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-Atqc", sql],
        env=env,
    )


def download_snapshot(target: Path) -> dict[str, object]:
    from google.cloud import storage

    client = storage.Client(project=EXPECTED_PROJECT)
    blob = client.bucket(EXPECTED_BUCKET).blob(
        EXPECTED_OBJECT, generation=EXPECTED_GENERATION
    )
    blob.reload(if_generation_match=EXPECTED_GENERATION)
    metadata = {
        "generation": int(blob.generation),
        "size": int(blob.size),
        "md5": blob.md5_hash,
        "crc32c": blob.crc32c,
    }
    expected = {
        "generation": EXPECTED_GENERATION,
        "size": EXPECTED_SIZE,
        "md5": EXPECTED_MD5,
        "crc32c": EXPECTED_CRC32C,
    }
    if metadata != expected:
        raise RuntimeError(f"snapshot metadata mismatch: {metadata!r}")
    blob.download_to_filename(
        str(target), if_generation_match=EXPECTED_GENERATION, checksum="auto"
    )
    return metadata


def verify_local_snapshot(path: Path) -> None:
    import google_crc32c

    md5 = hashlib.md5(usedforsecurity=False)
    crc32c = google_crc32c.Checksum()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            md5.update(chunk)
            crc32c.update(chunk)
    md5_base64 = base64.b64encode(md5.digest()).decode("ascii")
    crc32c_base64 = base64.b64encode(crc32c.digest()).decode("ascii")
    if (size, md5_base64, crc32c_base64) != (
        EXPECTED_SIZE,
        EXPECTED_MD5,
        EXPECTED_CRC32C,
    ):
        raise RuntimeError("downloaded snapshot checksum or size mismatch")


def baseline_migrations(*, env: dict[str, str]) -> None:
    sql_dir = Path("/app/sql")
    create_ledger = (sql_dir / "000_schema_migrations.sql").read_text(encoding="utf-8")
    run(
        ["psql", "-X", "--no-psqlrc", "-v", "ON_ERROR_STOP=1"],
        env=env,
        input_text=create_ledger,
    )
    values: list[str] = []
    for name in BASELINE_MIGRATIONS:
        content = (sql_dir / name).read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        values.append(f"('{name}', '{checksum}')")
    statement = (
        "BEGIN;\n"
        "INSERT INTO mf.schema_migrations (migration_name, content_sha256) VALUES\n"
        + ",\n".join(values)
        + ";\nCOMMIT;\n"
    )
    run(
        ["psql", "-X", "--no-psqlrc", "-v", "ON_ERROR_STOP=1"],
        env=env,
        input_text=statement,
    )


def apply_runtime_grants(*, env: dict[str, str]) -> None:
    grants = """
BEGIN;
GRANT CONNECT ON DATABASE mfdataindia TO mfdata_app, mfdata_ingest;
GRANT USAGE ON SCHEMA mf TO mfdata_app, mfdata_ingest;
GRANT SELECT ON ALL TABLES IN SCHEMA mf TO mfdata_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA mf TO mfdata_ingest;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA mf TO mfdata_ingest;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA mf TO mfdata_app, mfdata_ingest;
ALTER DEFAULT PRIVILEGES FOR ROLE mfdata_admin IN SCHEMA mf
    GRANT SELECT ON TABLES TO mfdata_app;
ALTER DEFAULT PRIVILEGES FOR ROLE mfdata_admin IN SCHEMA mf
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mfdata_ingest;
ALTER DEFAULT PRIVILEGES FOR ROLE mfdata_admin IN SCHEMA mf
    GRANT USAGE, SELECT ON SEQUENCES TO mfdata_ingest;
ALTER DEFAULT PRIVILEGES FOR ROLE mfdata_admin IN SCHEMA mf
    GRANT EXECUTE ON FUNCTIONS TO mfdata_app, mfdata_ingest;
COMMIT;
"""
    run(
        ["psql", "-X", "--no-psqlrc", "-v", "ON_ERROR_STOP=1"],
        env=env,
        input_text=grants,
    )


def validate_restored_schema(*, env: dict[str, str]) -> dict[str, object]:
    required = (
        "mf.amcs",
        "mf.funds",
        "mf.fund_variants",
        "mf.nav_history",
        "mf.fund_family",
        "mf.v_fund_data_status",
    )
    for relation in required:
        if scalar(f"SELECT to_regclass('{relation}') IS NOT NULL", env=env) != "t":
            raise RuntimeError(f"required restored relation is absent: {relation}")

    manifest: dict[str, object] = {
        "amcs": int(scalar("SELECT count(*) FROM mf.amcs", env=env)),
        "funds": int(scalar("SELECT count(*) FROM mf.funds", env=env)),
        "fund_variants": int(scalar("SELECT count(*) FROM mf.fund_variants", env=env)),
        "fund_families": int(scalar("SELECT count(*) FROM mf.fund_family", env=env)),
        "nav_rows": int(scalar("SELECT count(*) FROM mf.nav_history", env=env)),
        "nav_min_date": scalar("SELECT min(nav_date) FROM mf.nav_history", env=env),
        "nav_max_date": scalar("SELECT max(nav_date) FROM mf.nav_history", env=env),
    }
    if any(int(manifest[key]) <= 0 for key in ("amcs", "funds", "fund_variants", "fund_families", "nav_rows")):
        raise RuntimeError(f"restored dataset has empty required relations: {manifest!r}")

    orphan_families = int(
        scalar(
            "SELECT count(*) FROM mf.fund_family ff WHERE NOT EXISTS "
            "(SELECT 1 FROM mf.fund_variants fv WHERE fv.group_key = ff.group_key)",
            env=env,
        )
    )
    missing_families = int(
        scalar(
            "SELECT count(*) FROM (SELECT DISTINCT group_key FROM mf.fund_variants) fv "
            "WHERE NOT EXISTS (SELECT 1 FROM mf.fund_family ff WHERE ff.group_key = fv.group_key)",
            env=env,
        )
    )
    default_partition_rows = int(
        scalar("SELECT count(*) FROM mf.nav_history_default", env=env)
    )
    forbidden_checkpoints = int(
        scalar(
            "SELECT count(*) FROM mf.ingest_checkpoints WHERE source IN ('GROWW', 'SCRIPBOX')",
            env=env,
        )
    )
    forbidden_fact_sources = int(
        scalar(
            "SELECT count(*) FROM mf.fund_facts WHERE source IN ('GROWW', 'SCRIPBOX')",
            env=env,
        )
    )
    manifest.update(
        {
            "orphan_families": orphan_families,
            "missing_families": missing_families,
            "default_partition_rows": default_partition_rows,
            "forbidden_checkpoints": forbidden_checkpoints,
            "forbidden_fact_sources": forbidden_fact_sources,
        }
    )
    if any(
        value != 0
        for value in (
            orphan_families,
            missing_families,
            default_partition_rows,
            forbidden_checkpoints,
            forbidden_fact_sources,
        )
    ):
        raise RuntimeError(f"restored data-integrity checks failed: {manifest!r}")
    return manifest


def main() -> int:
    if required_env("GOOGLE_CLOUD_PROJECT") != EXPECTED_PROJECT:
        raise RuntimeError("unexpected GCP project")
    admin_dsn = required_env("MFDATAINDIA_ADMIN_DSN")
    env = postgres_env(admin_dsn)
    if scalar("SELECT current_database()", env=env) != EXPECTED_DATABASE:
        raise RuntimeError("connected to an unexpected database")
    if scalar("SELECT to_regnamespace('mf') IS NULL", env=env) != "t":
        raise RuntimeError("target database is not empty; restore is one-shot and will not retry")
    if not run(["pg_restore", "--version"], env=env).startswith("pg_restore (PostgreSQL) 18."):
        raise RuntimeError("snapshot restore requires pg_restore 18")

    snapshot = Path("/tmp/mfdataindia.snapshot.dump")
    try:
        metadata = download_snapshot(snapshot)
        verify_local_snapshot(snapshot)
        run(
            [
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--jobs=2",
                f"--dbname={EXPECTED_DATABASE}",
                str(snapshot),
            ],
            env=env,
        )
        manifest = validate_restored_schema(env=env)
        baseline_migrations(env=env)
        apply_runtime_grants(env=env)
        run(
            ["psql", "-X", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-c", "ANALYZE mf.funds", "-c", "ANALYZE mf.nav_history"],
            env=env,
        )
    finally:
        snapshot.unlink(missing_ok=True)

    print(json.dumps({"status": "RESTORED_AND_BASELINED", "snapshot": metadata, "manifest": manifest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
