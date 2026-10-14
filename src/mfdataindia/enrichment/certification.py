"""Real private evidence HTTP certification, never acceptance of extracted facts."""

import json
import hashlib
import re
import time
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg
import requests
from psycopg.rows import dict_row
from psycopg import sql

from mfdataindia.enrichment import evidence, schemas, store
from mfdataindia.enrichment.deployment import pinned_dsn, source_request
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.publication_operations import capture_review
from mfdataindia.review.rollout import seal_evidence


def metadata_state(conn):
    state = {}
    for table, key in (("documents", "document_id"), ("mappings", "mapping_id")):
        digest, count = hashlib.sha256(), 0
        query = sql.SQL("COPY (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') FROM mf_enrichment.{} t ORDER BY {}) TO STDOUT (FORMAT BINARY)").format(sql.Identifier(table), sql.Identifier(key))
        with conn.cursor() as cursor, cursor.copy(query) as stream:
            stream.set_types(["text"])
            for (sealed,) in stream.rows():
                digest.update((sealed + "\n").encode())
                count += 1
        state[table] = {"rows": count, "hash": digest.hexdigest()}
    return state


def projections(conn, source, document_id):
    if not re.fullmatch("[0-9a-f]{64}", document_id):
        raise ValueError("exact evidence document ID required")
    document = store.get_document(conn, document_id)
    if (document["intake"] != source.intake.model_dump(mode="json")
            or document["content_hash"] != source.intake.expected_sha256
            or document["byte_length"] != source.byte_length
            or document["publication"] != "evidence_only_not_accepted_facts"):
        raise ValueError("stored document differs from approved original source")
    base = "/api/enrichment/v1"
    result = [
        (base + "/policy", {"policy_hash": fingerprint(evidence.policy()), "document": store.installed_policy(conn)}),
        (base + "/documents?limit=1", schemas.DocumentPage.model_validate(store.documents(conn, limit=1)).model_dump(mode="json")),
        (base + "/documents/" + document_id, document),
    ]
    for code in source.intake.codes:
        expected = schemas.Coverage.model_validate(store.coverage(conn, code, document_id=document_id)).model_dump(mode="json")
        if (expected["document_id"] != document_id or expected["facts_available"]
                or expected["universe_complete"] or expected["mapping"] is None):
            raise ValueError("real retained evidence coverage missing or overclaimed")
        result.append((base + "/funds/" + str(code) + "/coverage?document_id=" + document_id, expected))
    return result


def check_http(session, origin, headers, cases, *, recovery=False):
    for route, expected in cases:
        denied = session.get(origin + route, headers={"X-Serverless-Authorization": headers["X-Serverless-Authorization"]}, timeout=30, allow_redirects=False)
        if (denied.status_code != (404 if recovery else 401)
                or denied.headers.get("Cache-Control") != "no-store"):
            raise ValueError("evidence assertion/profile denial differs")
        for _ in range(2):
            response = session.get(origin + route, headers=headers, timeout=30, allow_redirects=False)
            if (response.status_code != (404 if recovery else 200)
                    or response.headers.get("Cache-Control") != "no-store"
                    or not recovery and response.json() != expected):
                raise ValueError("evidence HTTP status/cache/projection/replay differs")
    if not recovery:
        for route, body in (("/api/enrichment/v1/documents", {}),):
            response = session.post(origin + route, json=body, headers=headers, timeout=30, allow_redirects=False)
            if response.status_code != 405:
                raise ValueError("unexpected HTTP source intake writer")
        for route, _ in cases:
            if re.fullmatch(r"/api/enrichment/v1/documents/[0-9a-f]{64}", route):
                for suffix in ("/raw", "/content"):
                    response = session.get(origin + route + suffix, headers=headers, timeout=30, allow_redirects=False)
                    if response.status_code != 404:
                        raise ValueError("unexpected raw document HTTP access")


def certify(manifest, environment, *, recovery=False):
    dsn = pinned_dsn(manifest, environment, store.ROLES[1])
    source = source_request(manifest, environment)
    origin, audience = request_origins(environment)
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("evidence certifier trust differs")
    verifier = AssertionVerifier(**{**trust, "allowed_scopes": frozenset(trust["allowed_scopes"])})
    now = int(time.time())
    assertion = jwt.encode({"iss": verifier.issuer, "aud": verifier.audience, "caller": verifier.caller,
        "sub": manifest.actor, "scope": ["review:read"], "iat": now, "nbf": now,
        "exp": now + 240, "jti": uuid4().hex}, Path("/signer/key.pem").read_text(), algorithm="EdDSA")

    def retained():
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            verify_runtime(conn, "api")
            store.verify_acl(conn)
            private = metadata_state(conn)
            return {"review": capture_review(conn, include_sequence=False), "evidence": private}

    before = retained()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cases = projections(conn, source, environment["ENRICHMENT_DOCUMENT_ID"])
    with requests.Session() as session:
        session.trust_env = False
        iam = metadata_identity(session, audience)
        check_http(session, origin, {"X-Serverless-Authorization": "Bearer " + iam, "Authorization": "Bearer " + assertion}, cases, recovery=recovery)
    if retained() != before:
        raise ValueError("private evidence/read requests changed retained state")
    return seal_evidence(manifest, "enrichment-recovery-http" if recovery else "enrichment-http", {
        "level": "DEV_PRIVATE_HTTP", "origin": origin, "audience": audience,
        "document_id": environment["ENRICHMENT_DOCUMENT_ID"], "source_binding_hash": source.bind(manifest),
        "content_hash": source.intake.expected_sha256, "routes_checked": 4,
        "responses_checked": len(cases), "profile_available": not recovery,
        "codes": source.intake.codes, "source_rows_changed": 0, "records_appended": 0,
        "accepted_facts_published": 0, "raw_document_http_access": False,
        "whole_amc_coverage_certified": False, "consumer_application_rbac_certified": False,
    })
