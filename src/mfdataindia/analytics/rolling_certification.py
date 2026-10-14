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

from mfdataindia.analytics import rolling, rolling_schemas as schemas, rolling_store as store
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
        raise ValueError("rolling certification reader must target the pinned DEV database")
    origin, audience = request_origins(environment)
    trust = json.loads(Path("/trust/config.json").read_text())
    if (
        environment["DEPLOYMENT_GIT_SHA"] != manifest.git_sha
        or fingerprint(trust) != manifest.trust_hash
    ):
        raise ValueError("rolling certifier source/trust differs")
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
                    raise ValueError("rolling certification needs dedicated reader")
                snapshot = review.capture(conn, code)
                expected = rolling.derive(snapshot, rolling.policy())
                retained = store.get_rolling(conn, code)
                public = schemas.PagedResponse.model_validate(store.page(retained)).model_dump(
                    mode="json"
                )
            path = origin + "/api/research/v1/funds/" + str(code) + "/rolling-returns"
            denied = session.get(
                path,
                headers={"X-Serverless-Authorization": "Bearer " + iam},
                timeout=30,
                allow_redirects=False,
            )
            if denied.status_code != 401:
                raise ValueError("rolling assertion denial not observed")
            current = session.get(path, headers=reader, timeout=30, allow_redirects=False)
            if (
                current.status_code != 200
                or current.headers.get("Cache-Control") != "no-store"
                or current.json() != public
            ):
                raise ValueError("rolling public projection differs from coherent database read")
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
                raise ValueError("rolling admin audit HTTP/cache failed")
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
                    "rolling served artifact differs from deterministic current derivation"
                )
            pages_checked = 0
            for years in (1, 3, 5):
                cursor = None
                combined = []
                while True:
                    local = schemas.PagedResponse.model_validate(
                        store.page(retained, years=years, limit=7, cursor=cursor)
                    ).model_dump(mode="json")
                    query = {"years": years, "limit": 7}
                    if cursor:
                        query["cursor"] = cursor
                    paged = session.get(
                        path, params=query, headers=reader, timeout=30, allow_redirects=False
                    )
                    if (
                        paged.status_code != 200
                        or paged.headers.get("Cache-Control") != "no-store"
                        or paged.json() != local
                    ):
                        raise ValueError(
                            "rolling bounded page differs from retained certified projection"
                        )
                    combined.extend(local["windows"])
                    pages_checked += 1
                    cursor = local["pagination"]["next_cursor"]
                    if not cursor:
                        break
                    if pages_checked > 600:
                        raise ValueError("rolling page traversal exceeded policy bounds")
                visible = retained["artifact"]["rolling"] if retained["artifact"] else None
                wanted = (
                    next(t["windows"] for t in visible["terms"] if t["years"] == years)
                    if visible
                    else []
                )
                if combined != wanted:
                    raise ValueError("rolling page traversal omitted/duplicated/reordered windows")
            terms = expected["rolling"]["terms"]
            periods = [window for term in terms for window in term["windows"]]
            results.append(
                {
                    "code": code,
                    "result_id": expected["result_id"],
                    "input_hash": expected["input_hash"],
                    "available_periods": sum(
                        p["annualized_return_pct"] is not None for p in periods
                    ),
                    "eligible_at_capture": expected["eligible_at_capture"],
                    "option": snapshot["identity"]["option_type"],
                    "signals": snapshot["context"]["nav_quality"]["signals"],
                    "assessment_status": snapshot["context"]["nav_quality"]["assessment_status"],
                    "unavailable_periods": sum(p["annualized_return_pct"] is None for p in periods)
                    + sum(t["summary"]["unavailable_reason"] is not None for t in terms),
                    "family_unavailable": expected["rolling"]["unavailable_reason"] is not None,
                    "terms": [
                        {
                            "years": t["years"],
                            "scheduled_windows": t["summary"]["scheduled_windows"],
                            "available_windows": t["summary"]["available_windows"],
                            "summary_reason": t["summary"]["unavailable_reason"],
                        }
                        for t in terms
                    ],
                    "normal_state": retained["state"],
                    "pages_checked": pages_checked,
                }
            )
    if not any(
        r["option"] == "GROWTH" and r["eligible_at_capture"] and r["available_periods"]
        for r in results
    ):
        raise ValueError("real eligible Growth rolling periods not certified")
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
        raise ValueError("rolling cohort lacks declared real coverage controls")
    return seal_evidence(
        manifest,
        "rolling-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "audience": audience,
            "policy_hash": fingerprint(rolling.policy()),
            "endpoint": "GET /api/research/v1/funds/{code}/rolling-returns",
            "cohort": results,
            "source_writes": 0,
            "records_appended": 0,
            "human_review_or_consumer_rbac_certified": False,
            "coverage_observed": sorted(coverage),
        },
    )
