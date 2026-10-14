"""Consumer-owned signed private HTTP checks; no financial scenarios stored."""

import json
import time
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg
import requests
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from mfdataindia.calculators import forward, schemas, store
from mfdataindia.analytics import schemas as calendar_schemas, store as calendar_store
from mfdataindia.analytics import rolling_schemas, rolling_store
from mfdataindia.review import schemas as review_schemas, store as review_store, publication
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.publication_operations import capture_review
from mfdataindia.review.rollout import CONNECTION, seal_evidence


def scenarios():
    common = {"months": 24, "annual_return_pct": "10", "rate_mode": "effective_annual"}
    return [
        {"kind": "lumpsum", "initial_paise": 1000000, **common},
        {"kind": "sip", "monthly_paise": 100000, "timing": "beginning", **common},
        {
            "kind": "stepup_sip",
            "monthly_paise": 100000,
            "timing": "end",
            "step": {"kind": "annual_percent", "value_pct": "10"},
            **common,
        },
        {
            "kind": "goal",
            "cost_today_paise": 2500000000,
            "annual_inflation_pct": "6",
            "timing": "end",
            **common,
        },
        {
            "kind": "goal",
            "cost_today_paise": 2500000000,
            "annual_inflation_pct": "6",
            "timing": "beginning",
            "step": {"kind": "annual_fixed", "value_paise": 100000},
            **common,
        },
        {
            "kind": "swp",
            "initial_paise": 2500,
            "withdrawal_paise": 1000,
            "timing": "end",
            **{**common, "annual_return_pct": "0"},
        },
        {"kind": "lumpsum", "initial_paise": 1000000, **{**common, "annual_return_pct": "-20"}},
        {
            "kind": "sip",
            "monthly_paise": 9000000,
            "timing": "end",
            **{**common, "annual_return_pct": "0.1234", "rate_mode": "nominal_annual_monthly"},
        },
    ]


def compatibility_reads(conn, manifest):
    """Exact read projections, not recertification of older derived artifacts.

    A scheduled refresh can legitimately make retained artifacts stale. Never
    mint another canary event, regenerate results, or relabel that state current
    just to certify a source-independent calculator deployment.
    """
    def wire(model, value):
        return model.model_validate(value).model_dump(mode="json")

    row = conn.execute(
        "SELECT policy_hash,policy_id,policy_version,document FROM mf_review.policies WHERE policy_hash=%s",
        (manifest.policy_hash,),
    ).fetchone()
    cases = [
        ("/api/review/v1/policies/" + manifest.policy_hash, wire(review_schemas.PolicyResponse, row)),
        ("/api/review/v1/funds?limit=1", wire(review_schemas.QueueResponse,
         review_store.queue(conn, after_code=0, limit=1, status=None, signal=None))),
    ]
    for code in manifest.cohort:
        base = "/api/review/v1/funds/" + str(code)
        cases.extend([
            (base, wire(review_schemas.ReviewResponse, review_store.get_review(conn, code))),
            (base + "/analytics", wire(review_schemas.ResultResponse, review_store.get_result(conn, code))),
            (base + "/publication", wire(review_schemas.PublicationResponse, publication.get_publication(conn, code))),
            ("/api/research/v1/funds/" + str(code) + "/calendar-returns",
             wire(calendar_schemas.Response, calendar_store.get_calendar(conn, code))),
            ("/api/research/v1/funds/" + str(code) + "/rolling-returns",
             wire(rolling_schemas.PagedResponse, rolling_store.page(rolling_store.get_rolling(conn, code)))),
        ])
    return cases


def certify(manifest, environment, *, recovery=False):
    parsed = conninfo_to_dict(environment["MFDATAINDIA_DSN"])
    if (
        parsed.get("host") != "/cloudsql/" + CONNECTION
        or parsed.get("dbname") != "mfdataindia"
        or parsed.get("user") != "mfdata_verified_reader"
        or not parsed.get("password")
        or parsed.get("hostaddr")
    ):
        raise ValueError("calculator certifier requires fixed DEV reader")
    origin, audience = request_origins(environment)
    trust = json.loads(Path("/trust/config.json").read_text())
    if (
        environment["DEPLOYMENT_GIT_SHA"] != manifest.git_sha
        or fingerprint(trust) != manifest.trust_hash
    ):
        raise ValueError("calculator certifier source/trust differs")
    verifier = AssertionVerifier(**{**trust, "allowed_scopes": frozenset(trust["allowed_scopes"])})
    key = Path("/signer/key.pem").read_text()
    now = int(time.time())
    token = jwt.encode(
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
        key,
        algorithm="EdDSA",
    )

    def retained():
        with psycopg.connect(environment["MFDATAINDIA_DSN"], row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            verify_runtime(conn, "api")
            store.installed_policy(conn)
            return capture_review(conn, include_sequence=False)

    before = retained()
    with psycopg.connect(environment["MFDATAINDIA_DSN"], row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        compatible = compatibility_reads(conn, manifest)
    result_ids = []
    with requests.Session() as session:
        session.trust_env = False
        iam = metadata_identity(session, audience)
        headers = {
            "X-Serverless-Authorization": "Bearer " + iam,
            "Authorization": "Bearer " + token,
        }
        path = origin + "/api/calculators/v1/projections"

        def post(body):
            return session.post(path, json=body, headers=headers, timeout=30, allow_redirects=False)

        first = scenarios()[0]
        denied = session.post(
            path,
            json=first,
            headers={"X-Serverless-Authorization": "Bearer " + iam},
            timeout=30,
            allow_redirects=False,
        )
        if denied.status_code != (404 if recovery else 401):
            raise ValueError("calculator assertion denial not observed")
        if recovery and post(first).status_code != 404:
            raise ValueError("prior nine-route recovery still exposes calculators")
        for body in ([] if recovery else scenarios()):
            expected = schemas.Response.model_validate(
                forward.derive(body, forward.policy())
            ).model_dump(mode="json")
            response = post(body)
            if (
                response.status_code != 200
                or response.headers.get("Cache-Control") != "no-store"
                or response.json() != expected
            ):
                raise ValueError("calculator private response differs from deterministic policy")
            replay = post(body)
            if (replay.status_code != 200 or replay.headers.get("Cache-Control") != "no-store"
                    or replay.json() != expected):
                raise ValueError("calculator replay differs")
            result_ids.append(expected["result_id"])
        invalid_cases = (
            {**first, "initial_paise": True},
            {**first, "annual_return_pct": "NaN"},
            {**first, "customer_name": "fixture-must-be-rejected"},
            {
                "kind": "goal",
                "months": 1,
                "annual_return_pct": "0",
                "rate_mode": "effective_annual",
                "cost_today_paise": 100000000000000,
                "annual_inflation_pct": "50",
                "timing": "end",
            },
        )
        for invalid in ([] if recovery else invalid_cases):
            if post(invalid).status_code != 422:
                raise ValueError("calculator invalid/PII/infeasible denial not observed")
        for route, expected in compatible:
            response = session.get(origin + route, headers=headers, timeout=30, allow_redirects=False)
            if (response.status_code != 200 or response.headers.get("Cache-Control") != "no-store"
                    or response.json() != expected):
                raise ValueError("prior private read projection changed during calculator rollout")
        detail = compatible[2][1]
        result = compatible[3][1]["result"]
        if detail["version"] == 9223372036854775807 or detail["current_input_hash"] == "0" * 64:
            raise ValueError("cannot construct a guaranteed non-applicable denial probe")
        denied_body = {
            "expected_version": 9223372036854775807, "expected_input_hash": "0" * 64,
            "result_id": result["result_id"], "reason": "read-scope denial; no event permitted",
            "evidence": [], "status": "in_review", "disposition": "not_determined",
        }
        for suffix in ("reviews", "enrichment-proposals"):
            body = denied_body if suffix == "reviews" else {
                **{k: v for k, v in denied_body.items() if k not in ("status", "disposition")},
                "fields": [{"field": "scheme_name", "proposed_value": detail["identity"]["scheme_name"]}],
            }
            response = session.post(origin + "/api/review/v1/funds/" + str(manifest.cohort[0]) + "/" + suffix,
                json=body, headers={**headers, "Idempotency-Key": "forward-denial-" + suffix},
                timeout=30, allow_redirects=False)
            if response.status_code != 403 or response.headers.get("Cache-Control") != "no-store":
                raise ValueError("calculator read assertion admitted an existing admin mutation")
    if retained() != before:
        raise ValueError("calculator requests persisted or changed review data")
    return seal_evidence(
        manifest,
        "forward-recovery-http" if recovery else "forward-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "audience": audience,
            "policy_hash": fingerprint(forward.policy()),
            "endpoint": "POST /api/calculators/v1/projections",
            "projection_available": not recovery,
            "scenarios": len(result_ids),
            "result_ids": result_ids,
            "source_writes": 0,
            "records_appended": 0,
            "personal_scenarios_persisted": 0,
            "assumed_projection_not_forecast": True,
            "prior_routes_compatible": 9,
            "prior_read_projections_checked": len(compatible),
            "prior_analytics_recertified_current": False,
            "prior_canary_events_appended": 0,
            "consumer_application_rbac_certified": False,
        },
    )
