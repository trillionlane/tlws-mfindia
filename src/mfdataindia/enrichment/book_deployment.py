"""Manifest-bound owner operations; complete-AMC authority lives in the registry."""

import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from mfdataindia.enrichment import (
    book_projection,
    book_store,
    book_preservation,
    book_sources,
    book_upgrade,
    current_facts_store as current,
)
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.preservation import capture_preservation
from mfdataindia.review.rollout import seal_evidence


def bound(manifest, environment, purpose):
    value = json.loads(environment["AMC_BOOK_" + purpose])
    if (
        fingerprint({"manifest": manifest.seal, purpose.lower(): value})
        != environment["AMC_BOOK_" + purpose + "_HASH"]
    ):
        raise ValueError("manifest-bound book " + purpose.lower() + " required")
    return value


def execute(manifest, environment, operation, *, root=Path("/app")):
    if operation not in ("install", "activate", "recover", "recover-source", "inspect"):
        raise ValueError("unsupported book owner operation")
    dsn = pinned_dsn(manifest, environment, "mfdata_admin")
    body = (
        bound(manifest, environment, "OPERATION")
        if operation in ("activate", "recover", "recover-source")
        else {}
    )
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        if operation in ("install", "inspect"):
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                + (",READ ONLY" if operation == "inspect" else "")
            )
            conn.execute("SET LOCAL statement_timeout='300s'")
            conn.execute("SET LOCAL transaction_timeout='420s'")
            if operation == "install":
                result = book_store.install(conn, root)
            else:
                from mfdataindia.api import queries

                result = {
                    "read_only": True,
                    "source_rows_changed": 0,
                    "preservation": capture_preservation(conn, current_facts_baseline=True),
                    "book": book_store.capture(conn),
                    "current": current.capture(conn),
                    "status": book_store.status(conn),
                    "configuration": dict(current.configuration(conn)),
                    "core_definition": current.view_definition(conn, "mf.current_fund_facts"),
                    **(
                        {
                            "prior_projection": book_preservation.capture(
                                conn, book_sources.CURRENT_VERSION - 1
                            )
                        }
                        if book_sources.CURRENT_VERSION > 1
                        else {}
                    ),
                    "samples": {
                        str(code): {
                            "book": book_store.fund(conn, code),
                            "current": current.get(conn, code),
                            "core_facts": queries.fund_detail(conn, code)["facts"],
                        }
                        for code in manifest.cohort
                    },
                }
        elif operation == "activate":
            if set(body) != {"reviewed", "expected_configuration_hash"}:
                raise ValueError("independent references and prior configuration seal required")
            if {r["code"] for r in body["reviewed"]} - set(manifest.cohort):
                raise ValueError("reviewed samples outside bound rollout cohort")
            result = book_projection.activate(
                conn,
                root,
                body["reviewed"],
                expected_configuration_hash=body["expected_configuration_hash"],
            )
        elif operation == "recover-source":
            if set(body) != {"upgrade_id"}:
                raise ValueError("one sealed source-upgrade intent required")
            result = book_upgrade.recover_unactivated(conn, body["upgrade_id"])
        else:
            if set(body) != {"activation_id"}:
                raise ValueError("one sealed prior book intent required")
            result = book_projection.recover(conn, root, body["activation_id"])
    return seal_evidence(manifest, "amc-book-" + operation, {"level": "DEV_DATABASE", **result})
