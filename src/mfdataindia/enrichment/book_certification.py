"""Bound real-source references, signed private HTTP and core scalar agreement."""

import json
import re
import time
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg
from psycopg.rows import dict_row
import requests

from mfdataindia.enrichment import (
    absl_expansion,
    book_deployment,
    book_projection,
    book_sources,
    book_store,
    current_facts_store as current,
    fact_review_store,
    multi_facts,
    multi_store,
)
from mfdataindia.enrichment.certification import check_http
from mfdataindia.enrichment.current_facts_certification import REVIEW_ORIGIN, core_http
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.certification import metadata_identity
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_certification import request_origins
from mfdataindia.review.rollout import seal_evidence


def expectation(manifest, environment):
    doc = book_deployment.bound(manifest, environment, "CERTIFICATION")
    if (
        set(doc)
        != {"reviewed", "prior_core_values", "preserved_core_values", "execution_id", "sources"}
        or not re.fullmatch(
            re.escape(multi_facts.JOB) + r"-[a-z0-9-]{1,40}", str(doc["execution_id"])
        )
        or not isinstance(doc["reviewed"], list)
        or not 2 <= len(doc["reviewed"]) <= 7
        or len({r["code"] for r in doc["reviewed"]}) != len(doc["reviewed"])
    ):
        raise ValueError("bounded independent whole-AMC certification references required")
    codes = {r["code"] for r in doc["reviewed"]}
    if not codes <= set(manifest.cohort) or set(doc["prior_core_values"]) != set(map(str, codes)):
        raise ValueError("exact reference and prior core sample cohort required")
    aliases = {
        a for group in book_sources.current_policy()["supported_fields"].values() for a in group
    }
    if set(doc["preserved_core_values"]) != {"101763", "103173", "103174"} or any(
        set(v) != aliases
        for v in [*doc["prior_core_values"].values(), *doc["preserved_core_values"].values()]
    ):
        raise ValueError("all prior and retained pilot core aliases required")
    fields = set()
    for sample in doc["reviewed"]:
        if (
            set(sample) != {"code", "batch_id", "as_of", "values"}
            or not re.fullmatch("[0-9a-f]{64}", str(sample["batch_id"]))
            or not isinstance(sample["values"], dict)
            or not sample["values"]
            or set(sample["values"]) - set(absl_expansion.FIELDS)
            or any(v is None for v in sample["values"].values())
        ):
            raise ValueError("nonempty independent dated field references required")
        fields.update(sample["values"])
    if fields != set(absl_expansion.FIELDS):
        raise ValueError("all five supported fields require an independent reference")
    expected_sources = {s["key"] for s in book_sources.registry()["sources"]}
    if set(doc["sources"]) != expected_sources:
        raise ValueError("every registered book source must be certified")
    for s in doc["sources"].values():
        if (
            set(s) != {"population_hash", "denominator", "batch_ids"}
            or type(s["denominator"]) is not int
            or not 1 <= s["denominator"] <= 1024
            or not re.fullmatch("[0-9a-f]{64}", str(s["population_hash"]))
            or not isinstance(s["batch_ids"], list)
            or len(s["batch_ids"]) != 2
            or len(set(s["batch_ids"])) != 2
            or any(not re.fullmatch("[0-9a-f]{64}", str(b)) for b in s["batch_ids"])
        ):
            raise ValueError("two distinct exact batches and complete denominator required")
    if any(
        r["batch_id"] not in {b for s in doc["sources"].values() for b in s["batch_ids"]}
        for r in doc["reviewed"]
    ):
        raise ValueError("review sample outside certified source batches")
    return doc


def certify(manifest, environment, *, active=False, recovery=False):
    if active and recovery:
        raise ValueError("recovery cannot claim active book projection")
    expected = expectation(manifest, environment)
    dsn = pinned_dsn(manifest, environment, "mfdata_verified_reader")
    origin, audience = request_origins(environment)
    candidate = (
        "https://cert-" + manifest.rollout_id + "---" + REVIEW_ORIGIN.removeprefix("https://")
    )
    if audience != REVIEW_ORIGIN or origin not in (REVIEW_ORIGIN, candidate):
        raise ValueError("exact private book service origin required")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        book_store.verify_acl(conn)
        before = (
            book_store.capture(conn),
            multi_store.capture(conn),
            current.capture(conn),
            fact_review_store.capture(conn),
        )
        status = book_store.status(conn)
        for source in status["sources"]:
            reference = expected["sources"][source["source_key"]]
            # Receipt is deliberately queried by exact source/execution, not by
            # latest success (which could hide a failed current source task).
            row = conn.execute(
                "SELECT document FROM mf_book_facts.runs WHERE source_key=%s AND execution_id=%s AND status<>'failed'",
                (source["source_key"], expected["execution_id"]),
            ).fetchone()
            run = row["document"] if row else None
            if (
                not run
                or run["batch_ids"] != reference["batch_ids"]
                or run["population_hash"] != reference["population_hash"]
                or source["current_population_hash"] != reference["population_hash"]
                or source["current_denominator"] != reference["denominator"]
            ):
                raise ValueError("complete exact-execution current source receipt differs")
        for table in ("mf_fact_refresh.runs", "mf_multi_facts.runs"):
            row = conn.execute(
                "SELECT document FROM " + table + " WHERE execution_id=%s AND status<>'failed'",
                (expected["execution_id"],),
            ).fetchone()
            if not row:
                raise ValueError("retained adapters require same-execution successful receipts")
        options = {
            book_store.fund(conn, r["code"])["identity"]["option"] for r in expected["reviewed"]
        }
        if not {"GROWTH", "IDCW"} <= options:
            raise ValueError("independent Growth and IDCW source samples required")
        if active:
            book_projection.check(conn, expected["reviewed"])
        elif current.policy_document(
            conn
        ) != multi_facts.current_policy() or not current.projection_state(conn):
            raise ValueError("accepted prior active graph not retained/restored")
        config = book_sources.configuration()
        cases = [
            (
                "/api/enrichment/v9/policy",
                {"configuration_hash": fingerprint(config), "document": config},
            ),
            ("/api/enrichment/v9/coverage", status),
        ]
        core = {**expected["prior_core_values"], **expected["preserved_core_values"]}
        for sample in expected["reviewed"]:
            code = sample["code"]
            facts = book_store.fund(conn, code)
            matches = [o for o in facts["observations"] if o["batch_id"] == sample["batch_id"]]
            if (
                len(matches) != 1
                or not matches[0]["current_identity_matches"]
                or matches[0]["disclosure_as_of"] != sample["as_of"]
                or any(
                    matches[0]["result"]["fields"][k]["status"] != "accepted"
                    or matches[0]["result"]["fields"][k]["value"] != v
                    for k, v in sample["values"].items()
                )
            ):
                raise ValueError("retained fields disagree with independent original reference")
            cases.append((f"/api/enrichment/v9/funds/{code}/facts", facts))
            if not recovery:
                cases.append((f"/api/enrichment/v3/funds/{code}/facts", current.get(conn, code)))
            if active:
                core[str(code)] = dict(core[str(code)])
                for field, value in sample["values"].items():
                    scalar = (
                        "; ".join(value)
                        if field == "fund_managers"
                        else float(value)
                        if field in ("aum", "base_expense_ratio")
                        else value
                    )
                    core[str(code)].update(
                        {
                            a: scalar
                            for a in book_sources.current_policy()["supported_fields"][field]
                        }
                    )
        for code in expected["preserved_core_values"]:
            if not recovery:
                cases.append(
                    (f"/api/enrichment/v3/funds/{code}/facts", current.get(conn, int(code)))
                )
    trust = json.loads(Path("/trust/config.json").read_text())
    if fingerprint(trust) != manifest.trust_hash:
        raise ValueError("book certification trust differs")
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
        # During recovery only the three new route patterns must disappear;
        # retained current metadata continues to exist in the previous profile.
        book_cases = [(p, v) for p, v in cases if p.startswith("/api/enrichment/v9/")]
        check_http(session, origin, headers, book_cases if recovery else cases, recovery=recovery)
        if not recovery:
            for route, _ in book_cases:
                if (
                    session.post(
                        origin + route, json={}, headers=headers, timeout=30, allow_redirects=False
                    ).status_code
                    != 405
                ):
                    raise ValueError("book HTTP mutation unexpectedly permitted")
            route = book_cases[-1][0]
            for suffix in ("/raw", "/content"):
                if (
                    session.get(
                        origin + route + suffix, headers=headers, timeout=30, allow_redirects=False
                    ).status_code
                    != 404
                ):
                    raise ValueError("raw original bytes exposed over book HTTP")
        checked = core_http(session, core)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        if before != (
            book_store.capture(conn),
            multi_store.capture(conn),
            current.capture(conn),
            fact_review_store.capture(conn),
        ):
            raise ValueError("book certification dependencies changed")
    return seal_evidence(
        manifest,
        "amc-book-recovery-http" if recovery else "amc-book-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "projection_active": active,
            "profile_available": not recovery,
            "metadata_responses_checked": len(book_cases if recovery else cases),
            "core_responses_checked": checked,
            "exact_execution_id": expected["execution_id"],
            "sources": expected["sources"],
            "independent_original_source_references_checked": True,
            "source_rows_changed": 0,
            "human_events_created": 0,
            "all_amc_data_complete": False,
            "adjusted_idcw_analytics_enabled": False,
        },
    )
