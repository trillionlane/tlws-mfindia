#!/usr/bin/env python3
"""Recompute mf.fund_risk_profile from mf.nav_history.

One window-function pass over the whole NAV table (12-20s), then an upsert
per fund. Run after large NAV backfills so the category risk-reward scatter
stays current; safe to run at any time (idempotent).

    MFDATAINDIA_DSN='postgresql://...@localhost:5432/mfdataindia' \
        PYTHONPATH=src python3 scripts/refresh_risk_profile.py [--min-points 252]

--min-points drops funds with less than one year of NAV (their annualized
vol/return is not meaningful and they clutter the scatter).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mfdataindia.store.dsn import DsnError, describe_dsn, resolve_dsn  # noqa: E402
from mfdataindia.store.postgres import PostgresStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--min-points", type=int, default=252,
                    help="skip funds with fewer NAV points (default 252 = 1y)")
    args = ap.parse_args()

    try:
        dsn = resolve_dsn(args.dsn, purpose="the risk-profile refresh")
    except DsnError as exc:
        ap.error(str(exc))
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("refresh_risk_profile")
    log.info("target database: %s", describe_dsn(dsn))

    # Full computation, one pass. CAGR and max drawdown need the raw series per
    # fund, so the heavy part (returns) is SQL, the per-fund extras are Python.
    with PostgresStore(dsn) as store:
        with store.transaction() as conn, conn.cursor() as cur:
            cur.execute("""
                WITH r AS (
                    SELECT amfi_scheme_code, nav_date, nav,
                           LN(nav) - LAG(LN(nav)) OVER w AS rt
                    FROM mf.nav_history
                    WHERE nav > 0
                    WINDOW w AS (PARTITION BY amfi_scheme_code ORDER BY nav_date)
                )
                SELECT amfi_scheme_code,
                       count(*)::int AS points,
                       min(nav_date) AS first_nav_date,
                       max(nav_date) AS last_nav_date,
                       round((AVG(rt) * 252 * 100)::numeric, 4) AS annualized_return,
                       round((STDDEV(rt) * SQRT(252) * 100)::numeric, 4) AS annual_vol
                FROM r
                WHERE rt IS NOT NULL
                GROUP BY amfi_scheme_code
                HAVING count(*) >= %(min_points)s
            """, {"min_points": args.min_points})
            rows = cur.fetchall()

        log.info("computed %d fund profiles (min %d points)", len(rows), args.min_points)

        # CAGR + max drawdown per fund from the series (funds are ~1.2k points,
        # trivial in Python).
        out = []
        with store.transaction() as conn, conn.cursor() as cur:
            for r in rows:
                series = cur.execute(
                    "SELECT nav FROM mf.nav_history "
                    "WHERE amfi_scheme_code = %s AND nav > 0 ORDER BY nav_date",
                    (r["amfi_scheme_code"],)).fetchall()
                navs = [float(x["nav"]) for x in series]
                years = (r["last_nav_date"] - r["first_nav_date"]).days / 365.25
                cagr = ((navs[-1] / navs[0]) ** (1 / years) - 1) * 100 if years > 0 else 0.0
                peak, mdd = navs[0], 0.0
                for v in navs:
                    peak = max(peak, v)
                    mdd = min(mdd, v / peak - 1)
                out.append((r["amfi_scheme_code"], r["points"], r["first_nav_date"],
                            r["last_nav_date"], r["annualized_return"], r["annual_vol"],
                            round(cagr, 4), round(mdd * 100, 4)))

            cur.executemany("""
                INSERT INTO mf.fund_risk_profile
                    (amfi_scheme_code, points, first_nav_date, last_nav_date,
                     annualized_return, annual_vol, cagr, max_drawdown, refreshed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (amfi_scheme_code) DO UPDATE SET
                    points = EXCLUDED.points,
                    first_nav_date = EXCLUDED.first_nav_date,
                    last_nav_date = EXCLUDED.last_nav_date,
                    annualized_return = EXCLUDED.annualized_return,
                    annual_vol = EXCLUDED.annual_vol,
                    cagr = EXCLUDED.cagr,
                    max_drawdown = EXCLUDED.max_drawdown,
                    refreshed_at = now()
            """, out)
        log.info("upserted %d rows into mf.fund_risk_profile", len(out))
        store.refresh_dataset_summary("risk_profile_refresh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
