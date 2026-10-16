"""Real private read/denial proof; no valid human-review mutation is submitted."""

import json
import time
from pathlib import Path
from uuid import uuid4
import jwt
import psycopg
import requests
from psycopg.rows import dict_row
from mfdataindia.enrichment import fact_review_policy, fact_review_store as store
from mfdataindia.enrichment.certification import check_http
from mfdataindia.enrichment.current_facts_certification import REVIEW_ORIGIN
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.enrichment.monthly_deployment import references
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.rollout import seal_evidence


def projections(conn, reviewed):
    cases = []
    for code in (103173, 103174):
        expected = sorted((r for r in reviewed if r["code"] == code), key=lambda r: r["as_of"])
        if len(expected) != 2:
            raise ValueError("both source periods required for history/change certification")
        history = store.history(conn, code)
        if {r["snapshot"]["snapshot_id"] for r in history["snapshots"]} != {
            r["snapshot_id"] for r in expected
        }:
            raise ValueError("source history differs from the exact two original periods")
        changes = store.changes(conn, code, expected[0]["snapshot_id"], expected[1]["snapshot_id"])
        for field in set(expected[0]["fields"]) & set(expected[1]["fields"]):
            left, right = expected[0]["fields"][field], expected[1]["fields"][field]
            if left != right:
                actual = changes["fields"].get(field)
                if (
                    not actual
                    or {k: actual["before"][k] for k in left} != left
                    or {k: actual["after"][k] for k in right} != right
                ):
                    raise ValueError(
                        "source difference does not match independent value/unit/status references"
                    )
        if (
            expected[0]["fields"]["risk_level"] == expected[1]["fields"]["risk_level"]
            and "risk_level" in changes["fields"]
        ):
            raise ValueError("unchanged reported risk must not masquerade as a risk change")
        route = f"/api/enrichment/v6/funds/{code}"
        cases.extend(
            [
                (route + "/history", history),
                (
                    route
                    + "/changes?from_snapshot_id="
                    + expected[0]["snapshot_id"]
                    + "&to_snapshot_id="
                    + expected[1]["snapshot_id"],
                    changes,
                ),
                (route + "/verification", store.state(conn, code)),
            ]
        )
    return cases


def certify(manifest, environment, *, recovery=False):
    reviewed = references(manifest, environment)
    dsn = pinned_dsn(manifest, environment, "mfdata_verified_reader")
    origin, audience = request_origins(environment)
    candidate = (
        "https://cert-" + manifest.rollout_id + "---" + REVIEW_ORIGIN.removeprefix("https://")
    )
    if audience != REVIEW_ORIGIN or origin not in (REVIEW_ORIGIN, candidate):
        raise ValueError("exact private verification origin required")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        before = store.capture(conn)
        cases = projections(conn, reviewed)
        policy = fact_review_policy.policy()
        policy_case = (
            "/api/review/v1/policies/" + fingerprint(policy),
            {
                "policy_hash": fingerprint(policy),
                "policy_id": policy["id"],
                "policy_version": policy["version"],
                "document": policy,
            },
        )
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("verification trust differs")
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
        check_http(session, origin, headers, [policy_case])
        for code in manifest.cohort:
            # Empty request is deliberately incapable of creating a human
            # review even if an authorization regression slips through.
            denied = session.post(
                origin + f"/api/enrichment/v6/funds/{code}/reviews",
                json={},
                headers={**headers, "Idempotency-Key": "certification-denied-" + str(code)},
                timeout=30,
                allow_redirects=False,
            )
            if denied.status_code != (404 if recovery else 403):
                raise ValueError("private review admin scope not enforced")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if store.capture(conn) != before:
            raise ValueError("certification changed human verification ledger")
    return seal_evidence(
        manifest,
        "fact-verification-recovery-http" if recovery else "fact-verification-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "profile_available": not recovery,
            "source_history_changes_verification_responses_checked": len(cases),
            "registered_policy_checked": True,
            "read_scope_cannot_review": True,
            "human_verification_created_by_certifier": False,
            "verification_events_before": before["events"]["rows"],
            "verification_events_after": before["events"]["rows"],
            "source_rows_changed": 0,
            "records_appended": 0,
            "all_amc_coverage": False,
            "UI_certified": False,
        },
    )
