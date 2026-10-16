"""Private census HTTP proof with an independent SQL denominator; never a data-completeness certificate."""

import json
import time
from pathlib import Path
from uuid import uuid4
import jwt
import psycopg
import requests
from psycopg.rows import dict_row
from mfdataindia.enrichment import amc_coverage, amc_register, fact_review_store
from mfdataindia.enrichment.certification import check_http
from mfdataindia.enrichment.current_facts_certification import REVIEW_ORIGIN
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.rollout import seal_evidence


def independent_denominator(conn, report):
    rows = conn.execute(
        "SELECT a.amc_id,count(f.amfi_scheme_code)::int AS retained_schemes,count(f.amfi_scheme_code) FILTER(WHERE f.in_scope)::int AS in_scope,count(f.amfi_scheme_code) FILTER(WHERE f.in_scope AND NOT f.is_defunct)::int AS in_scope_non_defunct,count(f.amfi_scheme_code) FILTER(WHERE NOT f.in_scope)::int AS out_of_scope FROM mf.amcs a LEFT JOIN mf.funds f USING(amc_id) GROUP BY a.amc_id ORDER BY a.amc_id"
    ).fetchall()
    actual = {r["amc_id"]: {k: v for k, v in r.items() if k != "amc_id"} for r in rows}
    seen = {}
    for group in report["amcs"]:
        identifier = group["database_amc_id"]
        if identifier is None:
            if group["counts"]["retained_schemes"] != 0:
                raise ValueError("directory-only entry has fabricated database population")
            continue
        if identifier in seen or any(
            group["counts"].get(k) != v for k, v in actual[identifier].items()
        ):
            raise ValueError("census database population differs from independent SQL")
        seen[identifier] = True
    register = amc_register.load_register()
    members = {m.member_id: m for m in register.members}
    listed = [g for g in report["amcs"] if g["member_id"] is not None]
    if len(listed) != len(members) or {g["member_id"] for g in listed} != set(members):
        raise ValueError("census omitted or duplicated official directory members")
    for group in listed:
        member = members[group["member_id"]]
        if (
            group["amc_key"] != "amfi-" + member.member_id
            or group["directory_name"] != member.name
            or group["source_state"] != member.source_state
            or group["listed_factsheet_url"] != member.listed_factsheet_url
            or group["url_review_reasons"] != member.url_review_reasons
            or group["planned_wave"] != member.planned_wave
            or group["acquisition_authorized_by_register"]
        ):
            raise ValueError("directory entry differs from frozen source register")
    if (
        set(seen) != set(actual)
        or report["directory_entries"] != len(register.members)
        or report["register_hash"] != amc_register.register_hash(register)
    ):
        raise ValueError("census silently omitted a directory or database AMC")
    if report["retained_schemes"] != sum(r["retained_schemes"] for r in actual.values()) or report[
        "in_scope_schemes"
    ] != sum(r["in_scope"] for r in actual.values()):
        raise ValueError("scheme denominator differs")
    if report["all_amc_data_complete"] or report["new_acquisition_authority"]:
        raise ValueError("census overclaims data or acquisition readiness")
    return {
        "database_amcs": len(actual),
        "retained_schemes": report["retained_schemes"],
        "in_scope_schemes": report["in_scope_schemes"],
        "directory_entries": len(register.members),
        "directory_entries_with_link": report["directory_entries_with_link"],
    }


def certify(manifest, environment, *, recovery=False):
    dsn = pinned_dsn(manifest, environment, "mfdata_verified_reader")
    origin, audience = request_origins(environment)
    candidate = (
        "https://cert-" + manifest.rollout_id + "---" + REVIEW_ORIGIN.removeprefix("https://")
    )
    if audience != REVIEW_ORIGIN or origin not in (REVIEW_ORIGIN, candidate):
        raise ValueError("exact private census origin required")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        report = amc_coverage.summary(conn)
        denominator = independent_denominator(conn, report)
        before = fact_review_store.capture(conn)
        policy = amc_register.policy()
        cases = [
            ("/api/enrichment/v7/policy", {"policy_hash": fingerprint(policy), "document": policy}),
            ("/api/enrichment/v7/coverage", report),
        ]
        for member in ("3", "22", "17", "28", "91"):
            key = "amfi-" + member
            page = amc_coverage.schemes(
                conn, key, limit=1, expected_revision=report["coverage_revision"]
            )
            prefix = (
                "/api/enrichment/v7/amcs/"
                + key
                + "/schemes?limit=1&expected_revision="
                + report["coverage_revision"]
            )
            cases.append((prefix, page))
            if page["next_after_code"] and member == "3":
                after = page["next_after_code"]
                cases.append(
                    (
                        prefix + "&after_code=" + str(after),
                        amc_coverage.schemes(
                            conn,
                            key,
                            limit=1,
                            after_code=after,
                            expected_revision=report["coverage_revision"],
                        ),
                    )
                )
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("census trust differs")
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
        if not recovery:
            for route in ("/api/enrichment/v7/coverage", "/api/enrichment/v7/amcs/amfi-3/schemes"):
                if (
                    session.post(
                        origin + route, json={}, headers=headers, timeout=30, allow_redirects=False
                    ).status_code
                    != 405
                ):
                    raise ValueError("census unexpectedly permits mutation")
            conflict = session.get(
                origin + "/api/enrichment/v7/amcs/amfi-3/schemes?expected_revision=" + ("0" * 64),
                headers=headers,
                timeout=30,
                allow_redirects=False,
            )
            if conflict.status_code != 409:
                raise ValueError("stale page revision accepted")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if (
            amc_coverage.summary(conn)["coverage_revision"] != report["coverage_revision"]
            or fact_review_store.capture(conn) != before
        ):
            raise ValueError("census dependencies or review ledger changed during certification")
    return seal_evidence(
        manifest,
        "amc-coverage-recovery-http" if recovery else "amc-coverage-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "profile_available": not recovery,
            "responses_checked": len(cases),
            "independent_sql_denominator": denominator,
            "coverage_revision": report["coverage_revision"],
            "register_hash": report["register_hash"],
            "directory_sha256": report["directory_sha256"],
            "new_acquisition_authority": False,
            "source_rows_changed": 0,
            "human_events_created": 0,
            "all_amc_data_complete": False,
        },
    )
