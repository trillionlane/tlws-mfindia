"""Read-only real private HTTP proof against two independently inspected months."""

import json
import time
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg
import requests
from psycopg.rows import dict_row

from mfdataindia.enrichment import monthly_store as store
from mfdataindia.enrichment.certification import check_http
from mfdataindia.enrichment.current_facts_certification import REVIEW_ORIGIN
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.enrichment.monthly_deployment import projections, references
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.rollout import seal_evidence


def certify(manifest, environment, *, recovery=False):
    reviewed = references(manifest, environment)
    dsn = pinned_dsn(manifest, environment, "mfdata_verified_reader")
    origin, audience = request_origins(environment)
    candidate = (
        "https://cert-" + manifest.rollout_id + "---" + REVIEW_ORIGIN.removeprefix("https://")
    )
    if audience != REVIEW_ORIGIN or origin not in (REVIEW_ORIGIN, candidate):
        raise ValueError("exact monthly canonical/candidate private origin required")
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("monthly certification trust differs")
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
        before = store.capture(conn)
        cases = projections(conn, reviewed)
        for code in manifest.cohort:
            cases.append((f"/api/enrichment/v4/funds/{code}/facts", store.get(conn, code)))
    with requests.Session() as session:
        session.trust_env = False
        headers = {
            "X-Serverless-Authorization": "Bearer " + metadata_identity(session, audience),
            "Authorization": "Bearer " + assertion,
        }
        check_http(session, origin, headers, cases, recovery=recovery)
        if not recovery:
            for code in manifest.cohort:
                response = session.post(
                    origin + f"/api/enrichment/v4/funds/{code}/facts",
                    json={},
                    headers=headers,
                    timeout=30,
                    allow_redirects=False,
                )
                if response.status_code != 405:
                    raise ValueError("unexpected monthly HTTP writer")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if before != store.capture(conn):
            raise ValueError("monthly HTTP certification mutated retained state")
    return seal_evidence(
        manifest,
        "monthly-facts-recovery-http" if recovery else "monthly-facts-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "audience": audience,
            "routes_checked": 2,
            "responses_checked": len(cases),
            "both_reporting_periods_checked": True,
            "independent_original_source_values_units_status_checked": True,
            "profile_available": not recovery,
            "records_appended": 0,
            "source_rows_changed": 0,
            "verification_status": "needs_verification",
            "all_amc_coverage": False,
            "consumer_UI_certified": False,
        },
    )
