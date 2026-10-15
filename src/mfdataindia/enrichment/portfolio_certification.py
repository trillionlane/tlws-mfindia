"""Real private portfolio HTTP, source oracle, full pagination and no-write checks."""

import json
import time
from pathlib import Path
from uuid import uuid4
import jwt
import psycopg
import requests
from psycopg.rows import dict_row
from mfdataindia.enrichment import (
    portfolios as engine,
    portfolio_schemas as schemas,
    portfolio_store as store,
)
from mfdataindia.enrichment.certification import check_http, metadata_state
from mfdataindia.enrichment.portfolio_deployment import selection
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.enrichment.facts_store import capture as capture_facts
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.publication_operations import capture_review
from mfdataindia.review.rollout import seal_evidence


def projections(conn, approved, oracle):
    if set(oracle) != {str(t.code) for t in approved.targets}:
        raise ValueError("per-fund independently reviewed source oracle required")
    cases = [
        (
            "/api/portfolios/v1/policy",
            {"policy_hash": fingerprint(engine.policy()), "document": store.installed_policy(conn)},
        )
    ]
    for target in approved.targets:
        response, snapshot = store.get(conn, target.code)
        if (
            response["state"] != "available"
            or snapshot is None
            or snapshot["source_id"] != approved.source_id
            or snapshot["identity_source_id"] != approved.identity_source_id
            or snapshot["sheet"] != target.sheet
            or snapshot["identity_extraction"]["page"] != target.identity_page
        ):
            raise ValueError("accepted portfolio source/identity differs")
        actual = {
            "holdings_count": len(snapshot["holdings"]),
            "grand_total_inr_lakh": snapshot["grand_total_inr_lakh"],
            "holdings_digest": fingerprint(
                [
                    {k: h[k] for k in ("row", "name", "identifier", "market_value_inr_lakh")}
                    for h in snapshot["holdings"]
                ]
            ),
            "derivative_count": len(snapshot["derivative_positions"]),
            "source_content_hash": snapshot["content_hash"],
            "identity_content_hash": snapshot["identity_content_hash"],
            "disclosure_as_of": snapshot["disclosure_as_of"],
        }
        if actual != oracle[str(target.code)] or not snapshot["statement_complete"]:
            raise ValueError("independent complete source-to-portfolio oracle differs")
        route = "/api/portfolios/v1/funds/" + str(target.code)
        cases.extend(
            [
                (
                    route + "/portfolio",
                    schemas.Response.model_validate(response).model_dump(mode="json"),
                ),
                (route + "/portfolio?snapshot_id=" + snapshot["snapshot_id"], response),
                (route + "/snapshots?limit=1", store.snapshots(conn, target.code, limit=1)),
            ]
        )
        after = 0
        observed = []
        while True:
            page = store.holdings(conn, target.code, snapshot["snapshot_id"], after=after, limit=50)
            cases.append(
                (
                    route
                    + "/snapshots/"
                    + snapshot["snapshot_id"]
                    + "/holdings?limit=50&after_row="
                    + str(after),
                    page,
                )
            )
            observed.extend(page["items"])
            if page["next_after_row"] is None:
                break
            if page["next_after_row"] <= after or len(observed) > 1000:
                raise ValueError("portfolio pagination did not advance")
            after = page["next_after_row"]
        if observed != snapshot["holdings"]:
            raise ValueError("portfolio pagination lost or duplicated rows")
    return cases


def retained(conn):
    return {
        "review": capture_review(conn, include_sequence=False),
        "evidence": metadata_state(conn),
        "facts": capture_facts(conn),
        "sources": conn.execute(
            "SELECT source_id,content_hash,intake FROM mf_portfolios.sources ORDER BY source_id"
        ).fetchall(),
        "snapshots": conn.execute(
            "SELECT snapshot_id,encode(sha256(convert_to(document::text,'UTF8')),'hex') AS hash FROM mf_portfolios.snapshots ORDER BY snapshot_id"
        ).fetchall(),
    }


def certify(manifest, environment, *, recovery=False):
    approved = selection(manifest, environment)
    oracle = json.loads(environment["PORTFOLIO_EXPECTATIONS"])
    binding = fingerprint({"selection": approved.model_dump(mode="json"), "oracle": oracle})
    if binding != environment["PORTFOLIO_CERTIFICATION_HASH"]:
        raise ValueError("portfolio certificate approval differs")
    dsn = pinned_dsn(manifest, environment, store.evidence.ROLES[1])
    origin, audience = request_origins(environment)
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("portfolio certifier trust differs")
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
        cases = projections(conn, approved, oracle)
    with requests.Session() as session:
        session.trust_env = False
        iam = metadata_identity(session, audience)
        headers = {
            "X-Serverless-Authorization": "Bearer " + iam,
            "Authorization": "Bearer " + assertion,
        }
        check_http(session, origin, headers, cases, recovery=recovery)
        if not recovery:
            for target in approved.targets:
                response = session.post(
                    origin + "/api/portfolios/v1/funds/" + str(target.code) + "/portfolio",
                    headers=headers,
                    json={},
                    timeout=30,
                    allow_redirects=False,
                )
                if response.status_code != 405:
                    raise ValueError("unexpected HTTP portfolio writer")
            for path in (
                "/api/portfolios/v1/sources",
                "/api/portfolios/v1/sources/" + approved.source_id + "/raw",
            ):
                if (
                    session.get(
                        origin + path, headers=headers, timeout=30, allow_redirects=False
                    ).status_code
                    != 404
                ):
                    raise ValueError("unexpected raw portfolio source endpoint")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if retained(conn) != before:
            raise ValueError("portfolio certification changed retained records")
    return seal_evidence(
        manifest,
        "portfolio-recovery-http" if recovery else "portfolio-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "audience": audience,
            "profile_available": not recovery,
            "routes_checked": 4,
            "responses_checked": len(cases),
            "source_oracle_checked": not recovery,
            "certification_binding": binding,
            "codes": [t.code for t in approved.targets],
            "records_appended": 0,
            "source_rows_changed": 0,
            "all_amc_complete": False,
            "freshness_certified": False,
            "consumer_application_rbac_certified": False,
        },
    )
