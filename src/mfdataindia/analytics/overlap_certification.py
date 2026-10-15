"""Private real HTTP plus independently derived source-pair oracle; no writes."""

import json
import time
from pathlib import Path
from uuid import uuid4
import jwt
import psycopg
import requests
from psycopg.rows import dict_row
from mfdataindia.analytics import (
    overlap as engine,
    overlap_store as store,
    overlap_schemas as schemas,
)
from mfdataindia.analytics.overlap_deployment import selection
from mfdataindia.enrichment.certification import check_http
from mfdataindia.enrichment.portfolio_certification import retained as retained_portfolios
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.rollout import seal_evidence


def oracle_projection(document):
    return {
        "observed_state": document["observed_state"],
        "observed_overlap_percent": document["observed_overlap_percent"],
        "shared_security_count": document["shared_security_count"],
        "shared_holdings_hash": fingerprint(document["shared_holdings"]),
        "coverage": document["coverage"],
        "disclosure_as_of": document["disclosure_as_of"],
        "full_economic_state": document["full_economic_state"],
        "full_economic_overlap_percent": document["full_economic_overlap_percent"],
    }


def projections(conn, pair, oracle):
    response, document = store.get(
        conn,
        pair.left_code,
        pair.right_code,
        left_snapshot_id=pair.left_snapshot_id,
        right_snapshot_id=pair.right_snapshot_id,
    )
    if (
        response["state"] != "available"
        or document is None
        or oracle_projection(document) != oracle
    ):
        raise ValueError("independent source-pair oracle differs")
    if (
        document["full_economic_overlap_percent"] is not None
        or document["scope"] != "observed_reported_isin_securities_only"
    ):
        raise ValueError("unrestricted overlap was not withheld")
    path = f"/api/overlap/v1/pairs/{pair.left_code}/{pair.right_code}"
    query = f"?left_snapshot_id={pair.left_snapshot_id}&right_snapshot_id={pair.right_snapshot_id}"
    result_path = "/api/overlap/v1/results/" + document["result_id"]
    cases = [
        (
            "/api/overlap/v1/policy",
            {"policy_hash": fingerprint(engine.policy()), "document": store.installed_policy(conn)},
        ),
        (
            path,
            schemas.Response.model_validate(
                store.get(conn, pair.left_code, pair.right_code)[0]
            ).model_dump(mode="json"),
        ),
        (path + query, response),
        (
            result_path,
            schemas.Result.model_validate(
                {k: v for k, v in document.items() if k != "shared_holdings"}
            ).model_dump(mode="json"),
        ),
    ]
    # Current and exact-pin checks are both required, not just historical success.
    if (
        cases[1][1]["state"] != "available"
        or cases[1][1]["result"]["result_id"] != document["result_id"]
    ):
        raise ValueError("accepted pair is not the current available projection")
    after, observed = "", []
    while True:
        page = store.shared(conn, document["result_id"], after=after, limit=10)
        cases.append((result_path + "/shared-holdings?limit=10&after_isin=" + after, page))
        observed.extend(page["items"])
        if page["next_after_isin"] is None:
            break
        if page["next_after_isin"] <= after or len(observed) > 1000:
            raise ValueError("overlap pagination did not advance")
        after = page["next_after_isin"]
    if observed != document["shared_holdings"]:
        raise ValueError("overlap pagination lost or duplicated rows")
    return cases


def retained(conn):
    return {
        "portfolios_and_prior": retained_portfolios(conn),
        "results": conn.execute(
            "SELECT result_id,encode(sha256(convert_to(document::text,'UTF8')),'hex') AS hash FROM mf_overlap.results ORDER BY result_id"
        ).fetchall(),
    }


def certify(manifest, environment, *, recovery=False):
    pair = selection(manifest, environment)
    oracle = json.loads(environment["OVERLAP_EXPECTATIONS"])
    binding = fingerprint({"pair": pair.model_dump(mode="json"), "oracle": oracle})
    if binding != environment["OVERLAP_CERTIFICATION_HASH"]:
        raise ValueError("overlap certificate approval differs")
    dsn = pinned_dsn(manifest, environment, store.portfolios.evidence.ROLES[1])
    origin, audience = request_origins(environment)
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("overlap certifier trust differs")
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
        cases = projections(conn, pair, oracle)
    with requests.Session() as session:
        session.trust_env = False
        headers = {
            "X-Serverless-Authorization": "Bearer " + metadata_identity(session, audience),
            "Authorization": "Bearer " + assertion,
        }
        check_http(session, origin, headers, cases, recovery=recovery)
        if not recovery:
            for path in (
                "/api/overlap/v1/pairs/1/1",
                "/api/overlap/v1/pairs/1/2?left_snapshot_id=" + pair.left_snapshot_id,
            ):
                if (
                    session.get(
                        origin + path, headers=headers, timeout=30, allow_redirects=False
                    ).status_code
                    != 422
                ):
                    raise ValueError("invalid overlap pair accepted")
            if (
                session.post(
                    origin + cases[0][0],
                    headers=headers,
                    json={},
                    timeout=30,
                    allow_redirects=False,
                ).status_code
                != 405
            ):
                raise ValueError("unexpected HTTP overlap writer")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if retained(conn) != before:
            raise ValueError("overlap certification changed retained records")
    return seal_evidence(
        manifest,
        "overlap-recovery-http" if recovery else "overlap-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": origin,
            "audience": audience,
            "profile_available": not recovery,
            "routes_checked": 4,
            "responses_checked": len(cases),
            "independent_source_pair_checked": not recovery,
            "certification_binding": binding,
            "source_rows_changed": 0,
            "records_appended": 0,
            "full_economic_overlap_available": False,
            "all_amc_complete": False,
            "consumer_application_rbac_certified": False,
        },
    )
