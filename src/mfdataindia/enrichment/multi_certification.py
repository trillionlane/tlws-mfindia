"""Exact-source private HTTP/core certification; not all-AMC or human acceptance."""

import json
import time
from pathlib import Path
from uuid import uuid4
import jwt
import psycopg
from psycopg.rows import dict_row
import requests

from mfdataindia.enrichment import (
    multi_facts,
    multi_store,
    multi_projection,
    current_facts_store as current,
    fact_review_store,
)
from mfdataindia.enrichment.certification import check_http
from mfdataindia.enrichment.current_facts_certification import core_http, REVIEW_ORIGIN
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.rollout import seal_evidence


def expectation(manifest, environment):
    doc = json.loads(environment["REGISTERED_FACTS_CERTIFICATION"])
    if (
        set(doc) != {"reviewed", "prior_core_values", "execution_id", "pilot_snapshot_ids"}
        or fingerprint({"manifest": manifest.seal, "expectation": doc})
        != environment["REGISTERED_FACTS_CERTIFICATION_HASH"]
    ):
        raise ValueError("independent original and prior-core certification binding required")
    return doc


def certify(manifest, environment, *, active=False, recovery=False):
    if active and recovery:
        raise ValueError("recovery cannot claim registered projection active")
    expected = expectation(manifest, environment)
    dsn = pinned_dsn(manifest, environment, "mfdata_verified_reader")
    origin, audience = request_origins(environment)
    candidate = (
        "https://cert-" + manifest.rollout_id + "---" + REVIEW_ORIGIN.removeprefix("https://")
    )
    if audience != REVIEW_ORIGIN or origin not in (REVIEW_ORIGIN, candidate):
        raise ValueError("exact private registered source origin required")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        multi_store.verify_acl(conn)
        before = (multi_store.capture(conn), current.capture(conn), fact_review_store.capture(conn))
        status = multi_store.status(conn)
        facts = multi_store.get(conn, 101763)
        matches = [
            r
            for r in facts["snapshots"]
            if r["snapshot"]["snapshot_id"] == expected["reviewed"]["snapshot_id"]
        ]
        if (
            len(matches) != 1
            or not matches[0]["published"]
            or not matches[0]["current_identity_matches"]
        ):
            raise ValueError("exact source snapshot publication and identity required")
        snapshot = matches[0]["snapshot"]
        if snapshot["disclosure_as_of"] != expected["reviewed"]["as_of"] or any(
            snapshot["fields"][field]["value"] != value
            for field, value in expected["reviewed"]["values"].items()
        ):
            raise ValueError("snapshot differs from independent original-page reference")
        run = conn.execute(
            "SELECT document FROM mf_multi_facts.runs WHERE execution_id=%s AND status<>'failed'",
            (expected["execution_id"],),
        ).fetchone()
        if not run or run["document"]["snapshot_ids"] != [snapshot["snapshot_id"]]:
            raise ValueError("successful exact-execution registered receipt required")
        pilot = conn.execute(
            "SELECT document FROM mf_fact_refresh.runs WHERE execution_id=%s AND status<>'failed'",
            (expected["execution_id"],),
        ).fetchone()
        if (
            not pilot
            or pilot["document"].get("mode") != "scheduled_refresh"
            or len(expected["pilot_snapshot_ids"]) != 2
            or set(pilot["document"]["snapshot_ids"]) != set(expected["pilot_snapshot_ids"])
        ):
            raise ValueError("both registered adapters require successful same-execution receipts")
        metadata = current.get(conn, 101763)
        if active:
            multi_projection.check(conn, expected["reviewed"])
            values = {}
            for field, value in expected["reviewed"]["values"].items():
                if field == "fund_managers":
                    value = "; ".join(value)
                if field in ("base_expense_ratio", "aum"):
                    value = float(value)
                values.update(
                    {a: value for a in multi_facts.current_policy()["supported_fields"][field]}
                )
        else:
            from mfdataindia.enrichment import monthly_current

            if current.policy_document(
                conn
            ) != monthly_current.policy() or not current.projection_state(conn):
                raise ValueError("prior active pilot graph not restored/retained")
            values = expected["prior_core_values"]
        policy = multi_facts.policy()
        cases = [
            ("/api/enrichment/v8/policy", {"policy_hash": fingerprint(policy), "document": policy}),
            ("/api/enrichment/v8/refresh/status", status),
            ("/api/enrichment/v8/funds/101763/facts", facts),
        ]
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("registered certification trust differs")
    verifier = AssertionVerifier(**{**trust, "allowed_scopes": frozenset(trust["allowed_scopes"])})
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": verifier.issuer,
            "aud": verifier.audience,
            "caller": verifier.caller,
            "sub": manifest.actor,
            "scope": ["review:read"],
            "iat": now,
            "nbf": now,
            "exp": now + 240,
            "jti": uuid4().hex,
        },
        Path("/signer/key.pem").read_text(),
        algorithm="EdDSA",
    )
    with requests.Session() as session:
        session.trust_env = False
        headers = {
            "X-Serverless-Authorization": "Bearer " + metadata_identity(session, audience),
            "Authorization": "Bearer " + assertion,
        }
        check_http(session, origin, headers, cases, recovery=recovery)
        if not recovery:
            check_http(
                session, origin, headers, [("/api/enrichment/v3/funds/101763/facts", metadata)]
            )
            for route, _ in cases:
                if (
                    session.post(
                        origin + route, json={}, headers=headers, timeout=30, allow_redirects=False
                    ).status_code
                    != 405
                ):
                    raise ValueError("registered source HTTP mutation unexpectedly permitted")
            for suffix in ("/raw", "/content"):
                if (
                    session.get(
                        origin + "/api/enrichment/v8/funds/101763/facts" + suffix,
                        headers=headers,
                        timeout=30,
                        allow_redirects=False,
                    ).status_code
                    != 404
                ):
                    raise ValueError("registered original bytes exposed over HTTP")
        core_responses = core_http(session, {"101763": values})
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if before != (
            multi_store.capture(conn),
            current.capture(conn),
            fact_review_store.capture(conn),
        ):
            raise ValueError("registered certification dependencies changed")
    return seal_evidence(
        manifest,
        "registered-facts-recovery-http" if recovery else "registered-facts-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "projection_active": active,
            "profile_available": not recovery,
            "metadata_responses_checked": len(cases) + (0 if recovery else 1),
            "core_responses_checked": core_responses,
            "source_snapshot_id": snapshot["snapshot_id"],
            "source_rows_changed": 0,
            "human_events_created": 0,
            "independent_original_source_reference_checked": True,
            "all_amc_data_complete": False,
            "adjusted_idcw_analytics_enabled": False,
        },
    )
