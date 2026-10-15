"""Real private observation HTTP certification, not complete-history certification."""

import json
import time
from pathlib import Path
from uuid import uuid4
import jwt
import psycopg
import requests
from psycopg.rows import dict_row
from mfdataindia.enrichment import distributions as engine, distribution_store as store
from mfdataindia.enrichment.distribution_deployment import selection
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.enrichment.certification import check_http
from mfdataindia.analytics.overlap_certification import retained as prior_retained
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.rollout import seal_evidence


def oracle_projection(document):
    return {
        k: document[k]
        for k in (
            "code",
            "identity",
            "window",
            "events",
            "conditional_announcement",
            "coverage_state",
            "complete_event_window_proven",
            "analytics_eligible",
        )
    }


def projections(conn, batch, snapshot_id, oracle):
    response = store.get(conn, batch.request.identity.code, snapshot_id)
    document = response["snapshot"]
    if (
        response["state"] != "observed"
        or document is None
        or not document["events"]
        or oracle_projection(document) != oracle
    ):
        raise ValueError("independent original-source observation oracle differs")
    if document["request"] != batch.request.model_dump(mode="json"):
        raise ValueError("snapshot source/identity/window request differs")
    coverage = store.coverage(conn, document["code"])
    if (
        coverage["coverage_state"] != "unknown"
        or coverage["analytics_eligible"]
        or coverage["complete_event_window_proven"]
    ):
        raise ValueError("observations overclaim coverage or analytics")
    base = f"/api/distributions/v1/funds/{document['code']}"
    return [
        (
            "/api/distributions/v1/policy",
            {"policy_hash": fingerprint(engine.policy()), "document": store.installed_policy(conn)},
        ),
        (base + "/coverage", coverage),
        (base + "/observations?snapshot_id=" + snapshot_id, response),
    ]


def retained(conn):
    return {
        "prior": prior_retained(conn),
        "sources": conn.execute(
            "SELECT source_id,metadata FROM mf_distributions.sources ORDER BY source_id"
        ).fetchall(),
        "snapshots": conn.execute(
            "SELECT snapshot_id,encode(sha256(convert_to(document::text,'UTF8')),'hex') AS hash FROM mf_distributions.snapshots ORDER BY snapshot_id"
        ).fetchall(),
    }


def certify(manifest, environment, *, recovery=False):
    batch = selection(manifest, environment)
    snapshot_id = environment["DISTRIBUTION_SNAPSHOT_ID"]
    oracle = json.loads(environment["DISTRIBUTION_EXPECTATIONS"])
    binding = fingerprint(
        {"batch": batch.model_dump(mode="json"), "snapshot_id": snapshot_id, "oracle": oracle}
    )
    if binding != environment["DISTRIBUTION_CERTIFICATION_HASH"]:
        raise ValueError("observation certification approval differs")
    dsn = pinned_dsn(manifest, environment, store.evidence.ROLES[1])
    origin, audience = request_origins(environment)
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("observation certifier trust differs")
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
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        store.verify_acl(conn)
        before = retained(conn)
        cases = projections(conn, batch, snapshot_id, oracle)
    with requests.Session() as session:
        session.trust_env = False
        headers = {
            "X-Serverless-Authorization": "Bearer " + metadata_identity(session, audience),
            "Authorization": "Bearer " + assertion,
        }
        check_http(session, origin, headers, cases, recovery=recovery)
        if not recovery:
            base = f"/api/distributions/v1/funds/{batch.request.identity.code}/observations"
            for path, method, expected in ((base, "get", 422), (cases[0][0], "post", 405)):
                if (
                    getattr(session, method)(
                        origin + path, headers=headers, timeout=30, allow_redirects=False
                    ).status_code
                    != expected
                ):
                    raise ValueError("observation pin/writer denial differs")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if retained(conn) != before:
            raise ValueError("observation certification changed retained records")
    return seal_evidence(
        manifest,
        "distribution-recovery-http" if recovery else "distribution-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "audience": audience,
            "profile_available": not recovery,
            "routes_checked": 3,
            "responses_checked": len(cases),
            "independent_source_observations_checked": not recovery,
            "certification_binding": binding,
            "source_rows_changed": 0,
            "records_appended": 0,
            "coverage_state": "unknown",
            "complete_event_history_certified": False,
            "adjusted_analytics_available": False,
            "consumer_application_rbac_certified": False,
        },
    )
