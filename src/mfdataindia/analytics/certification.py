"""Consumer-owned private HTTP conformance; signer never fetched by provider."""

import json
import time
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg
import requests
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from mfdataindia.analytics import calendar, schemas, store
from mfdataindia.review import store as review
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.rollout import CONNECTION, seal_evidence


def certify(manifest, environment):
    parsed = conninfo_to_dict(environment["MFDATAINDIA_DSN"])
    if (
        parsed.get("host") != "/cloudsql/" + CONNECTION
        or parsed.get("dbname") != "mfdataindia"
        or parsed.get("user") != "mfdata_verified_reader"
        or not parsed.get("password")
        or parsed.get("hostaddr")
    ):
        raise ValueError("calendar certification reader must target the pinned DEV database")
    origin, audience = request_origins(environment)
    trust = json.loads(Path("/trust/config.json").read_text())
    if (
        environment["DEPLOYMENT_GIT_SHA"] != manifest.git_sha
        or fingerprint(trust) != manifest.trust_hash
    ):
        raise ValueError("calendar certifier source/trust differs")
    verifier = AssertionVerifier(**{**trust, "allowed_scopes": frozenset(trust["allowed_scopes"])})
    private = Path("/signer/key.pem").read_text()
    now = int(time.time())

    def assertion(scopes):
        return jwt.encode(
            {
                "iss": verifier.issuer,
                "aud": verifier.audience,
                "caller": verifier.caller,
                "sub": manifest.actor,
                "scope": scopes,
                "iat": now,
                "nbf": now,
                "exp": now + 240,
                "jti": uuid4().hex,
            },
            private,
            algorithm="EdDSA",
        )

    with requests.Session() as session:
        session.trust_env = False
        iam = metadata_identity(session, audience)
        admin = {
            "X-Serverless-Authorization": "Bearer " + iam,
            "Authorization": "Bearer " + assertion(["review:admin"]),
        }
        reader = {**admin, "Authorization": "Bearer " + assertion(["review:read"])}
        results = []
        for code in manifest.cohort:
            with psycopg.connect(environment["MFDATAINDIA_DSN"], row_factory=dict_row) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                verify_runtime(conn, "api")
                store.verify_acl(conn)
                if (
                    conn.execute("SELECT current_user AS role").fetchone()["role"]
                    != "mfdata_verified_reader"
                ):
                    raise ValueError("calendar certification needs dedicated reader")
                snapshot = review.capture(conn, code)
                expected = calendar.derive(snapshot, calendar.policy())
                public = schemas.Response.model_validate(store.get_calendar(conn, code)).model_dump(
                    mode="json"
                )
            path = origin + "/api/research/v1/funds/" + str(code) + "/calendar-returns"
            denied = session.get(
                path,
                headers={"X-Serverless-Authorization": "Bearer " + iam},
                timeout=30,
                allow_redirects=False,
            )
            if denied.status_code != 401:
                raise ValueError("calendar assertion denial not observed")
            current = session.get(path, headers=reader, timeout=30, allow_redirects=False)
            if (
                current.status_code != 200
                or current.headers.get("Cache-Control") != "no-store"
                or current.json() != public
            ):
                raise ValueError("calendar public projection differs from coherent database read")
            audit_path = path + "?result_id=" + expected["result_id"]
            if (
                session.get(
                    audit_path, headers=reader, timeout=30, allow_redirects=False
                ).status_code
                != 403
            ):
                raise ValueError("read-only assertion admitted retained admin audit")
            audited = session.get(audit_path, headers=admin, timeout=30, allow_redirects=False)
            if audited.status_code != 200 or audited.headers.get("Cache-Control") != "no-store":
                raise ValueError("calendar admin audit HTTP/cache failed")
            raw = audited.json()
            parsed = schemas.AuditResponse.model_validate(raw)
            expected_audit = schemas.AuditResponse.model_validate(
                {
                    "audit_only": True,
                    "result": expected,
                    "current_input_hash": expected["input_hash"],
                    "current_context": snapshot["context"],
                    "input_status": "current",
                }
            ).model_dump(mode="json")
            if (
                raw != expected_audit
                or parsed.input_status != "current"
                or not raw["audit_only"]
                or "signals" not in raw["current_context"]["nav_quality"]
            ):
                raise ValueError(
                    "calendar served artifact differs from deterministic current derivation"
                )
            periods = [
                *expected["calendar"]["calendar_years"],
                *expected["calendar"]["months"],
                *(
                    [expected["calendar"]["year_to_date"]]
                    if expected["calendar"]["year_to_date"]
                    else []
                ),
            ]
            results.append(
                {
                    "code": code,
                    "result_id": expected["result_id"],
                    "input_hash": expected["input_hash"],
                    "available_periods": sum(p["return_pct"] is not None for p in periods),
                    "eligible_at_capture": expected["eligible_at_capture"],
                    "option": snapshot["identity"]["option_type"],
                    "signals": snapshot["context"]["nav_quality"]["signals"],
                    "assessment_status": snapshot["context"]["nav_quality"]["assessment_status"],
                    "unavailable_periods": sum(p["return_pct"] is None for p in periods),
                    "family_unavailable": expected["calendar"]["unavailable_reason"] is not None,
                }
            )
    if not any(
        r["option"] == "GROWTH" and r["eligible_at_capture"] and r["available_periods"]
        for r in results
    ):
        raise ValueError("real eligible Growth calendar periods not certified")
    if not any(
        r["option"] != "GROWTH" and not r["eligible_at_capture"] and r["available_periods"] == 0
        for r in results
    ):
        raise ValueError("unsupported-option control not certified")
    if "explicit_null_signals" in manifest.required_coverage and not any(
        r["signals"] is None and r["assessment_status"] == "not_assessed" for r in results
    ):
        raise ValueError("explicit unassessed null control not certified")
    coverage = set()
    for row in results:
        if row["option"] == "GROWTH" and row["eligible_at_capture"] and row["available_periods"]:
            coverage.add("growth_eligible")
        if (
            row["option"] != "GROWTH"
            and not row["eligible_at_capture"]
            and not row["available_periods"]
        ):
            coverage.add("unsupported_option")
        if row["signals"] is None and row["assessment_status"] == "not_assessed":
            coverage.add("explicit_null_signals")
        if row["signals"]:
            coverage.add("quality_signalled")
        if row["unavailable_periods"] or row["family_unavailable"]:
            coverage.add("unavailable_metric")
    if not set(manifest.required_coverage) <= coverage:
        raise ValueError("calendar cohort lacks declared real coverage controls")
    return seal_evidence(
        manifest,
        "calendar-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "audience": audience,
            "policy_hash": fingerprint(calendar.policy()),
            "endpoint": "GET /api/research/v1/funds/{code}/calendar-returns",
            "cohort": results,
            "source_writes": 0,
            "records_appended": 0,
            "human_review_or_consumer_rbac_certified": False,
            "coverage_observed": sorted(coverage),
        },
    )
