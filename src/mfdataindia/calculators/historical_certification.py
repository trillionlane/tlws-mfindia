"""Real consumer-owned private HTTP; no source or personal scenario persistence."""

import calendar
import json
import time
from datetime import date
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg
import requests
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from mfdataindia.calculators import historical, historical_store, historical_schemas
from mfdataindia.review import store as review
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.publication_operations import capture_review
from mfdataindia.review.rollout import CONNECTION, seal_evidence


def scenarios(snapshot):
    if not snapshot["observations"]:
        end = date(2026, 10, 13)  # nonpersonal unavailable control; no price inference
    else:
        end = date.fromisoformat(snapshot["observations"][-1]["date"])
    start = date(
        end.year - 1, end.month, min(end.day, calendar.monthrange(end.year - 1, end.month)[1])
    )
    common = {
        "code": snapshot["code"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    return [
        {"kind": "lumpsum", "initial_paise": 1000000, **common},
        {"kind": "sip", "monthly_paise": 100000, "day_of_month": 15, **common},
    ]


def certify(manifest, environment, *, recovery=False):
    parsed = conninfo_to_dict(environment["MFDATAINDIA_DSN"])
    if (
        parsed.get("host") != "/cloudsql/" + CONNECTION
        or parsed.get("dbname") != "mfdataindia"
        or parsed.get("user") != "mfdata_verified_reader"
        or not parsed.get("password")
        or parsed.get("hostaddr")
    ):
        raise ValueError("historical certifier requires pinned DEV reader")
    origin, audience = request_origins(environment)
    trust = json.loads(Path("/trust/config.json").read_text())
    if (
        environment["DEPLOYMENT_GIT_SHA"] != manifest.git_sha
        or fingerprint(trust) != manifest.trust_hash
    ):
        raise ValueError("historical certifier source/trust differs")
    verifier = AssertionVerifier(**{**trust, "allowed_scopes": frozenset(trust["allowed_scopes"])})
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
        Path("/signer/key.pem").read_text(),
        algorithm="EdDSA",
    )

    def retained():
        with psycopg.connect(environment["MFDATAINDIA_DSN"], row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            verify_runtime(conn, "api")
            historical_store.installed_policy(conn)
            return capture_review(conn, include_sequence=False)

    before = retained()
    cases = []
    with psycopg.connect(environment["MFDATAINDIA_DSN"], row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        for code in manifest.cohort:
            source = review.capture(conn, code)
            for body in scenarios(source):
                expected = historical_schemas.Response.model_validate(
                    historical.derive(body, source, historical.policy())
                ).model_dump(mode="json")
                cases.append((body, expected))
    if not recovery and not all(
        any(
            b["kind"] == kind and r["available"] and r["summary"]["xirr_pct"] is not None
            for b, r in cases
        )
        for kind in ("lumpsum", "sip")
    ):
        raise ValueError("real covered Growth SIP and lumpsum samples with XIRR required")
    if not recovery and not any(r["unavailable_reason"] == "unsupported_option" for _, r in cases):
        raise ValueError("real IDCW unavailable control required")
    checked = []
    with requests.Session() as session:
        session.trust_env = False
        iam = metadata_identity(session, audience)
        headers = {
            "X-Serverless-Authorization": "Bearer " + iam,
            "Authorization": "Bearer " + token,
        }
        url = origin + "/api/calculators/v1/backtests"
        denied = session.post(
            url,
            json=cases[0][0],
            headers={"X-Serverless-Authorization": "Bearer " + iam},
            timeout=30,
            allow_redirects=False,
        )
        if denied.status_code != (404 if recovery else 401):
            raise ValueError("historical assertion/profile denial differs")
        for body, expected in cases:
            for _ in range(2):
                response = session.post(
                    url, json=body, headers=headers, timeout=30, allow_redirects=False
                )
                if (
                    response.status_code != (404 if recovery else 200)
                    or response.headers.get("Cache-Control") != "no-store"
                    or not recovery
                    and response.json() != expected
                ):
                    raise ValueError(
                        "historical private replay differs from coherent policy/source"
                    )
            if not recovery:
                checked.append(
                    {
                        "code": body["code"],
                        "kind": body["kind"],
                        "result_id": expected["result_id"],
                        "input_hash": expected["input_hash"],
                        "dataset_version": expected["dataset_version"],
                        "available": expected["available"],
                        "unavailable_reason": expected["unavailable_reason"],
                        "purchases": len(expected["purchases"]),
                        "xirr_pct": expected["summary"]["xirr_pct"]
                        if expected["summary"]
                        else None,
                    }
                )
        if not recovery:
            for invalid in (
                {**cases[0][0], "initial_paise": True},
                {**cases[0][0], "customer_name": "fixture-rejected"},
                {**cases[0][0], "start_date": "2023-02-30"},
            ):
                if (
                    session.post(
                        url, json=invalid, headers=headers, timeout=30, allow_redirects=False
                    ).status_code
                    != 422
                ):
                    raise ValueError("historical invalid/personal input denial failed")
    if retained() != before:
        raise ValueError("historical scenario persisted or review rows changed")
    return seal_evidence(
        manifest,
        "historical-recovery-http" if recovery else "historical-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "audience": audience,
            "policy_hash": fingerprint(historical.policy()),
            "projection_available": not recovery,
            "endpoint": "POST /api/calculators/v1/backtests",
            "cases": checked,
            "source_writes": 0,
            "records_appended": 0,
            "personal_scenarios_persisted": 0,
            "consumer_application_rbac_certified": False,
            "data_correctness_or_execution_certified": False,
        },
    )
