"""Compute missing risk/return metrics from our own NAV series.

Fills NULL ``fund_facts`` fields (``sharpe_ratio``, ``std_deviation`` and the
``return_*`` columns) for in-scope live funds from ``mf.nav_history`` — the
AMFI-sourced daily NAV. Conventions are identical to the API's on-the-fly
analytics (:func:`mfdataindia.api.queries.fund_analytics` and ``returns``), so
a stored value always equals what the API already computes for the same fund:

* daily log returns over the fund's full history; annualised ``*252`` /
  ``*sqrt(252)`` (sample stdev, n-1)
* Sharpe with a 6.5% annual risk-free rate (``queries.RISK_FREE_ANNUAL``),
  stored as a ratio; std deviation stored in percent (the columns' units)
* trailing returns: latest NAV at or just before
  ``last_date - round(months * 30.44) days`` (1-week uses 7 days)
* ``return_since_launch`` only where the fund's (source-known) inception falls
  inside our NAV data — a fund that started before the series would be
  mislabelled, so it stays NULL

Invariant: **fill-if-missing only.** Every write is ``SET col = COALESCE(col,
%s)`` (UPDATE) or a fresh INSERT (``source='COMPUTED'``) for funds with no
facts row yet — a non-NULL value can never be overwritten. Every filled
(fund, field) is recorded in ``mf.computed_fields_log`` (method, window,
as-of date); requires migration 010.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import date, timedelta
from typing import Any, Optional

from mfdataindia.store.postgres import PostgresStore

log = logging.getLogger("compute_metrics")

#: Same constants as api.queries, so stored == on-the-fly values.
RISK_FREE_ANNUAL = 0.065
_TRADING_DAYS = 252.0
#: Same too-short threshold as queries.fund_analytics.
MIN_POINTS = 30

#: Fields this job may fill (whitelist; never built from external input).
TARGET_FIELDS = (
    "sharpe_ratio", "std_deviation",
    "return_1day", "return_1week", "return_1month", "return_3month",
    "return_6month", "return_9month", "return_1year", "return_2year",
    "return_3year", "return_4year", "return_5year", "return_7year",
    "return_10year", "return_since_launch",
)


def log_returns(navs: list[float]) -> list[float]:
    """Daily log returns; skips pairs where either NAV is non-positive."""
    return [math.log(navs[i] / navs[i - 1]) for i in range(1, len(navs))
            if navs[i - 1] > 0 and navs[i] > 0]


def annualize(rets: list[float]) -> tuple[float, float]:
    """(mean, stdev) of daily returns -> (annualized return, annualized vol)."""
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return mean * _TRADING_DAYS, math.sqrt(var) * math.sqrt(_TRADING_DAYS)


def sharpe_of(ann_return: float, ann_vol: float) -> Optional[float]:
    if ann_vol <= 0:
        return None
    return (ann_return - RISK_FREE_ANNUAL) / ann_vol


def trailing_return_pct(dates: list[date], navs: list[float], months: float) -> Optional[float]:
    """Trailing return over ~``months`` months (30.44 days each).

    Base = latest NAV at or before the cutoff; None where the series is too
    short to contain one (young funds). Mirrors queries.returns.
    """
    last_date, last_nav = dates[-1], navs[-1]
    if last_nav <= 0:
        return None
    cutoff = last_date - timedelta(days=int(round(months * 30.44)))
    for d, v in zip(reversed(dates), reversed(navs)):
        if d <= cutoff:
            if v <= 0:
                return None
            return (last_nav / v - 1.0) * 100.0
    return None


def one_day_return_pct(navs: list[float]) -> Optional[float]:
    if len(navs) < 2 or navs[-1] <= 0 or navs[-2] <= 0:
        return None
    return (navs[-1] / navs[-2] - 1.0) * 100.0


def since_launch_return_pct(dates: list[date], navs: list[float],
                            inception: Optional[date]) -> Optional[float]:
    """Return from the first NAV in our data, only when that first NAV is the
    true launch (inception on/after it). None otherwise — see module docstring.
    """
    if inception is None or inception < dates[0]:
        return None
    if navs[0] <= 0 or navs[-1] <= 0:
        return None
    return (navs[-1] / navs[0] - 1.0) * 100.0


def compute_for_series(dates: list[date], navs: list[float],
                       inception: Optional[date]) -> dict[str, Optional[float]]:
    """All target fields for one fund's series (None where not computable).

    Returns are rounded to 2dp — the same display precision the API uses, so
    the stored value equals the on-the-fly value to the last digit.
    """
    out: dict[str, Optional[float]] = {f: None for f in TARGET_FIELDS}
    rets = log_returns(navs)
    if rets:
        ann_return, ann_vol = annualize(rets)
        out["std_deviation"] = round(ann_vol * 100, 2)
        s = sharpe_of(ann_return, ann_vol)
        out["sharpe_ratio"] = round(s, 2) if s is not None else None
    out["return_1day"] = round(one_day_return_pct(navs), 2) \
        if one_day_return_pct(navs) is not None else None
    wk = trailing_return_pct(dates, navs, 7 / 30.44)
    out["return_1week"] = round(wk, 2) if wk is not None else None
    for col, months in (("return_1month", 1), ("return_3month", 3),
                        ("return_6month", 6), ("return_9month", 9),
                        ("return_1year", 12), ("return_2year", 24),
                        ("return_3year", 36), ("return_4year", 48),
                        ("return_5year", 60), ("return_7year", 84),
                        ("return_10year", 120)):
        v = trailing_return_pct(dates, navs, months)
        out[col] = round(v, 2) if v is not None else None
    v = since_launch_return_pct(dates, navs, inception)
    out["return_since_launch"] = round(v, 2) if v is not None else None
    return out


_METHODS = {
    "sharpe_ratio": ("LOG_RETURNS_FULL_HISTORY_ANNUAL_252_RF6.5", "FULL_HISTORY"),
    "std_deviation": ("ANNUALIZED_VOL_FULL_HISTORY_252", "FULL_HISTORY"),
    "return_1day": ("TRAILING_NAV_WINDOW", "1D"),
    "return_1week": ("TRAILING_NAV_WINDOW", "7D"),
    "return_1month": ("TRAILING_NAV_WINDOW", "1M"),
    "return_3month": ("TRAILING_NAV_WINDOW", "3M"),
    "return_6month": ("TRAILING_NAV_WINDOW", "6M"),
    "return_9month": ("TRAILING_NAV_WINDOW", "9M"),
    "return_1year": ("TRAILING_NAV_WINDOW", "1Y"),
    "return_2year": ("TRAILING_NAV_WINDOW", "2Y"),
    "return_3year": ("TRAILING_NAV_WINDOW", "3Y"),
    "return_4year": ("TRAILING_NAV_WINDOW", "4Y"),
    "return_5year": ("TRAILING_NAV_WINDOW", "5Y"),
    "return_7year": ("TRAILING_NAV_WINDOW", "7Y"),
    "return_10year": ("TRAILING_NAV_WINDOW", "10Y"),
    "return_since_launch": ("NAV_FIRST_TO_LAST", "SINCE_LAUNCH"),
}


def _fill_fund(cur, code: int, current: Optional[Any],
               to_fill: dict[str, float], as_of: date) -> None:
    """Fill-if-missing write for one fund (row + provenance log)."""
    cols = ", ".join(to_fill)
    if current is None:
        # No facts row yet: create one carrying only computed values.
        # ON CONFLICT DO NOTHING — never clobber a row another process created.
        cur.execute(
            f"""
            INSERT INTO mf.fund_facts (amfi_scheme_code, source, fetched_at, {cols})
            VALUES (%s, 'COMPUTED', now(), {', '.join(['%s'] * len(to_fill))})
            ON CONFLICT (amfi_scheme_code) DO NOTHING
            """,
            [code] + list(to_fill.values()))
    else:
        cur.execute(
            f"""
            UPDATE mf.fund_facts
            SET {', '.join(f'{c} = COALESCE({c}, %s)' for c in to_fill)}
            WHERE amfi_scheme_code = %s
            """,
            list(to_fill.values()) + [code])
    for field, value in to_fill.items():
        method, window = _METHODS[field]
        cur.execute(
            """
            INSERT INTO mf.computed_fields_log
                (amfi_scheme_code, field, value, method, window_label, as_of)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (amfi_scheme_code, field) DO UPDATE SET
                value = EXCLUDED.value, method = EXCLUDED.method,
                window_label = EXCLUDED.window_label, as_of = EXCLUDED.as_of,
                computed_at = now()
            """,
            (code, field, value, method, window, as_of))


def compute_fund_metrics(
    store: PostgresStore,
    *,
    codes: Optional[list[int]] = None,
    dry_run: bool = False,
    max_funds: Optional[int] = None,
    min_points: int = MIN_POINTS,
) -> dict[str, Any]:
    """Fill the NULL target fields for in-scope live funds.

    ``codes`` restricts the run (default: all in-scope, live, active funds).
    ``dry_run`` computes and reports without writing.
    """
    report: dict[str, Any] = {
        "funds_scanned": 0, "skipped_too_short": 0, "funds_filled": 0,
        "funds_no_gaps": 0, "rows_inserted": 0, "filled_by_field": {},
        "dry_run": dry_run,
        "started_at": time.monotonic(),
    }
    with store.connect().cursor() as cur:
        if codes:
            target = [int(c) for c in codes]
        else:
            target = [r["amfi_scheme_code"] for r in cur.execute(
                "SELECT amfi_scheme_code FROM mf.funds "
                "WHERE in_scope AND NOT is_defunct AND is_active "
                "ORDER BY amfi_scheme_code").fetchall()]

    for code in target:
        if max_funds is not None and report["funds_scanned"] >= max_funds:
            break
        report["funds_scanned"] += 1
        with store.connect().cursor() as cur:
            rows = cur.execute(
                "SELECT nav_date, nav FROM mf.nav_history "
                "WHERE amfi_scheme_code = %s ORDER BY nav_date", (code,)
            ).fetchall()
            current = cur.execute(
                "SELECT inception_date, " + ", ".join(TARGET_FIELDS) +
                " FROM mf.fund_facts WHERE amfi_scheme_code = %s", (code,)
            ).fetchone()

        if len(rows) < min_points:
            report["skipped_too_short"] += 1
            continue
        dates = [r["nav_date"] for r in rows]
        navs = [float(r["nav"]) for r in rows]
        computed = compute_for_series(
            dates, navs, current["inception_date"] if current else None)
        to_fill = {f: v for f, v in computed.items() if v is not None
                   and (current is None or current[f] is None)}
        if not to_fill:
            report["funds_no_gaps"] += 1
            continue
        report["funds_filled"] += 1
        if current is None:
            report["rows_inserted"] += 1
        for f, v in to_fill.items():
            report["filled_by_field"][f] = report["filled_by_field"].get(f, 0) + 1
        if dry_run:
            continue
        with store.transaction() as conn, conn.cursor() as cur:
            _fill_fund(cur, code, current, to_fill, dates[-1])

    report["elapsed_s"] = round(time.monotonic() - report["started_at"], 1)
    log.info("compute_metrics: %s", report)
    return report
