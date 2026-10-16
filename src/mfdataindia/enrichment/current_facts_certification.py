"""Real private HTTP checks of metadata AND the existing core fund API."""

import json
import time
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg
import requests
from psycopg.rows import dict_row

from mfdataindia.enrichment import current_facts_store as store, facts_store
from mfdataindia.enrichment.certification import check_http
from mfdataindia.enrichment.current_facts_deployment import projections, reference
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.rollout import seal_evidence

CORE_ORIGIN = "https://tlws-mf-data-dev-kazxufb4wa-el.a.run.app"
REVIEW_ORIGIN = "https://tlws-mf-review-dev-kazxufb4wa-el.a.run.app"


def core_http(session, expected):
    # Fixed provider origin, no caller-controlled URL/audience or secret output.
    token = metadata_identity(session, CORE_ORIGIN)
    core_headers = {"Authorization": "Bearer " + token}
    checks = 0
    for code, values in expected.items():
        detail = session.get(
            CORE_ORIGIN + f"/api/funds/{code}",
            headers=core_headers,
            timeout=30,
            allow_redirects=False,
        )
        if detail.status_code != 200 or detail.headers.get("Cache-Control") != "no-store":
            raise ValueError("existing core fund detail HTTP unavailable")
        actual = detail.json().get("facts") or {}
        if any(actual.get(key) != value for key, value in values.items()):
            raise ValueError("existing core detail does not serve selected official facts")
        batch = session.get(
            CORE_ORIGIN + "/api/funds/batch",
            params={"ids": code},
            headers=core_headers,
            timeout=30,
            allow_redirects=False,
        )
        if batch.status_code != 200 or batch.headers.get("Cache-Control") != "no-store":
            raise ValueError("existing core batch HTTP unavailable")
        funds = batch.json().get("funds", [])
        if (
            len(funds) != 1
            or str(funds[0].get("amfi_scheme_code")) != code
            or any(
                funds[0].get(key) != value for key, value in values.items() if key != "benchmark"
            )
        ):
            raise ValueError("existing core batch disagrees with selected official facts")
        checks += 2
    return checks


def certify(manifest, environment, *, active: bool, recovery=False):
    reviewed = reference(manifest, environment)
    dsn = pinned_dsn(manifest, environment, "mfdata_verified_reader")
    origin, audience = request_origins(environment)
    candidate = (
        "https://cert-" + manifest.rollout_id + "---" + REVIEW_ORIGIN.removeprefix("https://")
    )
    if audience != REVIEW_ORIGIN or origin not in (REVIEW_ORIGIN, candidate):
        raise ValueError(
            "current-facts certification requires the exact provider origin or this rollout candidate"
        )
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("current-facts certification trust differs")
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
        verify_runtime(conn, "api")
        store.verify_acl(conn)
        before = (store.capture(conn), facts_store.capture(conn))
        cases, core = projections(conn, reviewed, active=active, core_queries=False)
    with requests.Session() as session:
        session.trust_env = False
        headers = {
            "X-Serverless-Authorization": "Bearer " + metadata_identity(session, audience),
            "Authorization": "Bearer " + assertion,
        }
        check_http(session, origin, headers, cases, recovery=recovery)
        checked = core_http(session, core)
        if not recovery:
            for code in reviewed:
                denied = session.post(
                    origin + f"/api/enrichment/v3/funds/{code}/facts",
                    json={},
                    headers=headers,
                    timeout=30,
                    allow_redirects=False,
                )
                if denied.status_code != 405:
                    raise ValueError("current facts HTTP writes unexpectedly enabled")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if before != (store.capture(conn), facts_store.capture(conn)):
            raise ValueError("current-facts certification changed retained state")
    return seal_evidence(
        manifest,
        "current-facts-recovery-http" if recovery else "current-facts-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "audience": audience,
            "core_origin": CORE_ORIGIN,
            "projection_active": active,
            "metadata_profile_available": not recovery,
            "metadata_routes_checked": 2,
            "metadata_responses_checked": len(cases),
            "existing_core_responses_checked": checked,
            "independent_original_source_reference_checked": True,
            "source_rows_changed": 0,
            "records_appended": 0,
            "verification_status": "needs_verification",
            "all_amc_coverage": False,
            "consumer_ui_certified": False,
        },
    )
