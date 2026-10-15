"""Private source-to-API checks with separately reviewed value expectations."""
import json
import time
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg
import requests
from psycopg.rows import dict_row

from mfdataindia.enrichment import facts, facts_schemas as schemas, facts_store
from mfdataindia.enrichment.certification import check_http, metadata_state
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.publication_operations import capture_review
from mfdataindia.review.rollout import seal_evidence


def projections(conn, selection, expected_fields):
    if set(expected_fields) != {"benchmark_name", "fund_managers", "base_expense_ratio"}:
        raise ValueError("three separately reviewed accepted field expectations required")
    base = "/api/enrichment/v2"
    cases = [(base + "/policy", {"policy_hash": fingerprint(facts.policy()), "document": facts_store.installed_policy(conn)})]
    for code in selection.codes:
        current = schemas.Response.model_validate(facts_store.get(conn, code)).model_dump(mode="json")
        snapshot = current["snapshot"]
        if (current["state"] != "available" or snapshot is None
                or snapshot["document_id"] != selection.document_id
                or snapshot["identity_document_id"] != selection.identity_document_id
                or snapshot["disclosure_extraction"]["page"] != selection.facts_page
                or snapshot["identity_extraction"]["page"] != selection.identity_page):
            raise ValueError("real accepted disclosure/identity mapping missing")
        for name, expected in expected_fields.items():
            field = snapshot["fields"][name]
            if field["status"] != "accepted" or field["value"] != expected or field["disclosure_as_of"] != snapshot["disclosure_as_of"]:
                raise ValueError("reviewed source-to-field expectation differs")
        if (snapshot["fields"]["aum"]["value"] is not None
                or snapshot["fields"]["total_expense_ratio"]["value"] is not None
                or snapshot["fields"]["base_expense_ratio"]["unit"] != "percent"
                or snapshot["fields"]["base_expense_ratio"]["scope"] != "regular_plan"
                or snapshot["identity_evidence"]["amfi_code"] != code):
            raise ValueError("unsupported units, TER/AUM or identity claims")
        route = base + "/funds/" + str(code)
        cases.extend([(route + "/facts", current),
                      (route + "/facts?snapshot_id=" + snapshot["snapshot_id"], current),
                      (route + "/snapshots?limit=1", schemas.Page.model_validate(facts_store.snapshots(conn, code, limit=1)).model_dump(mode="json"))])
    return cases


def certify(manifest, environment, *, recovery=False):
    selection = schemas.Selection.model_validate_json(environment["OFFICIAL_FACTS_SELECTION"])
    oracle = json.loads(environment["OFFICIAL_FACTS_EXPECTATIONS"])
    binding = fingerprint({"selection": selection.model_dump(mode="json"), "expected_fields": oracle})
    if binding != environment["OFFICIAL_FACTS_CERTIFICATION_HASH"] or not set(selection.codes) <= set(manifest.cohort):
        raise ValueError("official facts certificate approval differs")
    dsn = pinned_dsn(manifest, environment, facts_store.evidence.ROLES[1])
    origin, audience = request_origins(environment)
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("facts certifier trust differs")
    verifier = AssertionVerifier(**{**trust, "allowed_scopes": frozenset(trust["allowed_scopes"])})
    now = int(time.time())
    assertion = jwt.encode({"iss": verifier.issuer, "aud": verifier.audience, "caller": verifier.caller,
        "sub": manifest.actor, "scope": ["review:read"], "iat": now, "nbf": now,
        "exp": now + 240, "jti": uuid4().hex}, Path("/signer/key.pem").read_text(), algorithm="EdDSA")

    def retained():
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            verify_runtime(conn, "api")
            return {"review": capture_review(conn, include_sequence=False), "evidence": metadata_state(conn), "facts": facts_store.capture(conn)}
    before = retained()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cases = projections(conn, selection, oracle)
    with requests.Session() as session:
        session.trust_env = False
        iam = metadata_identity(session, audience)
        headers = {"X-Serverless-Authorization": "Bearer " + iam, "Authorization": "Bearer " + assertion}
        check_http(session, origin, headers, cases, recovery=recovery)
        if not recovery:
            for code in selection.codes:
                result = session.post(origin + "/api/enrichment/v2/funds/" + str(code) + "/facts", headers=headers, json={}, timeout=30, allow_redirects=False)
                if result.status_code != 405:
                    raise ValueError("unexpected HTTP disclosure writer")
    if retained() != before:
        raise ValueError("certification changed retained data")
    return seal_evidence(manifest, "official-facts-recovery-http" if recovery else "official-facts-http", {
        "level": "DEV_PRIVATE_HTTP", "origin": origin, "audience": audience, "profile_available": not recovery,
        "routes_checked": 3, "responses_checked": len(cases), "codes": selection.codes,
        "source_to_field_expectations_checked": not recovery, "certification_binding": binding,
        "accepted_fields_per_fund": 3, "records_appended": 0, "source_rows_changed": 0,
        "whole_amc_coverage_certified": False, "freshness_certified": False,
        "consumer_application_rbac_certified": False})
