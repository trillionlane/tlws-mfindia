#!/usr/bin/env python3
"""Bounded source acquisition only. No DB import, legacy backfill, force or deletion."""

import argparse
import json
import time
from datetime import date
from pathlib import Path

from mfdataindia.enrichment.source_capture import (
    capture_document, capture_history, discover_absl, extract_document,
)
from mfdataindia.ingest.amfi_client import AmfiClient
from mfdataindia.enrichment.pdf_source_dispatch import require_source_authority


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    operations = parser.add_subparsers(dest="kind", required=True)
    history = operations.add_parser("history")
    history.add_argument("--from-date", type=date.fromisoformat, required=True)
    history.add_argument("--to-date", type=date.fromisoformat, required=True)
    history.add_argument("--chunk-days", type=int, default=90)
    history.add_argument("--max-requests", type=int, default=3)
    document = operations.add_parser("document")
    document.add_argument("--url", required=True)
    document.add_argument("--official-host", action="append", required=True)
    document.add_argument("--authority-reference", required=True)
    document.add_argument(
        "--use-basis",
        choices=["operator_reported_authorization", "published_permission"],
        default="operator_reported_authorization",
    )
    discovery = operations.add_parser("discover-absl")
    discovery.add_argument("--year", type=int, required=True)
    extraction = operations.add_parser("extract-pdf")
    extraction.add_argument("--document", type=Path, required=True)
    extraction.add_argument("--expected-sha", required=True)
    extraction.add_argument("--absl-period", default=None)
    extraction.add_argument("--page-start", type=int, default=1)
    extraction.add_argument("--page-end", type=int, default=None)
    args = parser.parse_args()
    if args.kind != "history":
        require_source_authority()
    if args.kind == "history":
        if (
            args.from_date > args.to_date
            or not 1 <= args.max_requests <= 12
            or not 1 <= args.chunk_days <= 90
        ):
            parser.error("ordered range and 1..12 request bound required")
        checked = 0
        for start, end, tp in AmfiClient.iter_nav_history_ranges(
            args.from_date, args.to_date, chunk_days=args.chunk_days
        ):
            if checked >= args.max_requests:
                break
            if checked:
                time.sleep(1)
            result = capture_history(args.archive, start, end, tp=tp)
            print(json.dumps(result, sort_keys=True), flush=True)
            checked += 1
        planned = (((args.to_date - args.from_date).days + args.chunk_days) // args.chunk_days) * 3
        print(
            json.dumps(
                {
                    "format": "bounded-amfi-history-capture-run-v1",
                    "planned_requests": planned,
                    "completed_requests": checked,
                    "remaining_requests": planned - checked,
                    "requested_coordinates_exhausted": checked == planned,
                    "database_imported": False,
                    "coverage_complete": False,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    elif args.kind == "discover-absl":
        print(json.dumps(discover_absl(args.archive, args.year), sort_keys=True))
    elif args.kind == "extract-pdf":
        print(json.dumps(extract_document(
            args.archive, args.document, args.expected_sha, absl_period=args.absl_period,
            page_start=args.page_start, page_end=args.page_end,
        ), sort_keys=True))
    else:
        print(
            json.dumps(
                capture_document(
                    args.archive,
                    args.url,
                    args.official_host,
                    authority_reference=args.authority_reference,
                    use_basis=args.use_basis,
                ),
                sort_keys=True,
            )
        )
