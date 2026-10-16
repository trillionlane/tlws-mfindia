#!/usr/bin/env python3
"""Scratch-only reviewed view replay and original MF-source comparison, not DEV acceptance."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from mfdataindia.review.restored_facts import replay_owned_current_values
from mfdataindia.review.rollout_database import file_sha256, readonly
from mfdataindia.review.preservation import capture_preservation, compare_preservation
from mfdataindia.review.policy import fingerprint


def run(args, environment):
    if args.output.exists():
        raise ValueError("never replace an existing reconciliation receipt")
    if file_sha256(args.backup) != args.expected_backup_sha256:
        raise ValueError("restored original backup bytes differ from approval")
    evidence = json.loads(args.backup_evidence.read_text())
    if (
        evidence.get("backup_sha256") != args.expected_backup_sha256
        or evidence.get("backup_bytes") != args.backup.stat().st_size
    ):
        raise ValueError("backup evidence does not bind the exact original dump")
    dsn = environment["MFDATA_FACTS_CLONE_DSN"]
    parameters = conninfo_to_dict(dsn)
    if (
        parameters.get("host") not in ("127.0.0.1", "::1", "localhost")
        or parameters.get("hostaddr")
        or not 55500 <= int(parameters.get("port", "0")) <= 55699
        or parameters.get("user") != "postgres"
        or parameters.get("dbname") != args.scratch_database
    ):
        raise ValueError("refuse source/prod or unbounded clone connection before connecting")
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        result = replay_owned_current_values(
            conn,
            root,
            scratch_database=args.scratch_database,
            backup_sha256=args.expected_backup_sha256,
        )
    with readonly(dsn, statement_seconds=300) as conn:
        compare_preservation(
            evidence["baseline"],
            capture_preservation(conn),
            hashlib.sha256(
                (root / "sql/optional/023_verified_fund_review.sql").read_bytes()
            ).hexdigest(),
        )
    result.update(
        original_mf_source_comparison_verified=True,
        all_retained_layer_comparisons_still_required=True,
        private_http_and_actual_cloud_recovery_still_required=True,
    )
    receipt = {
        "format": "official-facts-restored-clone-reconciliation-v1",
        "evidence": result,
        "sha256": fingerprint(result),
    }
    with args.output.open("x") as handle:
        json.dump(receipt, handle, sort_keys=True, indent=2)
    return {"level": result["level"], "status": result["status"], "sha256": receipt["sha256"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--backup-evidence", type=Path, required=True)
    parser.add_argument("--expected-backup-sha256", required=True)
    parser.add_argument("--scratch-database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args, os.environ), sort_keys=True))
    except Exception as error:
        print("scratch-only facts reconciliation failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
