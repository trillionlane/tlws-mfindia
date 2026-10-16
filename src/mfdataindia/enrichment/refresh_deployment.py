"""Approved bounded DEV schema/bootstrap/current-policy transition operations."""

import json
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from mfdataindia.enrichment import refresh_store, monthly_projection, current_facts_store
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation
from mfdataindia.review.rollout import seal_evidence


def approved(manifest, environment):
    body = json.loads(environment["FACT_REFRESH_OPERATION"])
    if (
        fingerprint({"manifest": manifest.seal, "operation": body})
        != environment["FACT_REFRESH_OPERATION_HASH"]
    ):
        raise ValueError("exact source refresh operation binding differs")
    return body


def execute(manifest, environment, operation, *, root=Path("/app")):
    if operation not in ("install", "bootstrap", "activate", "recover", "inspect"):
        raise ValueError("unsupported bounded refresh operation")
    body = approved(manifest, environment) if operation not in ("install", "inspect") else None
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
                refresh_store.install(conn, root)
                if operation == "install"
                else {
                    "read_only": True,
                    "source_rows_changed": 0,
                    "preservation": capture_preservation(conn, current_facts_baseline=True),
                    "refresh": refresh_store.capture(conn),
                    "current": current_facts_store.capture(conn),
                }
            )
        elif operation == "activate":
            if set(body) != {"legacy_snapshot_ids", "reviewed"}:
                raise ValueError("exact current transition inputs required")
            result = monthly_projection.activate(conn, root, **body)
        elif operation == "recover":
            if set(body) != {"activation_id"}:
                raise ValueError("exact recovery transition required")
            result = monthly_projection.recover(conn, body["activation_id"])
        else:
            conn.execute("SET LOCAL statement_timeout='15s'")
            if (
                set(body) != {"receipts"}
                or not isinstance(body["receipts"], list)
                or len(body["receipts"]) != 2
            ):
                raise ValueError("exact two retained reporting-period receipts required")
            results = []
            for receipt in body["receipts"]:
                if receipt.get("mode") != "retained_bootstrap" or receipt.get("status") not in (
                    "updated",
                    "completed_with_field_gaps",
                ):
                    raise ValueError("owner-only retained source bootstrap required")
                results.append(refresh_store.append_run(conn, receipt))
            if (
                len({r["content_hash"] for r in results}) != 2
                or len({r["disclosure_as_of"] for r in results}) != 2
            ):
                raise ValueError("two distinct real retained books/periods required")
            result = {"receipts": results, "source_rows_changed": 0}
    return seal_evidence(manifest, "fact-refresh-" + operation, {"level": "DEV_DATABASE", **result})
