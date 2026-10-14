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
            "monthly_paise": 100000,
            "timing": "end",
            **{**common, "annual_return_pct": "12", "rate_mode": "nominal_annual_monthly"},
        },
    ]


def certify(manifest, environment):
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
            return capture_review(conn)

    before = retained()
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
        if denied.status_code != 401:
            raise ValueError("calculator assertion denial not observed")
        for body in scenarios():
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
            if post(body).json() != expected:
                raise ValueError("calculator replay differs")
            result_ids.append(expected["result_id"])
        for invalid in (
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
        ):
            if post(invalid).status_code != 422:
                raise ValueError("calculator invalid/PII/infeasible denial not observed")
    if retained() != before:
        raise ValueError("calculator requests persisted or changed review data")
    return seal_evidence(
        manifest,
        "forward-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "audience": audience,
            "policy_hash": fingerprint(forward.policy()),
            "endpoint": "POST /api/calculators/v1/projections",
            "scenarios": len(result_ids),
            "result_ids": result_ids,
            "source_writes": 0,
            "records_appended": 0,
            "personal_scenarios_persisted": 0,
            "assumed_projection_not_forecast": True,
            "consumer_application_rbac_certified": False,
        },
    )
