#!/usr/bin/env python3
"""Explicit opt-in schema install and finite analytics publication. No source DML."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mfdataindia.review.policy import policy, validate_policy  # noqa: E402
from mfdataindia.review.privileges import verify_runtime  # noqa: E402
from mfdataindia.review.store import MAX_CODES, capture, publish  # noqa: E402
from mfdataindia.store.dsn import resolve_dsn  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("policy", help="print the supported policy; no DB connection")
    install = commands.add_parser("install-schema", help="administrator-only; opt-in empty structures")
    install.add_argument("--confirm-database", required=True)
    generate = commands.add_parser("generate", help="explicit finite batch using the dedicated generator")
    generate.add_argument("--confirm-database", required=True)
    generate.add_argument("--codes", nargs="+", type=int, required=True)
    generate.add_argument("--expected-policy-hash", required=True)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    document = policy()
    policy_hash = validate_policy(document)
    if args.command == "policy":
        print(json.dumps({"policy_hash": policy_hash, "document": document}, indent=2, sort_keys=True))
        return 0
    if args.command == "generate":
        if not 1 <= len(args.codes) <= MAX_CODES or len(set(args.codes)) != len(args.codes) or any(not 0<c<=2147483647 for c in args.codes):
            raise ValueError("require 1-25 unique positive scheme codes")
        if args.expected_policy_hash != policy_hash:
            raise ValueError("expected policy differs from supported policy")
    dsn = resolve_dsn(purpose="opt-in verified fund review operator")
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if conn.execute("SELECT current_database() AS name").fetchone()["name"] != args.confirm_database:
            raise ValueError("database confirmation mismatch")
        if args.command == "generate":
            verify_runtime(conn, "generator")
            conn.execute("SET LOCAL statement_timeout='30s'")
            snapshots = [capture(conn,code) for code in sorted(args.codes)]
    if args.command == "install-schema":
        # Unlike scripts/migrate.py, this never invokes legacy recomputation,
        # quality publication or automatic role reconciliation.
        with PostgresStore(dsn, use_copy=False) as database:
            installed = database.apply_migrations(ROOT / "sql/optional", files=("023_verified_fund_review.sql",))
        print(json.dumps({"installed": installed, "source_writes": 0}))
    else:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            conn.execute("SET LOCAL statement_timeout='30s'")
            verify_runtime(conn, "generator")
            results = publish(conn,snapshots,document)
        print(json.dumps({"result_ids": [r["result_id"] for r in results], "policy_hash":policy_hash,
                          "source_writes":0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, psycopg.Error) as exc:
        # Never print connection/credential material or operator-supplied text.
        print("verified review command failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(2)
