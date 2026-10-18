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
    book_preservation,
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
    version = book_sources.registry()["version"]
    keys = {"reviewed", "prior_core_values", "preserved_core_values", "execution_id", "sources"}
    if version > 1:
        keys |= {"prior_projection", "original_oracles"}
    if version >= 4:
        keys.add("supplemental_oracles")
    if (
        set(doc) != keys
        or not re.fullmatch(
            re.escape(multi_facts.JOB) + r"-[a-z0-9-]{1,40}", str(doc["execution_id"])
        )
        or not isinstance(doc["reviewed"], list)
        or not 2 <= len(doc["reviewed"]) <= 7
        or len({r["code"] for r in doc["reviewed"]}) != len(doc["reviewed"])
    ):
        raise ValueError("bounded independent whole-AMC certification references required")
    if version > 1:
        prior = doc["prior_projection"]
        if (
            not isinstance(prior, dict)
            or prior.get("format") != ("official-prior-amc-projection-v2" if version >= 5 else "official-prior-amc-projection-v1")
            or prior.get("previous_source_version") != version - 1
            or prior.get("source_rows_changed") != 0
            or not isinstance(prior.get("codes"), list)
            or not 1 <= len(prior["codes"]) <= 4096
            or any(type(c) is not int or c <= 0 for c in prior["codes"])
            or prior["codes"] != sorted(set(prior["codes"]))
        ):
            raise ValueError("complete previous-AMC projection certificate required")
        if version >= 5:
            from mfdataindia.enrichment import book_preservation_v2

            book_preservation_v2.validate(prior)
            revised = book_preservation_v2.revised_keys(version)
            untouched = sorted(s["key"] for s in book_sources.registry(version - 1)["sources"] if s["key"] not in revised)
            if (prior["target_source_version"] != version or prior["revised_source_keys"] != sorted(revised)
                    or prior["untouched_source_keys"] != untouched):
                raise ValueError("entire declared prior AMC preservation scope required")
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
        sample_keys = {"code", "batch_id", "as_of", "values"}
        if version > 1:
            sample_keys.add("source_values")
        if (
            set(sample) != sample_keys
            or not re.fullmatch("[0-9a-f]{64}", str(sample["batch_id"]))
            or not isinstance(sample["values"], dict)
            or not sample["values"]
            or set(sample["values"]) - set(absl_expansion.FIELDS)
            or any(v is None for v in sample["values"].values())
        ):
            raise ValueError("nonempty independent dated field references required")
        if version > 1 and (
            not isinstance(sample["source_values"], dict)
            or set(sample["source_values"]) != set(sample["values"])
            or any(v is None for v in sample["source_values"].values())
        ):
            raise ValueError("independent original-token and canonical-value references required")
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
    if version > 1:
        oracles = doc["original_oracles"]
        if not isinstance(oracles, list) or not 4 <= len(oracles) <= 8:
            raise ValueError("independent original oracles for both books and periods required")
        for sample in oracles:
            if (
                not isinstance(sample, dict)
                or set(sample) != {"code", "batch_id", "as_of", "source_values"}
                or sample["code"] not in manifest.cohort
                or sample["batch_id"]
                not in {b for s in doc["sources"].values() for b in s["batch_ids"]}
                or not isinstance(sample["source_values"], dict)
                or not sample["source_values"]
                or set(sample["source_values"]) - set(absl_expansion.FIELDS)
                or any(v is None for v in sample["source_values"].values())
            ):
                raise ValueError("nonempty exact original-source oracle required")
    if version >= 4:
        supplemental = doc["supplemental_oracles"]
        if (
            not isinstance(supplemental, list)
            or not 4 <= len(supplemental) <= 8
        ):
            raise ValueError("distinct independent UTI TER references required")
        identities = set()
        for sample in supplemental:
            if (
                not isinstance(sample, dict)
                or set(sample) != {"code", "batch_id", "as_of", "value"}
                or type(sample["code"]) is not int
                or sample["code"] not in manifest.cohort
                or not isinstance(sample["batch_id"], str)
                or sample["batch_id"] not in doc["sources"]["uti-expanded"]["batch_ids"]
                or not isinstance(sample["as_of"], str)
                or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", sample["as_of"])
                or not isinstance(sample["value"], str)
                or not re.fullmatch(r"(?:[0-9](?:\.[0-9]{1,4})?|10(?:\.0{1,4})?)", sample["value"])
            ):
                raise ValueError("exact source-bound bounded Regular TER reference required")
            identity = (sample["code"], sample["batch_id"])
            if identity in identities:
                raise ValueError("distinct independent UTI TER references required")
            identities.add(identity)
    return doc


def new_supplemental_oracles(conn, expected):
    """UTI TER is independently source-checked; it must never become BER."""
    if book_sources.CURRENT_VERSION < 4:
        return {}
    batches = {}
    for batch_id in expected["sources"]["uti-expanded"]["batch_ids"]:
        row = conn.execute(
            "SELECT source_key,disclosure_as_of,document FROM mf_book_facts.batches WHERE batch_id=%s",
            (batch_id,),
        ).fetchone()
        if not row or row["source_key"] != "uti-expanded":
            raise ValueError("supplemental UTI batch differs")
        batches[batch_id] = row
    needed = {
        (batch_id, extraction["content_hash"])
        for batch_id, batch in batches.items()
        for extraction in batch["document"]["monthly_extractions"]
    }
    latest = max(batches, key=lambda key: batches[key]["disclosure_as_of"])
    observed, current_options = set(), set()
    for sample in expected["supplemental_oracles"]:
        facts = book_store.fund(conn, sample["code"])
        matches = [o for o in facts["observations"] if o["batch_id"] == sample["batch_id"]]
        if (
            len(matches) != 1
            or not matches[0]["current_identity_matches"]
            or matches[0]["disclosure_as_of"] != sample["as_of"]
        ):
            raise ValueError("supplemental source identity/date differs")
        result = matches[0]["result"]
        ter = result.get("reported_total_expense_ratio")
        ber = result.get("fields", {}).get("base_expense_ratio")
        if (
            not isinstance(ter, dict)
            or ter.get("status") != "accepted"
            or ter.get("value") != sample["value"]
            or ter.get("unit") != "percent"
            or ter.get("scope") != "regular_plan"
            or ter.get("expense_basis") != "TER"
            or not isinstance(ber, dict)
            or ber.get("status") != "unavailable"
            or "value" not in ber
            or ber["value"] is not None
            or (sample["batch_id"], ter.get("content_hash")) not in needed
        ):
            raise ValueError("independent source TER/basis or unavailable BER differs")
        observed.add((sample["batch_id"], ter["content_hash"]))
        if sample["batch_id"] == latest:
            current_options.add(facts["identity"]["option"])
    if observed != needed or not {"GROWTH", "IDCW"} <= current_options:
        raise ValueError("TER needs every actual English book/period and current Growth/IDCW")
    return {
        "independent_supplemental_oracles_checked": True,
        "supplemental_samples_checked": len(expected["supplemental_oracles"]),
        "supplemental_originals_checked": len(needed),
    }


def new_source_oracles(conn, expected):
    """No false PASS with only old-AMC samples or only active/newest books."""
    version = book_sources.CURRENT_VERSION
    if version == 1:
        return {"new_source_original_oracles": 0}
    old = {s["key"] for s in book_sources.registry(version - 1)["sources"]}
    added = {s["key"] for s in book_sources.registry(version)["sources"]} - old
    if version >= 5:
        from mfdataindia.enrichment import book_preservation_v2

        added |= set(book_preservation_v2.revised_keys(version))
    batches, needed, latest = {}, {}, {}
    for key in sorted(added):
        for batch_id in expected["sources"][key]["batch_ids"]:
            row = conn.execute(
                "SELECT source_key,disclosure_as_of,document FROM mf_book_facts.batches WHERE batch_id=%s",
                (batch_id,),
            ).fetchone()
            if not row or row["source_key"] != key:
                raise ValueError("new-source oracle batch differs")
            batches[batch_id] = row
            needed[batch_id] = {e["content_hash"] for e in row["document"]["monthly_extractions"]}
        latest[key] = max(
            (b for b in batches if batches[b]["source_key"] == key),
            key=lambda b: batches[b]["disclosure_as_of"],
        )
    observed, current_observed = {b: set() for b in batches}, {k: set() for k in added}
    options = {k: set() for k in added}
    for sample in [*expected["original_oracles"], *expected["reviewed"]]:
        batch_id = sample["batch_id"]
        if batch_id not in batches:
            continue
        facts = book_store.fund(conn, sample["code"])
        matches = [o for o in facts["observations"] if o["batch_id"] == batch_id]
        if (
            len(matches) != 1
            or not matches[0]["current_identity_matches"]
            or matches[0]["disclosure_as_of"] != sample["as_of"]
        ):
            raise ValueError("original-oracle identity/period differs")
        hashes = set()
        for field, value in sample["source_values"].items():
            actual = matches[0]["result"]["fields"].get(field)
            if not actual or actual["status"] != "accepted" or actual["value"] != value:
                raise ValueError("original oracle disagrees with raw monthly evidence")
            hashes.add(actual["content_hash"])
        if "values" in sample:
            key = batches[batch_id]["source_key"]
            if latest[key] == batch_id:
                current_observed[key] |= hashes
                options[key].add(facts["identity"]["option"])
        else:
            observed[batch_id] |= hashes
    if any(observed[b] != needed[b] for b in needed):
        raise ValueError(
            "every new original active/passive book and period needs independent raw-field oracles"
        )
    if any(
        current_observed[k] != needed[latest[k]] or not {"GROWTH", "IDCW"} <= options[k]
        for k in added
    ):
        raise ValueError("new AMC needs current Growth/IDCW and both active/passive API references")
    return {
        "new_source_original_oracles": len(expected["original_oracles"]),
        "new_source_original_documents": sum(map(len, needed.values())),
    }


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
        for key, reference in expected["sources"].items():
            population = book_store.population(conn, key)
            # Receipt is deliberately queried by exact source/execution, not by
            # latest success (which could hide a failed current source task).
            row = conn.execute(
                "SELECT document FROM mf_book_facts.runs WHERE source_key=%s AND execution_id=%s AND status<>'failed'",
                (key, expected["execution_id"]),
            ).fetchone()
            run = row["document"] if row else None
            if (
                not run
                or run["batch_ids"] != reference["batch_ids"]
                or run["population_hash"] != reference["population_hash"]
                or fingerprint(population) != reference["population_hash"]
                or len(population) != reference["denominator"]
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
        original_oracles = new_source_oracles(conn, expected)
        supplemental_oracles = new_supplemental_oracles(conn, expected)
        if active:
            book_projection.check(conn, expected["reviewed"])
        elif current.policy_document(conn) != (
            multi_facts.current_policy()
            if book_sources.CURRENT_VERSION == 1
            else book_sources.current_policy(book_sources.CURRENT_VERSION - 1)
        ) or not current.projection_state(conn):
            raise ValueError("accepted prior active graph not retained/restored")
        preservation_checked = {}
        if book_sources.CURRENT_VERSION > 1:
            actual_preservation = book_preservation.capture(conn, book_sources.CURRENT_VERSION - 1)
            if book_sources.CURRENT_VERSION >= 5:
                from mfdataindia.enrichment import book_preservation_v2

                preservation_checked = book_preservation_v2.compare(expected["prior_projection"], actual_preservation)
            elif actual_preservation != expected["prior_projection"]:
                raise ValueError("prior whole-AMC facts/provenance changed")
        config = book_store.installed_configuration(conn)["document"]
        cases = [
            (
                "/api/enrichment/v9/policy",
                {"configuration_hash": fingerprint(config), "document": config},
            ),
            ("/api/enrichment/v9/coverage", status),
        ]
        core = {**expected["prior_core_values"], **expected["preserved_core_values"]}
        if book_sources.CURRENT_VERSION > 1:
            for code in sorted(
                {o["code"] for o in expected["original_oracles"]}
                - {r["code"] for r in expected["reviewed"]}
            ):
                cases.append(
                    (f"/api/enrichment/v9/funds/{code}/facts", book_store.fund(conn, code))
                )
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
                    for k, v in sample.get("source_values", sample["values"]).items()
                )
            ):
                raise ValueError("retained fields disagree with independent original reference")
            cases.append((f"/api/enrichment/v9/funds/{code}/facts", facts))
            if not recovery or book_sources.CURRENT_VERSION > 1:
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
        # FS08 recovered the earlier 49-path profile. Later source upgrades
        # recover an already-established 52-path profile: old authority and
        # scalars must return, not fictitious disappearance of existing routes.
        book_cases = [(p, v) for p, v in cases if p.startswith("/api/enrichment/v9/")]
        routes_absent = recovery and book_sources.CURRENT_VERSION == 1
        check_http(
            session, origin, headers, book_cases if routes_absent else cases, recovery=routes_absent
        )
        if not routes_absent:
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
            "profile_available": not routes_absent,
            "metadata_responses_checked": len(book_cases if routes_absent else cases),
            "core_responses_checked": checked,
            "exact_execution_id": expected["execution_id"],
            "sources": expected["sources"],
            "independent_original_source_references_checked": True,
            **({"prior_projection_preservation": preservation_checked} if preservation_checked else {}),
            **original_oracles,
            **supplemental_oracles,
            "source_rows_changed": 0,
            "human_events_created": 0,
            "all_amc_data_complete": False,
            "adjusted_idcw_analytics_enabled": False,
        },
    )
