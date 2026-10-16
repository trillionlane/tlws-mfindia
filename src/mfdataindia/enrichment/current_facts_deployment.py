"""Pinned DEV current-facts operations and independent-reference projections."""

import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from mfdataindia.api import queries
from mfdataindia.enrichment import current_facts as engine, current_facts_store as store
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation
from mfdataindia.review.rollout import seal_evidence


def reference(manifest, environment):
    document = json.loads(environment["CURRENT_FACTS_REFERENCE"])
    if (
        fingerprint({"manifest": manifest.seal, "reference": document})
        != environment["CURRENT_FACTS_REFERENCE_HASH"]
    ):
        raise ValueError("current-facts independent reference binding differs")
    if set(document) != {str(c) for c in manifest.cohort}:
        raise ValueError("exact current-facts reference cohort required")
    for row in document.values():
        if set(row) != {"snapshot_id", "as_of", "values"} or set(row["values"]) != set(
            engine.FIELDS
        ):
            raise ValueError("exact source snapshot/date/field reference required")
    return document


def projections(conn, reviewed, *, active, core_queries=True):
    if store.projection_state(conn) != active:
        raise ValueError("current-facts activation state differs")
    cases = [
        (
            "/api/enrichment/v3/policy",
            {"policy_hash": fingerprint(engine.policy()), "document": engine.policy()},
        )
    ]
    core = {}
    for key, expected in reviewed.items():
        code = int(key)
        document = store.get(conn, code)
        for field, value in expected["values"].items():
            actual = document["fields"][field]
            if (
                actual["state"] != "available"
                or actual["value"] != value
                or actual["disclosure_as_of"] != expected["as_of"]
                or expected["snapshot_id"] not in {s["snapshot_id"] for s in actual["sources"]}
            ):
                raise ValueError("current-facts independent original-source reference differs")
        expected_values = {}
        for field, aliases in engine.FIELDS.items():
            value = expected["values"][field]
            if field == "fund_managers":
                value = "; ".join(value)
            if field == "base_expense_ratio":
                value = float(value)
            expected_values.update({alias: value for alias in aliases})
        if core_queries:
            # Owner-side activation must still check actual core SQL inside the
            # switch transaction. Never weaken that atomic publication guard.
            detail = queries.fund_detail(conn, code)
            batch = queries.funds_batch(conn, [str(code)])
            if not detail or batch["found"] != 1:
                raise ValueError("current-facts core code lookup unavailable")
            values = {
                alias: detail["facts"][alias]
                for aliases in engine.FIELDS.values() for alias in aliases
            }
        elif active:
            # The restricted certifier proves actual core behaviour over HTTP,
            # against the independently sealed original-source reference.
            values = expected_values
        else:
            # Already-authorized immutable baseline, not the core API tables or
            # queries. Fail closed for absent/empty baseline facts: sibling
            # fallback needs an explicit separate oracle, not guessed nulls.
            row = conn.execute(
                "SELECT benchmark_name,benchmark,fund_manager_name,base_expense_ratio "
                "FROM mf_live_facts.baseline WHERE amfi_scheme_code=%s", (code,)
            ).fetchone()
            if not row or not any(row.values()):
                raise ValueError("current-facts baseline reference unavailable")
            values = dict(row)
            if values["base_expense_ratio"] is not None:
                values["base_expense_ratio"] = float(values["base_expense_ratio"])
        if active:
            for field, aliases in engine.FIELDS.items():
                expected_value = expected["values"][field]
                if field == "fund_managers":
                    expected_value = "; ".join(expected_value)
                if field == "base_expense_ratio":
                    expected_value = float(expected_value)
                if any(values[a] != expected_value for a in aliases):
                    raise ValueError("existing core detail still serves a stale official fact")
                for alias in aliases if core_queries else ():
                    if alias != "benchmark" and batch["funds"][0].get(alias) != expected_value:
                        raise ValueError("existing core batch disagrees with official fact")
        core[key] = values
        cases.append((f"/api/enrichment/v3/funds/{code}/facts", document))
    return cases, core


def execute(manifest, environment, operation, *, root=Path("/app")):
    if operation not in ("install", "activate", "recover", "inspect"):
        raise ValueError("unsupported current-facts operation")
    reviewed = reference(manifest, environment) if operation in ("install", "activate") else None
    dsn = pinned_dsn(manifest, environment, "mfdata_admin")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if operation == "inspect":
            conn.execute("SET TRANSACTION READ ONLY")
        # The short switch/projection transaction must not consume the bulk
        # installation/inspection budget while API readers wait on view DDL.
        if operation in ("activate", "recover"):
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL transaction_timeout='15s'")
        else:
            conn.execute(
                "SET LOCAL statement_timeout='300s'"
                if operation == "inspect"
                else "SET LOCAL statement_timeout='120s'"
            )
            conn.execute("SET LOCAL transaction_timeout='420s'")
        conn.execute("SET LOCAL lock_timeout='5s'")
        if operation == "inspect":
            result = {
                "preservation": capture_preservation(conn, current_facts_baseline=True),
                "current_facts": store.capture(conn),
                "current_view_definition_hash": fingerprint(
                    store.view_definition(conn, "mf.current_fund_facts")
                ),
                "source_rows_changed": 0,
                "read_only": True,
            }
        else:
            result = (
                store.install(conn, root)
                if operation == "install"
                else store.switch(conn, activate=operation == "activate")
            )
        # Include post-switch source-to-existing-API proof in the same transaction;
        # any discrepancy rolls back the view switch rather than reporting PASS.
        if operation in ("install", "activate"):
            cases, values = projections(
                conn,
                reviewed,
                active=operation == "activate"
                if operation != "install"
                else store.projection_state(conn),
            )
            result.update(
                core_values=values, source_reference_checked=True, private_metadata_cases=len(cases)
            )
    return seal_evidence(
        manifest, "current-facts-" + operation, {"level": "DEV_DATABASE", **result}
    )
