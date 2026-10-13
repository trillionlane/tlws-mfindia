#!/usr/bin/env python3
"""DATA-02 operator tooling. No production NAV apply or rollback action exists."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.audit.remediation import (  # noqa: E402
    DISPOSITIONS,
    canonical,
    export_register,
    read_document,
    record_decision,
    record_proposal,
    register_snapshots,
    seal,
)
from mfdataindia.audit.remediation_evidence import (  # noqa: E402
    archive_evidence,
    build_proposal,
    validate_evidence_reference,
    write_once,
)
from mfdataindia.audit.remediation_rehearsal import rehearse, validate_proposal  # noqa: E402
from mfdataindia.store.dsn import resolve_dsn  # noqa: E402
from mfdataindia.store.locks import acquire_nav_data_operation_lock  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export-register", help="read-only current certified case inventory")
    export.add_argument("--expected-dataset-version", type=int, required=True)
    export.add_argument("--expected-findings-sha256", required=True)
    archive = sub.add_parser("archive-evidence", help="archive operator-supplied original bytes")
    archive.add_argument("--raw-file", type=Path, required=True)
    archive.add_argument(
        "--source", choices=["AMFI_NAV_HISTORY", "AMC_OFFICIAL_DOCUMENT"], required=True
    )
    archive.add_argument("--url", required=True)
    archive.add_argument("--fetched-at", required=True)
    archive.add_argument("--official-host")
    archive.add_argument("--issuer-reference")
    archive.add_argument("--archive-directory", type=Path, required=True)
    register = sub.add_parser("record-register", help="append case snapshots only")
    register.add_argument("--register-file", type=Path, required=True)
    decision = sub.add_parser("prepare-decision", help="prepare a human review statement")
    decision.add_argument("--register-file", type=Path, required=True)
    decision.add_argument("--case-id", required=True)
    decision.add_argument("--disposition", choices=sorted(DISPOSITIONS), required=True)
    decision.add_argument("--reason", required=True)
    decision.add_argument("--reviewer-reference", required=True)
    decision.add_argument("--supersedes-sha256", default=None)
    decision.add_argument("--evidence-file", type=Path, action="append", default=[])
    record = sub.add_parser(
        "record-decision", help="append decision; does not approve source writes"
    )
    record.add_argument("--decision-file", type=Path, required=True)
    record.add_argument("--archive-directory", type=Path, required=True)
    proposal = sub.add_parser("propose", help="read-only exact old/new NAV proposal")
    proposal.add_argument("--register-file", type=Path, required=True)
    proposal.add_argument("--case-id", required=True)
    proposal.add_argument("--decision-file", type=Path, required=True)
    proposal.add_argument("--evidence-file", type=Path, required=True)
    proposal.add_argument("--archive-directory", type=Path, required=True)
    proposal.add_argument("--expected-dataset-version", type=int, required=True)
    record_plan = sub.add_parser("record-proposal", help="append proposal; does not apply it")
    record_plan.add_argument("--proposal-file", type=Path, required=True)
    record_plan.add_argument("--archive-directory", type=Path, required=True)
    trial = sub.add_parser("rehearse", help="scratch database ONLY; always rolls back")
    trial.add_argument("--proposal-file", type=Path, required=True)
    trial.add_argument("--archive-directory", type=Path, required=True)
    trial.add_argument("--scratch-dsn", required=True)
    trial.add_argument("--confirm-scratch-database", required=True)
    trial.add_argument("--simulate-failure-after", type=int)
    for command in (export, register, record, proposal, record_plan):
        command.add_argument("--dsn", help="default: MFDATAINDIA_DSN; never logged")
    for command in (export, decision, proposal):
        command.add_argument("--output", type=Path, required=True)
    return root


def _case(args) -> dict:
    register = read_document(args.register_file)
    matches = [case for case in register["cases"] if case["case_id"] == args.case_id]
    if len(matches) != 1:
        raise ValueError("case must occur exactly once in the register")
    return matches[0]


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == "archive-evidence":
            result = archive_evidence(
                args.raw_file,
                args.archive_directory,
                source=args.source,
                url=args.url,
                fetched_at=args.fetched_at,
                official_host=args.official_host,
                issuer_reference=args.issuer_reference,
            )
        elif args.action == "prepare-decision":
            result = seal(
                {
                    "case_snapshot_sha256": _case(args)["sha256"],
                    "supersedes_sha256": args.supersedes_sha256,
                    "disposition": args.disposition,
                    "reason": args.reason,
                    "reviewer_reference": args.reviewer_reference,
                    "evidence": [read_document(path) for path in args.evidence_file],
                }
            )
        elif args.action == "rehearse":
            result = rehearse(
                args.scratch_dsn,
                read_document(args.proposal_file),
                args.archive_directory,
                confirm_scratch_database=args.confirm_scratch_database,
                simulate_failure_after=args.simulate_failure_after,
            )
        else:
            dsn = resolve_dsn(args.dsn, purpose="NAV remediation review")
            with PostgresStore(dsn, use_copy=False) as store, store.transaction() as conn:
                if args.action == "export-register":
                    result = export_register(
                        conn,
                        expected_dataset_version=args.expected_dataset_version,
                        expected_findings_sha256=args.expected_findings_sha256,
                    )
                elif args.action == "record-register":
                    added = register_snapshots(conn, read_document(args.register_file))
                    result = {"status": "CASE_SNAPSHOTS_RECORDED", "added": added}
                elif args.action == "record-decision":
                    decision = read_document(args.decision_file)
                    for ref in decision["evidence"]:
                        validate_evidence_reference(ref)
                        raw = (args.archive_directory / f"{ref['raw_sha256']}.bin").read_bytes()
                        if hashlib.sha256(raw).hexdigest() != ref["raw_sha256"]:
                            raise ValueError("archived decision evidence changed")
                    result = {"status": record_decision(conn, decision), "source_writes": 0}
                elif args.action == "record-proposal":
                    proposal = read_document(args.proposal_file)
                    validate_proposal(proposal, args.archive_directory)
                    result = {"status": record_proposal(conn, proposal), "source_writes": 0}
                else:
                    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                    conn.execute("SET LOCAL statement_timeout = '120s'")
                    acquire_nav_data_operation_lock(conn)
                    result = build_proposal(
                        conn,
                        case=_case(args),
                        decision=read_document(args.decision_file),
                        reference=read_document(args.evidence_file),
                        archive=args.archive_directory,
                        expected_dataset_version=args.expected_dataset_version,
                    )
        if getattr(args, "output", None):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_once(args.output, (canonical(result) + "\n").encode())
            print(json.dumps({"status": "ARTIFACT_PREPARED", "sha256": result["sha256"]}))
        else:
            print(canonical(result))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"remediation review rejected: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
