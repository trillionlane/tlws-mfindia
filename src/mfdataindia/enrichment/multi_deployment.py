"""Manifest-bound DEV operations; no flags added to GitHub environment configuration."""

import json
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from mfdataindia.enrichment import multi_store, multi_projection, current_facts_store
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation
from mfdataindia.review.rollout import seal_evidence


def execute(manifest, environment, operation, *, root=Path("/app")):
    if operation not in ("install", "activate", "recover", "inspect"):
        raise ValueError("unsupported registered source operation")
    if set(manifest.cohort) != {101763, 103173, 103174}:
        raise ValueError("exact approved two-source rollout cohort required")
    body = {}
    if operation in ("activate", "recover"):
        body = json.loads(environment["REGISTERED_FACTS_OPERATION"])
        if (
            fingerprint({"manifest": manifest.seal, "operation": body})
            != environment["REGISTERED_FACTS_OPERATION_HASH"]
        ):
            raise ValueError("bound registered-source operation required")
    dsn = pinned_dsn(manifest, environment, "mfdata_admin")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        if operation in ("install", "inspect"):
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                + (", READ ONLY" if operation == "inspect" else "")
            )
            conn.execute("SET LOCAL statement_timeout='300s'")
            conn.execute("SET LOCAL transaction_timeout='420s'")
            result = (
                multi_store.install(conn, root)
                if operation == "install"
                else {
                    "read_only": True,
                    "source_rows_changed": 0,
                    "preservation": capture_preservation(conn, current_facts_baseline=True),
                    "registered": multi_store.capture(conn),
                    "current": current_facts_store.capture(conn),
                    "status": multi_store.status(conn),
                    "facts": multi_store.get(conn, 101763),
                    "current_hdfc": current_facts_store.get(conn, 101763),
                    "configuration": dict(current_facts_store.configuration(conn)),
                    "core_definition": current_facts_store.view_definition(
                        conn, "mf.current_fund_facts"
                    ),
                }
            )
            if operation == "inspect":
                from mfdataindia.api import queries

                detail = queries.fund_detail(conn, 101763)
                result["core_values"] = {
                    alias: detail["facts"][alias]
                    for aliases in current_facts_store.policy_document(conn)[
                        "supported_fields"
                    ].values()
                    for alias in aliases
                }
        elif operation == "activate":
            if set(body) != {"reviewed"}:
                raise ValueError("exact independent source reference required")
            result = multi_projection.activate(conn, root, body["reviewed"])
        else:
            if set(body) != {"activation_id"}:
                raise ValueError("exact prior transition required")
            result = multi_projection.recover(conn, root, body["activation_id"])
    return seal_evidence(
        manifest, "registered-facts-" + operation, {"level": "DEV_DATABASE", **result}
    )
