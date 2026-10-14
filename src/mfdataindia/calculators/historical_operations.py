"""Operational restored-clone proof; no restore-over-DEV or scenario storage."""

import secrets

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from mfdataindia.calculators import (
    historical_certification as certification,
    historical as engine,
    historical_store as store,
)
from mfdataindia.review import store as review
from mfdataindia.analytics.operations import capture_calendar, compare_calendar
from mfdataindia.analytics.rolling_operations import capture_rolling, compare_rolling
from mfdataindia.review.preservation import capture_preservation, compare_preservation
from mfdataindia.review.publication_operations import capture_review, compare_review
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.rollout import ROLES
from mfdataindia.review.rollout_database import file_sha256, readonly


def rehearse(clone_dsn, backup, directory, root, manifest, tools):
    if file_sha256(directory / "snapshot.dump") != backup["backup_sha256"]:
        raise ValueError("calculator rehearsal archive differs")
    parsed = conninfo_to_dict(clone_dsn)
    if parsed.get("host") != "127.0.0.1" or parsed.get("dbname") != "mfdata_review_clone_control":
        raise ValueError("isolated calculator control database required")
    name = "mfdata_historical_rehearsal_" + manifest.rollout_id.replace("-", "_")
    with psycopg.connect(clone_dsn, autocommit=True, row_factory=dict_row) as admin:
        databases = {
            r["datname"] for r in admin.execute("SELECT datname FROM pg_database").fetchall()
        }
        roles = {
            r["rolname"]
            for r in admin.execute(
                "SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_' "
            ).fetchall()
        }
        if databases != {
            "postgres",
            "template0",
            "template1",
            "mfdata_review_clone_control",
        } or roles != {"postgres"}:
            raise ValueError("clone not empty; never overwrite retained data or roles")
        for role in backup["roles"]:
            if role != "postgres":
                admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    restored = make_conninfo(clone_dsn, dbname=name)
    tools.run(
        "pg_restore",
        restored,
        directory,
        ["--dbname=" + name, "--exit-on-error", "--single-transaction", "/backup/snapshot.dump"],
    )
    with readonly(restored) as conn:
        before = capture_preservation(conn)
        prior = capture_review(conn)
        cal = capture_calendar(conn)
        roll = capture_rolling(conn)
    compare_preservation(backup["baseline"], before, manifest.migration_hash)
    compare_review(backup["review_baseline"], prior)
    compare_calendar(backup.get("analytics_baseline"), cal)
    compare_rolling(backup.get("rolling_baseline"), roll)
    password = secrets.token_hex(32)
    with psycopg.connect(restored) as conn:
        conn.execute(
            sql.SQL("ALTER ROLE mfdata_admin LOGIN PASSWORD {}").format(sql.Literal(password))
        )
    owner = make_conninfo(restored, user="mfdata_admin", password=password)
    with psycopg.connect(owner, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        installed = store.install_policy(conn)
    passwords = {role: secrets.token_hex(32) for role in ROLES}
    with psycopg.connect(restored) as conn:
        for role, value in passwords.items():
            conn.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(value)
                )
            )
    for role, value in passwords.items():
        with readonly(make_conninfo(restored, user=role, password=value)) as conn:
            verify_runtime(conn, "generator" if role == ROLES[0] else "api")
            store.installed_policy(conn)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from fastapi.testclient import TestClient
    from mfdataindia.review.app import create_historical_calculator_app
    from mfdataindia.review.auth import AssertionVerifier
    import jwt
    import time

    key = Ed25519PrivateKey.generate()
    trust = AssertionVerifier(
        public_key=key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
        issuer="clone-historical",
        audience="clone-historical",
        caller="clone-historical",
        allowed_scopes=frozenset({"review:read", "review:admin"}),
    )
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "clone-historical",
            "aud": "clone-historical",
            "caller": "clone-historical",
            "sub": manifest.actor,
            "scope": ["review:read"],
            "iat": now,
            "nbf": now,
            "exp": now + 240,
            "jti": "clone-historical-only",
        },
        key,
        algorithm="EdDSA",
    )
    api_dsn = make_conninfo(restored, user=ROLES[2], password=passwords[ROLES[2]])
    with readonly(restored) as conn:
        registered = capture_review(conn)
    ids = []
    covered = set()
    cases = []
    with readonly(restored) as conn:
        for code in manifest.cohort:
            snapshot = review.capture(conn, code)
            for body in certification.scenarios(snapshot):
                cases.append((body, engine.derive(body, snapshot, engine.policy())))
    with TestClient(create_historical_calculator_app(api_dsn, verifier=trust)) as client:
        path = "/api/calculators/v1/backtests"
        for body, expected in cases:
            if client.post(path, json=body).status_code != 401:
                raise ValueError("clone historical assertion denial failed")
            headers = {"Authorization": "Bearer " + token}
            for _ in range(2):
                reply = client.post(path, json=body, headers=headers)
                if (
                    reply.status_code != 200
                    or reply.json() != expected
                    or reply.headers.get("Cache-Control") != "no-store"
                ):
                    raise ValueError("clone historical source/private replay differs")
            ids.append(expected["result_id"])
            if expected["available"] and expected["summary"]["xirr_pct"] is not None:
                covered.add(body["kind"])
    if covered != {"lumpsum", "sip"}:
        raise ValueError("real restored Growth SIP/lumpsum coverage with XIRR required")
    with readonly(restored) as conn:
        compare_preservation(before, capture_preservation(conn), manifest.migration_hash)
        compare_review(registered, capture_review(conn))
        compare_calendar(cal, capture_calendar(conn))
        compare_rolling(roll, capture_rolling(conn))
    return {
        "level": "OPERATIONAL_RESTORED_CLONE",
        "restored_database": name,
        "backup_sha256": backup["backup_sha256"],
        "installation": installed,
        "source_preserved": True,
        "prior_review_preserved": True,
        "prior_calendar_preserved": True,
        "prior_rolling_preserved": True,
        "personal_scenarios_persisted": 0,
        "schema_changes": 0,
        "data_deleted": 0,
        "scenario_count": len(ids),
        "clone_result_ids": ids,
        "cloud_iam_or_consumer_rbac_certified": False,
    }
