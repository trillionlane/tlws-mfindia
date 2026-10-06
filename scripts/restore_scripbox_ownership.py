"""Restore Scripbox-owned fund_facts values that the Groww merge overwrote.

The Groww loader used to merge with ``COALESCE(EXCLUDED.col, t.col)`` -- *incoming
wins* -- so on every shared column Groww replaced the value Scripbox had already
provided instead of only filling gaps. ``raw_payload`` still holds Scripbox's
original factsheet (Groww never writes it), so the original values are recoverable
exactly: this re-derives them with the same mapper Scripbox used and writes them
back.

Only Scripbox-owned columns are touched, and only where Scripbox actually had a
value -- ``COALESCE(incoming, current)`` means a column Scripbox left NULL keeps
whatever Groww filled in. Groww-owned columns (its ratings, holdings analysis,
fetch stamp) are left alone, as are ``source`` / ``raw_payload`` / ``fetched_at``.

    PYTHONPATH=src python scripts/restore_scripbox_ownership.py            # dry run
    PYTHONPATH=src python scripts/restore_scripbox_ownership.py --apply    # write

Takes a backup table first (``mf.fund_facts_bak_pre_restore``). Read-only unless
``--apply`` is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import psycopg
from psycopg.rows import dict_row

from mfdataindia.load.scripbox_to_store import (
    FUND_FACTS_COLUMNS, FUND_FACTS_TYPES, factsheet_to_facts,
)
from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn

# Scripbox-owned columns eligible for restore. The excluded four are not value
# columns: the pk, the provenance tag (already 'SCRIPBOX', the loader never
# updates it), the retained payload itself, and the fetch stamp (Scripbox's
# original timestamp is not recoverable -- the mapper stamps load time, not
# payload time -- so overwriting it with "now" would be worse than leaving it).
RESTORE_COLUMNS: tuple[str, ...] = tuple(
    c for c in FUND_FACTS_COLUMNS
    if c not in ("amfi_scheme_code", "source", "raw_payload", "fetched_at")
)

# Columns compared numerically rather than as text, so a pure representation
# difference ('10' vs '10.0000') is not counted as a change.
_NUMERIC = {
    "aum", "expense_ratio", "face_value", "source_nav", "return_1day",
    "return_3month", "return_6month", "return_1year", "return_2year",
    "return_3year", "return_4year", "return_5year", "return_7year",
    "return_10year", "return_since_launch", "min_initial_investment_amount",
    "min_subsequent_investment_amount", "min_withdrawal_amount",
    "min_investment_multiples",
}
_BOOL = {
    "openended", "is_active_status", "is_investable", "is_purchase_allowed",
    "is_withdrawal_allowed", "is_sip_allowed", "is_stp_allowed",
    "is_swp_allowed", "is_switch_in_allowed", "is_switch_out_allowed",
    "is_nfo",
}
# jsonb columns: psycopg hands these back as parsed dict/list while the mapper
# emits a JSON string, so they must be compared structurally or every one of them
# looks changed. Groww writes none of these except fund_manager_details.
_JSONB = {
    "composition", "sectorwise_holding", "asset_holding", "holdings_maturity",
    "exit_load", "sip", "stp", "swp", "stats_variables", "category_return",
    "fund_manager", "fund_variant", "fund_manager_details",
}
# Temporal columns: psycopg returns date/datetime objects while the mapper emits
# ISO strings, so '2026-09-07T11:57:20+00:00' vs datetime(...11:57:20+00) would
# otherwise read as a change. Compare the actual instants/dates instead.
_DATE = {"returns_as_on_date", "inception_date", "launch_date", "source_nav_date"}
_TS = {"source_updated_at"}


def _num(v: object) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _temporal(cur: object, want: object, ts: bool) -> bool:
    """Compare a stored date/datetime against the mapper's ISO string."""
    from datetime import date, datetime
    try:
        w = datetime.fromisoformat(str(want).replace("Z", "+00:00"))
    except ValueError:
        return str(cur) == str(want)
    c = cur if isinstance(cur, datetime) else (
        datetime(cur.year, cur.month, cur.day, tzinfo=w.tzinfo)
        if isinstance(cur, date) else None)
    if c is None:
        try:
            c = datetime.fromisoformat(str(cur).replace("Z", "+00:00"))
        except ValueError:
            return str(cur) == str(want)
    if not ts:                                   # date column: compare the day
        return c.date() == w.date()
    if c.tzinfo is None:
        return c == w.replace(tzinfo=None)
    return c == w.astimezone(c.tzinfo)


def _same(col: str, cur: object, want: object) -> bool:
    """True when the stored value already equals the Scripbox value."""
    if want is None:
        return True                      # nothing to restore
    if cur is None:
        return False
    if col in _NUMERIC:
        a, b = _num(cur), _num(want)
        return a is not None and b is not None and abs(a - b) <= Decimal("0.0001")
    if col in _BOOL:
        return str(cur).lower() == str(want).lower()
    if col in _JSONB:
        try:
            a = json.loads(want) if isinstance(want, str) else want
            return a == (cur if not isinstance(cur, str) else json.loads(cur))
        except (ValueError, TypeError):
            return str(cur) == str(want)
    if col in _DATE or col in _TS:
        return _temporal(cur, want, ts=col in _TS)
    return str(cur).strip() == str(want).strip()


def _payload(raw: object) -> dict:
    return json.loads(raw) if isinstance(raw, str) else raw


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="write the restore (default: dry run, report only)")
    ap.add_argument("--limit", type=int, default=None,
                    help="restrict to N funds (debugging)")
    args = ap.parse_args()

    # No DSN default on purpose: this script writes, and restoring Scripbox
    # ownership into the wrong database is not recoverable. resolve_dsn() also
    # refuses the PGlite port, which predates the Groww columns entirely.
    try:
        dsn = resolve_dsn(purpose="the Scripbox ownership restore")
    except DsnError as exc:
        ap.error(str(exc))
        return 2
    print(f"target database: {describe_dsn(dsn)}")
    conn = psycopg.connect(dsn, connect_timeout=30, row_factory=dict_row,
                           autocommit=not args.apply)
    cur = conn.cursor()

    sel = ", ".join(["amfi_scheme_code", "raw_payload", *RESTORE_COLUMNS])
    sql = (f"SELECT {sel} FROM mf.fund_facts "
           "WHERE groww_fetched_at IS NOT NULL AND raw_payload IS NOT NULL")
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = cur.execute(sql).fetchall()
    print(f"candidates: {len(rows)} Groww-enriched funds retaining a Scripbox payload")

    changed = Counter()
    plans: list[tuple] = []
    for r in rows:
        try:
            fs = factsheet_to_facts(_payload(r["raw_payload"]))
        except Exception as exc:                            # noqa: BLE001
            print(f"  ! {r['amfi_scheme_code']}: payload unusable ({exc})")
            continue
        if fs is None:
            continue
        want = dict(zip(FUND_FACTS_COLUMNS, fs))
        diffs = [c for c in RESTORE_COLUMNS if not _same(c, r[c], want.get(c))]
        if not diffs:
            continue
        for c in diffs:
            changed[c] += 1
        plans.append(tuple(want.get(c) for c in RESTORE_COLUMNS)
                     + (r["amfi_scheme_code"],))

    print(f"funds needing restore: {len(plans)}")
    if changed:
        print("\nvalues to restore (Scripbox <- current):")
        for col, n in changed.most_common():
            print(f"  {col:34} {n:>6}")

    if not args.apply:
        print("\nDRY RUN -- no changes written. Re-run with --apply to restore.")
        return 0
    if not plans:
        print("\nnothing to restore.")
        return 0

    cur.execute("SELECT to_regclass('mf.fund_facts_bak_pre_restore') AS t")
    if cur.fetchone()["t"] is None:
        cur.execute("CREATE TABLE mf.fund_facts_bak_pre_restore AS TABLE mf.fund_facts")
        print("\nbackup table mf.fund_facts_bak_pre_restore created")

    set_clause = ", ".join(
        f"{c} = COALESCE(NULLIF(%s, '')::{FUND_FACTS_TYPES.get(c, 'text')}, {c})"
        for c in RESTORE_COLUMNS)
    stmt = f"UPDATE mf.fund_facts SET {set_clause} WHERE amfi_scheme_code = %s"
    cur.executemany(stmt, plans)
    conn.commit()
    print(f"\nrestored {len(plans)} funds "
          f"({sum(changed.values())} column values) -- committed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
