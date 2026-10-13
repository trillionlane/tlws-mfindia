"""Version-bound NAV calculations. Does not overwrite published fund facts."""
from __future__ import annotations

import math

from psycopg.types.json import Jsonb

from mfdataindia.jobs.compute_metrics import annualize, compute_for_series, log_returns
from mfdataindia.store.locks import acquire_nav_data_operation_lock

METHODOLOGY_VERSION = "1"


def recompute(conn, codes: list[int]) -> dict:
    """Caller owns the transaction; publication is atomic with any correction.

    Revision includes historical NAV edits, appended observations and inception
    edits. A stale row is invisible in the API views, even when nav_last is the
    same date. NULL computations are published too, never COALESCE'd to old data.
    """
    acquire_nav_data_operation_lock(conn)
    result = {}
    for code in sorted(set(codes)):
        revision = conn.execute(
            "SELECT revision FROM mf.nav_source_revisions WHERE amfi_scheme_code=%s", (code,)
        ).fetchone()
        rows = conn.execute(
            "SELECT nav_date,nav FROM mf.nav_history WHERE amfi_scheme_code=%s ORDER BY nav_date",
            (code,),
        ).fetchall()
        if not revision or not rows:
            raise ValueError("cannot publish calculations without versioned NAV observations")
        facts = conn.execute(
            "SELECT inception_date FROM mf.fund_facts WHERE amfi_scheme_code=%s", (code,)
        ).fetchone()
        conn.execute(
            "INSERT INTO mf.fund_facts(amfi_scheme_code,source,fetched_at) VALUES(%s,'COMPUTED',now()) "
            "ON CONFLICT(amfi_scheme_code) DO NOTHING", (code,),
        )
        dates = [r["nav_date"] for r in rows]
        navs = [float(r["nav"]) for r in rows]
        # Nonpositive series cannot establish these metrics; don't skip invalid
        # observations and silently bridge a gap in a purported complete series.
        valid = all(v > 0 and math.isfinite(v) for v in navs)
        metrics = compute_for_series(dates, navs, facts["inception_date"] if facts else None) if valid else {}
        profile = None
        if valid and len(rows) >= 252:
            annual_return, annual_vol = annualize(log_returns(navs))
            years = (dates[-1] - dates[0]).days / 365.25
            peak, drawdown = navs[0], 0.0
            for value in navs:
                peak = max(peak, value)
                drawdown = min(drawdown, value / peak - 1)
            profile = {
                "points": len(rows), "first_nav_date": dates[0].isoformat(),
                "last_nav_date": dates[-1].isoformat(),
                "annualized_return": round(annual_return * 100, 4),
                "annual_vol": round(annual_vol * 100, 4),
                "cagr": round(((navs[-1] / navs[0]) ** (1 / years) - 1) * 100, 4) if years else 0,
                "max_drawdown": round(drawdown * 100, 4),
            }
        conn.execute(
            "INSERT INTO mf.nav_calculations(amfi_scheme_code,source_revision,methodology_version,as_of,metrics,profile) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(amfi_scheme_code) DO UPDATE SET "
            "source_revision=EXCLUDED.source_revision,methodology_version=EXCLUDED.methodology_version,"
            "as_of=EXCLUDED.as_of,metrics=EXCLUDED.metrics,profile=EXCLUDED.profile,computed_at=now()",
            (code, revision["revision"], METHODOLOGY_VERSION, dates[-1], Jsonb(metrics), Jsonb(profile) if profile else None),
        )
        result[str(code)] = {"source_revision": revision["revision"], "as_of": dates[-1].isoformat(), "metrics": metrics, "profile": profile}
    return result
