#!/usr/bin/env python3
"""Governed DATA-03/04 operator commands. No cloud provisioning or deployment.

Approval and correction use DIFFERENT dedicated restricted SQL principals.
Restore proof is independently verified operator evidence, not a self-issued
claim that synthetic tests or supplied hashes prove a cloud backup restore.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.audit.nav_corrections import execute, record_approval  # noqa: E402
from mfdataindia.audit.remediation import canonical, read_document  # noqa: E402
from mfdataindia.audit.remediation_evidence import write_once  # noqa: E402
from mfdataindia.store.dsn import resolve_dsn  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    approve = sub.add_parser("approve", help="append independently reviewed finite-batch approval")
    approve.add_argument("--approval-file", type=Path, required=True)
    approve.add_argument("--restore-proof-file", type=Path, required=True)
    commands = [approve]
    for action in ("apply", "rollback"):
        command = sub.add_parser(action, help="atomic source, metrics, audit and receipt operation")
        command.add_argument("--approval-sha256", required=True)
        command.add_argument("--expected-dataset-version", type=int, required=True)
        command.add_argument("--receipt-output", type=Path, required=True)
        commands.append(command)
    for command in commands:
        command.add_argument("--proposal-file", type=Path, required=True)
        command.add_argument("--archive-directory", type=Path, required=True)
        command.add_argument("--dsn")
        command.add_argument("--confirm-database", required=True)
    args = parser.parse_args(argv)
    try:
        proposal = read_document(args.proposal_file)
        with PostgresStore(resolve_dsn(args.dsn, purpose="governed NAV correction"), use_copy=False) as store:
            with store.transaction() as conn:
                name = conn.execute("SELECT current_database() AS name").fetchone()["name"]
                if name != args.confirm_database:
                    raise ValueError("connected database differs from explicitly confirmed target")
                if args.action == "approve":
                    status = record_approval(conn, read_document(args.approval_file), proposal,
                                             read_document(args.restore_proof_file), args.archive_directory)
                    result = {"status": status, "source_writes": 0}
                else:
                    result = execute(conn, proposal, args.archive_directory,
                                     expected_version=args.expected_dataset_version,
                                     approval_sha256=args.approval_sha256, rollback=args.action == "rollback")
            # Disk failure after commit is recoverable by exact durable replay.
            if args.action != "approve":
                args.receipt_output.parent.mkdir(parents=True, exist_ok=True)
                write_once(args.receipt_output, (canonical(result) + "\n").encode())
        print(json.dumps({"status": result["status"], "receipt_sha256": result.get("sha256")}))
        return 0
    except Exception as exc:
        # Driver diagnostics can contain DSN material; print only exception class.
        print(json.dumps({"status": "OPERATION_REJECTED_OR_RECEIPT_EXPORT_FAILED", "error_type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
