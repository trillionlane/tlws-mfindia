"""Before/after safety snapshot for Groww enrichment runs.

Groww is a *secondary* source, so the question after any run is not "did it
write?" but "did it write anything it was not allowed to?". This captures the
invariants as JSON and diffs two snapshots, making the check mechanical instead
of a matter of reading logs.

Invariants, and what a legal run may do to each:

  holdings_rows / funds_with_holdings   must NEVER fall. Groww writes holdings
                                        ``only_if_empty``, so a fund that already
                                        has a snapshot keeps it untouched.
  asset_class / sub_asset_class         must not fall; these hold Groww's real
                                        category hierarchy.
  pending_*                             expected to RISE during re-enrichment --
                                        that is the point of the run.
  super_category / sub_category         must stay 0 forever. Groww's
                                        ``super_category`` is the fund name, not
                                        a category. See sql/008.

    export MFDATAINDIA_DSN='postgresql://postgres:secret@localhost:5432/mfdataindia'
    PYTHONPATH=src python scripts/groww_safety_snapshot.py save reports/pre_reenrich.json
    PYTHONPATH=src python scripts/groww_safety_snapshot.py diff \
        reports/pre_reenrich.json reports/post_reenrich.json

Exit status is 0 when every invariant held, 1 when one was violated.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import psycopg
from psycopg.rows import dict_row

from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn

#: Metrics that must never decrease.
NEVER_FALL = (
    "fund_facts_rows", "holdings_rows", "funds_with_holdings",
    "asset_class_nn", "sub_asset_class_nn",
)
#: Metrics that must stay at zero.
MUST_STAY_ZERO = ("super_category_nn", "sub_category_nn")
#: Metrics expected to rise during a re-enrichment (informational).
EXPECTED_RISE = ("registrar_agent_nn", "base_expense_ratio_nn",
                 "expense_ratio_history_nn")


def snapshot(dsn: str) -> dict:
    """Capture every invariant as a flat, JSON-serialisable dict."""
    conn = psycopg.connect(dsn, row_factory=dict_row)
    one = lambda sql: conn.execute(sql).fetchone()["n"]  # noqa: E731
    live = ("FROM mf.fund_facts ff "
            "JOIN mf.v_fund_data_status v USING (amfi_scheme_code) "
            "WHERE v.in_scope AND NOT v.is_closed")
    nn = lambda col: one(f"SELECT count(*) n {live} AND ff.{col} IS NOT NULL")  # noqa: E731
    snap = {
        "fund_facts_rows": one("SELECT count(*) n FROM mf.fund_facts"),
        "holdings_rows": one("SELECT count(*) n FROM mf.fund_holdings"),
        "funds_with_holdings": one(
            "SELECT count(DISTINCT amfi_scheme_code) n FROM mf.fund_holdings"),
        "live_in_scope": one(f"SELECT count(*) n {live}"),
        "asset_class_nn": nn("asset_class"),
        "sub_asset_class_nn": nn("sub_asset_class"),
        "super_category_nn": nn("super_category"),
        "sub_category_nn": nn("sub_category"),
        "registrar_agent_nn": nn("registrar_agent"),
        "base_expense_ratio_nn": nn("base_expense_ratio"),
        "expense_ratio_history_nn": nn("expense_ratio_history"),
        "pending_gaps": one(
            "SELECT coalesce(sum(pending_expected - pending_present), 0) n "
            "FROM mf.v_fund_data_status WHERE in_scope AND NOT is_closed"),
        "headline_avg_pct": float(conn.execute(
            "SELECT round(avg(data_completeness_pct), 2) n "
            "FROM mf.v_fund_data_status WHERE in_scope AND NOT is_closed"
        ).fetchone()["n"]),
        "headline_perfect": one(
            "SELECT count(*) n FROM mf.v_fund_data_status "
            "WHERE in_scope AND NOT is_closed AND data_completeness_pct = 100"),
    }
    conn.close()
    # SUM()/AVG() come back as Decimal, which json cannot serialise.
    return {k: _plain(v) for k, v in snap.items()}


def _plain(v):
    """Coerce Decimal to int/float so the snapshot is JSON-serialisable."""
    if isinstance(v, Decimal):
        f = float(v)
        return int(f) if f.is_integer() else f
    return v


def diff(before: dict, after: dict) -> int:
    """Print the delta; return 1 if any invariant was violated."""
    violations = []
    keys = list(dict.fromkeys([*before, *after]))
    print(f"{'metric':<28}{'before':>12}{'after':>12}{'delta':>12}")
    print("-" * 64)
    for k in keys:
        b, a = before.get(k), after.get(k)
        if not isinstance(b, (int, float)) or not isinstance(a, (int, float)):
            print(f"{k:<28}{str(b):>12}{str(a):>12}")
            continue
        d = a - b
        print(f"{k:<28}{b:>12}{a:>12}{d:>+12}")
        if k in NEVER_FALL and d < 0:
            violations.append(f"{k} FELL by {-d} (must never decrease)")
        if k in MUST_STAY_ZERO and a != 0:
            violations.append(f"{k} is {a} (must stay 0 forever)")
    print("-" * 64)
    rose = [k for k in EXPECTED_RISE if after.get(k, 0) > before.get(k, 0)]
    if rose:
        print(f"pending fields that gained data: {', '.join(rose)}")
    if violations:
        print("\nINVARIANT VIOLATIONS:")
        for v in violations:
            print("  FAIL:", v)
        return 1
    print("\nAll invariants held.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("save", "diff"))
    ap.add_argument("paths", nargs="+", help="save: 1 output path; diff: before after")
    ap.add_argument("--dsn", default=None)
    args = ap.parse_args()

    if args.action == "save":
        if len(args.paths) != 1:
            ap.error("save takes exactly one output path")
        try:
            dsn = resolve_dsn(args.dsn, purpose="Groww safety snapshot")
        except DsnError as exc:
            ap.error(str(exc))
            return 2
        print(f"target database: {describe_dsn(dsn)}")
        snap = snapshot(dsn)
        out = Path(args.paths[0])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
        print(f"wrote {out} ({len(snap)} metrics)")
        for k in sorted(snap):
            print(f"    {k:<28} {snap[k]}")
        return 0

    if len(args.paths) != 2:
        ap.error("diff takes exactly two paths: before after")
    return diff(json.loads(Path(args.paths[0]).read_text()),
                json.loads(Path(args.paths[1]).read_text()))


if __name__ == "__main__":
    raise SystemExit(main())
