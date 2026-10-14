"""Explicit optional installation with source AND prior-sidecar preservation."""

import hashlib
import re
from pathlib import Path

from psycopg import sql

from mfdataindia.analytics import rolling, rolling_store as store
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation, compare_preservation
from mfdataindia.review.publication_operations import capture_review, compare_review
from mfdataindia.analytics.operations import capture_calendar, compare_calendar
from mfdataindia.review.rollout import ROLES
from mfdataindia.review.privileges import verify_principal
from mfdataindia.store.locks import acquire_nav_data_operation_lock

MIGRATION = "026_rolling_analytics.sql"
SCHEMA_SHA256 = "45774437379f736c9f1465935c6071951072674e5affb9abeee60e99eba3af41"
TABLES = {"rolling_results": "result_id", "rolling_certifications": "certificate_id"}


def verified_record(cloud, manifest, execution_name, origin, versions):
    """Actual immutable consumer execution, not an operator-supplied PASS."""
    from mfdataindia.review.image_provenance import verify_image
    from mfdataindia.review.rollout import CALLER_SA, SECRETS, service_origin, verify_evidence
    from mfdataindia.review.rollout_cloud import (
        ROLLING_FACTORY,
        revision_name,
        verify_revision,
        verify_service,
    )

    origin = service_origin(origin)
    service = cloud.service()
    verify_service(service)
    if service["status"]["url"] != origin:
        raise ValueError("rolling registration requires the fixed service origin")
    traffic = service["status"]["traffic"]
    serving = [item for item in traffic if item.get("percent", 0) > 0]
    expected = revision_name(manifest, ROLLING_FACTORY)
    if (
        any(item.get("tag") for item in traffic)
        or len(serving) != 1
        or serving[0]["revisionName"] != expected
        or serving[0]["percent"] != 100
    ):
        raise ValueError("rolling certificate service is not the exact untagged serving revision")
    verify_revision(cloud.revision(expected), manifest, versions, factory=ROLLING_FACTORY)
    cloud.iam(complete=True)
    verify_image(cloud, manifest.image, manifest.git_sha)
    environment = {
        "DEPLOYMENT_GIT_SHA": manifest.git_sha,
        "REVIEW_MANIFEST": manifest.model_dump_json(),
        "REVIEW_ORIGIN": origin,
        "REVIEW_AUDIENCE": origin,
    }
    references = [
        "MFDATAINDIA_DSN=" + SECRETS[ROLES[1]] + ":" + str(versions[ROLES[1]]),
        "/trust/config.json=" + manifest.trust_secret + ":" + str(manifest.trust_version),
        "/signer/key.pem=" + manifest.signer_secret + ":" + str(manifest.signer_version),
    ]
    execution, certificate = cloud.inspect_execution(
        manifest,
        execution_name,
        CALLER_SA,
        ["scripts/certify_rolling_analytics_dev.py"],
        environment,
        references,
    )
    evidence = verify_evidence(manifest, "rolling-http", certificate)
    if (
        evidence.get("level") != "DEV_PRIVATE_HTTP"
        or evidence.get("source_writes") != 0
        or evidence.get("records_appended") != 0
        or evidence.get("origin") != origin
        or evidence.get("audience") != origin
        or evidence.get("policy_hash") != fingerprint(rolling.policy())
        or evidence.get("human_review_or_consumer_rbac_certified") is not False
        or len(evidence.get("cohort", [])) != len(manifest.cohort)
        or {r["code"] for r in evidence["cohort"]} != set(manifest.cohort)
        or not set(manifest.required_coverage) <= set(evidence.get("coverage_observed", []))
    ):
        raise ValueError("rolling conformance scope or dependencies differ")
    return {
        "format": "rolling-analytics-certification-v1",
        "policy_hash": fingerprint(rolling.policy()),
        "manifest": manifest.model_dump(mode="json"),
        "execution": execution,
        "certificate": certificate,
    }


def capture_rolling(conn):
    if not conn.execute("SELECT to_regnamespace('mf_rolling') AS namespace").fetchone()[
        "namespace"
    ]:
        return None
    inventory = conn.execute(
        "SELECT c.relname,pg_get_userbyid(c.relowner) AS owner FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_rolling' AND c.relkind IN('r','p','m','v','S')"
    ).fetchall()
    if {r["relname"] for r in inventory} != set(TABLES) or any(
        r["owner"] not in ("mfdata_admin", "postgres") for r in inventory
    ):
        raise ValueError("unmodelled rolling sidecar inventory or ownership")
    conn.execute("SET LOCAL TimeZone='UTC'")
    rows = {}
    for table, key in TABLES.items():
        query = sql.SQL(
            "SELECT {}::text,to_jsonb(t)::text FROM mf_rolling.{} t ORDER BY {}"
        ).format(sql.Identifier(key), sql.Identifier(table), sql.Identifier(key))
        values = {}
        with (
            conn.cursor() as cursor,
            cursor.copy(sql.SQL("COPY ({}) TO STDOUT (FORMAT BINARY)").format(query)) as copy,
        ):
            copy.set_types(["text", "text"])
            for identifier, value in copy.rows():
                values[identifier] = hashlib.sha256(value.encode()).hexdigest()
        rows[table] = values
    return rows


def compare_rolling(before, after, *, allow_empty_install=False):
    if before is None:
        if after is not None and (not allow_empty_install or any(after.values())):
            raise ValueError("undeclared rolling sidecar installation/addition")
        return
    if before != after:
        raise ValueError("prior rolling rows changed or undeclared additions appeared")


def install(conn, root: Path):
    with conn.transaction():
        identity = conn.execute(
            "SELECT session_user AS principal,current_user=session_user AS direct"
        ).fetchone()
        if not identity["direct"] or identity["principal"] not in ("mfdata_admin", "postgres"):
            raise ValueError("direct migration-owner login required")
        if (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "repeatable read"
        ):
            raise ValueError("rolling installation requires consistent transaction")
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute("SET LOCAL statement_timeout='120s'")
        conn.execute("SET LOCAL transaction_timeout='180s'")
        acquire_nav_data_operation_lock(conn)
        if conn.execute("SELECT to_regnamespace('mf_rolling') AS namespace").fetchone()[
            "namespace"
        ]:
            for role in ROLES:
                store.verify_acl(conn, principal=role)
            return {"status": "ALREADY_INSTALLED", "source_rows_changed": 0}
        source = (root / "sql/optional" / MIGRATION).read_text()
        if hashlib.sha256(source.encode()).hexdigest() != SCHEMA_SHA256:
            raise ValueError("rolling migration differs from reviewed pin")
        for namespace in ("mf", "mf_review", "mf_analytics"):
            tables = conn.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relkind IN('r','p') AND NOT c.relispartition ORDER BY c.relname",
                (namespace,),
            ).fetchall()
            for table in tables:
                conn.execute(
                    sql.SQL("LOCK TABLE {}.{} IN SHARE MODE").format(
                        sql.Identifier(namespace), sql.Identifier(table["relname"])
                    )
                )
        before = capture_preservation(conn)
        prior = capture_review(conn)
        prior_calendar = capture_calendar(conn)
        if len(re.findall(r"^BEGIN;$", source, re.M)) != 1 or not source.rstrip().endswith(
            "COMMIT;"
        ):
            raise ValueError("rolling migration transaction shape differs")
        conn.execute(
            re.sub(r"^BEGIN;\n", "", source, count=1, flags=re.M).rstrip()[: -len("COMMIT;")]
        )
        document = rolling.policy()
        review._insert_exact(
            conn,
            "policies",
            "policy_hash",
            fingerprint(document),
            document,
            {"policy_id": document["id"], "policy_version": document["version"]},
        )
        for role in ROLES:
            verify_principal(conn, "generator" if role == ROLES[0] else "api", role)
            store.verify_acl(conn, principal=role)
        compare_preservation(before, capture_preservation(conn), "")
        compare_review(prior, capture_review(conn), added_policy=fingerprint(document))
        compare_calendar(prior_calendar, capture_calendar(conn))
        compare_rolling(None, capture_rolling(conn), allow_empty_install=True)
        return {
            "status": "INSTALLED",
            "source_rows_changed": 0,
            "prior_review_preserved": True,
            "policy_hash": fingerprint(document),
            "schema_sha256": SCHEMA_SHA256,
            "source_migration_ledger_appended": False,
        }


def rehearse(clone_dsn, backup, directory, root, manifest, tools):
    """Restore only onto a fresh task-owned server; never restores over DEV."""
    import secrets
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from psycopg.rows import dict_row
    from mfdataindia.review.rollout_database import file_sha256, readonly

    if file_sha256(directory / "snapshot.dump") != backup["backup_sha256"]:
        raise ValueError("rolling rehearsal archive differs")
    parsed = conninfo_to_dict(clone_dsn)
    if parsed.get("host") != "127.0.0.1" or parsed.get("dbname") != "mfdata_review_clone_control":
        raise ValueError("rolling rehearsal requires isolated runner control database")
    name = "mfdata_roll_rehearsal_" + manifest.rollout_id.replace("-", "_")
    with psycopg.connect(clone_dsn, autocommit=True, row_factory=dict_row) as admin:
        databases = {
            r["datname"] for r in admin.execute("SELECT datname FROM pg_database").fetchall()
        }
        roles = {
            r["rolname"]
            for r in admin.execute(
                "SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_'"
            ).fetchall()
        }
        if databases != {
            "postgres",
            "template0",
            "template1",
            "mfdata_review_clone_control",
        } or roles != {"postgres"}:
            raise ValueError("clone server not empty; no overwrite or role replacement allowed")
        for role in backup["roles"]:
            if role != "postgres":
                admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    restored_dsn = make_conninfo(clone_dsn, dbname=name)
    tools.run(
        "pg_restore",
        restored_dsn,
        directory,
        ["--dbname=" + name, "--exit-on-error", "--single-transaction", "/backup/snapshot.dump"],
    )
    with readonly(restored_dsn) as conn:
        original = capture_preservation(conn)
        prior = capture_review(conn)
        analytics = capture_rolling(conn)
        prior_calendar = capture_calendar(conn)
    compare_preservation(backup["baseline"], original, manifest.migration_hash)
    compare_review(backup["review_baseline"], prior)
    compare_rolling(backup.get("rolling_baseline"), analytics)
    compare_calendar(backup.get("analytics_baseline"), prior_calendar)
    password = secrets.token_hex(32)
    with psycopg.connect(restored_dsn) as conn:
        conn.execute(
            sql.SQL("ALTER ROLE mfdata_admin LOGIN PASSWORD {}").format(sql.Literal(password))
        )
        conn.execute(
            sql.SQL("GRANT CREATE ON DATABASE {} TO mfdata_admin").format(sql.Identifier(name))
        )
    owner_dsn = make_conninfo(restored_dsn, user="mfdata_admin", password=password)
    with psycopg.connect(owner_dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        installed = install(conn, root)
    passwords = {role: secrets.token_hex(32) for role in ROLES}
    with psycopg.connect(restored_dsn) as conn:
        for role, value in passwords.items():
            conn.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(value)
                )
            )
    generator = make_conninfo(restored_dsn, user=ROLES[0], password=passwords[ROLES[0]])
    from mfdataindia.review.privileges import verify_runtime

    for role, value in passwords.items():
        with readonly(make_conninfo(restored_dsn, user=role, password=value)) as conn:
            verify_runtime(conn, "generator" if role == ROLES[0] else "api")
            store.verify_acl(conn)
    with readonly(generator) as conn:
        verify_runtime(conn, "generator")
        store.verify_acl(conn)
        snapshots = [review.capture(conn, code) for code in manifest.cohort]
    with psycopg.connect(generator, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        results = store.publish(conn, snapshots)
        if store.publish(conn, snapshots) != results:
            raise ValueError("restored rolling replay differs")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi.testclient import TestClient
    from mfdataindia.review.app import create_rolling_app
    from mfdataindia.review.auth import AssertionVerifier
    from mfdataindia.analytics.rolling_schemas import AuditResponse, PagedResponse
    import jwt
    import time

    key = Ed25519PrivateKey.generate()
    trust = AssertionVerifier(
        public_key=key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode(),
        issuer="clone-rolling",
        audience="clone-rolling",
        caller="clone-rolling",
        allowed_scopes=frozenset({"review:read", "review:admin"}),
    )
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "clone-rolling",
            "aud": "clone-rolling",
            "caller": "clone-rolling",
            "sub": manifest.actor,
            "scope": ["review:admin"],
            "iat": now,
            "nbf": now,
            "exp": now + 240,
            "jti": "clone-rolling-only",
        },
        key,
        algorithm="EdDSA",
    )
    admin_dsn = make_conninfo(restored_dsn, user=ROLES[2], password=passwords[ROLES[2]])
    with TestClient(create_rolling_app(admin_dsn, verifier=trust)) as client:
        for result in results:
            path = "/api/research/v1/funds/" + str(result["code"]) + "/rolling-returns"
            if client.get(path).status_code != 401:
                raise ValueError("restored rolling assertion denial failed")
            headers = {"Authorization": "Bearer " + token}
            reply = client.get(path, headers=headers)
            if reply.status_code != 200:
                raise ValueError("restored rolling projection failed")
            PagedResponse.model_validate(reply.json())
            audited = client.get(path + "?result_id=" + result["result_id"], headers=headers)
            if (
                audited.status_code != 200
                or AuditResponse.model_validate(audited.json()).result.result_id
                != result["result_id"]
            ):
                raise ValueError("restored rolling serialization/audit read differs")
    with readonly(restored_dsn) as conn:
        compare_preservation(original, capture_preservation(conn), manifest.migration_hash)
        later = capture_review(conn)
        compare_calendar(prior_calendar, capture_calendar(conn))
        for table in ("policies", "snapshots", "results", "events", "certifications"):
            if any(
                later.get(table, {}).get(key) != value
                for key, value in prior.get(table, {}).items()
            ):
                raise ValueError("restored prior review data changed")
        current = capture_rolling(conn)
        if analytics is not None and any(
            current[table].get(key) != value
            for table in analytics
            for key, value in analytics[table].items()
        ):
            raise ValueError("restored prior analytics changed")
    return {
        "level": "OPERATIONAL_RESTORED_CLONE",
        "restored_database": name,
        "backup_sha256": backup["backup_sha256"],
        "installation": installed,
        "source_preserved": True,
        "prior_review_preserved": True,
        "prior_analytics_preserved": True,
        "replay_verified": True,
        "cohort_results": [
            {k: r[k] for k in ("code", "result_id", "input_hash", "policy_hash")} for r in results
        ],
        "data_deleted": 0,
        "cloud_iam_or_consumer_rbac_certified": False,
        "http_evidence_level": "RESTORED_CLONE_HTTP",
    }
