"""Restricted-reader original-to-private-HTTP certification of current publication."""

import json
import time
from pathlib import Path
from uuid import uuid4
import jwt
import psycopg
import requests
from psycopg.rows import dict_row
from mfdataindia.enrichment import current_facts_store as current, monthly_projection
from mfdataindia.enrichment import refresh_policy, refresh_store
from mfdataindia.enrichment.certification import check_http
from mfdataindia.enrichment.current_facts_certification import core_http, REVIEW_ORIGIN
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.rollout import seal_evidence


def certify(manifest, environment, *, active=False, recovery=False):
    if active and recovery:
        raise ValueError("recovery cannot claim active monthly projection")
    document = json.loads(environment["FACT_REFRESH_CERTIFICATION"])
    if (
        set(document) != {"reviewed", "prior_core_values", "execution_id"}
        or fingerprint({"manifest": manifest.seal, "expectation": document})
        != environment["FACT_REFRESH_CERTIFICATION_HASH"]
    ):
        raise ValueError("bound source/current/recovery expectations required")
    origin, audience = request_origins(environment)
    candidate = (
        "https://cert-" + manifest.rollout_id + "---" + REVIEW_ORIGIN.removeprefix("https://")
    )
    if audience != REVIEW_ORIGIN or origin not in (REVIEW_ORIGIN, candidate):
        raise ValueError("exact private source refresh origin required")
    dsn = pinned_dsn(manifest, environment, "mfdata_verified_reader")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        before = (refresh_store.capture(conn), current.capture(conn))
        status = refresh_store.status(conn)
        if document["execution_id"]:
            row = conn.execute(
                "SELECT document FROM mf_fact_refresh.runs WHERE execution_id=%s",
                (document["execution_id"],),
            ).fetchone()
            if not row or row["document"]["status"] not in (
                "updated",
                "unchanged",
                "completed_with_field_gaps",
            ):
                raise ValueError("successful pinned runtime receipt missing")
            receipt = row["document"]
            if (
                receipt.get("mode") != "scheduled_refresh"
                or not receipt.get("archive")
                or set(receipt["snapshot_ids"])
                != {r["snapshot_id"] for r in document["reviewed"].values()}
            ):
                raise ValueError("runtime receipt differs from reviewed original source")
        new_cases = [
            (
                "/api/enrichment/v5/policy",
                {
                    "policy_hash": fingerprint(refresh_policy.policy()),
                    "document": refresh_policy.policy(),
                },
            ),
            ("/api/enrichment/v5/status", status),
        ]
        policy = current.policy_document(conn)
        previous_cases = [
            ("/api/enrichment/v3/policy", {"policy_hash": fingerprint(policy), "document": policy})
        ]
        previous_cases.extend(
            (f"/api/enrichment/v3/funds/{code}/facts", current.get(conn, code))
            for code in manifest.cohort
        )
        core = (
            monthly_projection.check_current(conn, document["reviewed"], core_queries=False)
            if active
            else document["prior_core_values"]
        )
        if set(map(int, core)) != {103173, 103174}:
            raise ValueError("both exact current core expectations required")
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("refresh certification trust differs")
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
        check_http(session, origin, headers, new_cases, recovery=recovery)
        check_http(session, origin, headers, previous_cases)
        checked = core_http(session, core)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if before != (refresh_store.capture(conn), current.capture(conn)):
            raise ValueError("read-only refresh certification observed state changes")
    return seal_evidence(
        manifest,
        "fact-refresh-recovery-http" if recovery else "fact-refresh-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "projection_active": active,
            "new_profile_available": not recovery,
            "metadata_responses_checked": len(new_cases) + len(previous_cases),
            "existing_core_responses_checked": checked,
            "runtime_execution_id": document["execution_id"],
            "source_reference_checked": active,
            "source_rows_changed": 0,
            "records_appended": 0,
            "human_verification": "needs_verification",
            "all_amc_coverage": False,
            "UI_certified": False,
        },
    )
