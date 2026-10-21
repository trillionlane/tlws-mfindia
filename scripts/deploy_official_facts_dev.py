#!/usr/bin/env python3
"""Explicit fixed DEV sidecar installation/publication; no original-data mutations."""
import argparse
import json
import os
import sys
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from mfdataindia.enrichment import facts_operations, facts_store, facts_schemas
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.rollout import Manifest, seal_evidence
from mfdataindia.review.policy import fingerprint


def execute(manifest, environment, operation, *, root=Path("/app")):
    if operation == "publish":
        from mfdataindia.enrichment.pdf_source_dispatch import require_source_authority

        require_source_authority()
    if operation not in ("install", "publish"):
        raise ValueError("unsupported facts operation")
    selection = facts_schemas.Selection.model_validate_json(environment["OFFICIAL_FACTS_SELECTION"]) if operation == "publish" else None
    if selection and (not set(selection.codes) <= set(manifest.cohort) or fingerprint(selection.model_dump(mode="json")) != environment["OFFICIAL_FACTS_SELECTION_HASH"]):
        raise ValueError("facts selection approval differs")
    role = "mfdata_admin" if operation == "install" else facts_store.evidence.ROLES[0]
    dsn = pinned_dsn(manifest, environment, role)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL " + ("REPEATABLE READ" if operation == "install" else "READ COMMITTED"))
        if operation == "install":
            result = facts_operations.install(conn, root)
        else:
            conn.execute("SET LOCAL lock_timeout='5s'")
            conn.execute("SET LOCAL statement_timeout='120s'")
            conn.execute("SET LOCAL transaction_timeout='180s'")
            snapshots = facts_store.publish(conn, selection)
            result = {"snapshot_ids": [s["snapshot_id"] for s in snapshots],
                      "codes": selection.codes, "accepted_fields_per_snapshot": [sum(v["status"] == "accepted" for v in s["fields"].values()) for s in snapshots],
                      "selection_hash": fingerprint(selection.model_dump(mode="json")), "source_rows_changed": 0}
    return seal_evidence(manifest, "official-facts-" + operation, {"level": "DEV_DATABASE", **result})


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("operation", choices=["install", "publish"])
        args = parser.parse_args()
        if args.operation == "publish":
            from mfdataindia.enrichment.pdf_source_dispatch import require_source_authority

            require_source_authority()
        print(json.dumps(execute(Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"]), os.environ, args.operation), sort_keys=True))
    except Exception as exc:
        print("official facts operation failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
