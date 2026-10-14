#!/usr/bin/env python3
"""Operator-only discovery/import; never fetches on import or exposes a public writer."""

import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from mfdataindia.enrichment import evidence, operations, schemas, store
from mfdataindia.store.dsn import resolve_dsn
from mfdataindia.review.rollout import CONNECTION


def bounded_file(path, maximum):
    if not path.is_file():
        raise ValueError("regular evidence file required")
    with path.open("rb") as source:
        content = source.read(maximum + 1)
    if not 0 < len(content) <= maximum:
        raise ValueError("evidence file size exceeds bound")
    return content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    discover = sub.add_parser(
        "discover", help="parse an acquired official AMFI directory; no document imports"
    )
    discover.add_argument("--directory", type=Path, required=True)
    ingest = sub.add_parser(
        "import", help="append one locally acquired official document and bounded cohort"
    )
    ingest.add_argument("--manifest", type=Path, required=True)
    ingest.add_argument("--document", type=Path, required=True)
    sub.add_parser("install", help="explicit owner-only opt-in schema/policy installation")
    args = parser.parse_args()
    if args.operation == "discover":
        print(
            json.dumps(
                evidence.directory(bounded_file(args.directory, evidence.MAX_BYTES)), sort_keys=True
            )
        )
        return
    if args.operation == "import":
        intake = schemas.Intake.model_validate_json(bounded_file(args.manifest, 16384))
        content = bounded_file(args.document, evidence.MAX_BYTES)
    dsn = resolve_dsn(None, purpose="official enrichment operator")
    target = conninfo_to_dict(dsn)
    if (
        target.get("host") != "/cloudsql/" + CONNECTION
        or target.get("hostaddr")
        or target.get("dbname") != "mfdataindia"
        or not target.get("password")
        or target.get("user") != ("mfdata_admin" if args.operation == "install" else store.ROLES[0])
    ):
        raise ValueError("explicit pinned DEV database and dedicated principal required")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute(
            "SET TRANSACTION ISOLATION LEVEL "
            + ("REPEATABLE READ" if args.operation == "install" else "READ COMMITTED")
        )
        conn.execute("SET LOCAL statement_timeout='120s'")
        conn.execute("SET LOCAL lock_timeout='5s'")
        result = (
            operations.install(conn, Path(__file__).resolve().parents[1])
            if args.operation == "install"
            else store.append(conn, intake, content)
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("official enrichment operation failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
