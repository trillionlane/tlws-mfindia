"""Read-only queries for the API.

These functions take a psycopg connection (supplied by the pool) and return plain
dicts/lists. No writes happen here; the API is a pure read layer over the store.

Serialisation note: NAV comes back as ``Decimal`` (NUMERIC(18,4)). It is converted
to ``float`` only at the JSON boundary — never stored as float anywhere upstream.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

#: Allowed sort keys -> SQL expression. A whitelist, never string-interpolated
#: from user input, so no injection is possible.
_SORT = {
    "name": "f.scheme_name",
    "nav": "lt.nav DESC NULLS LAST",
    "aum": "ff.aum DESC NULLS LAST",
    "expense": "ff.expense_ratio ASC NULLS LAST",
    "return_5y": "ff.return_5year DESC NULLS LAST",
}

#: Sort keys for the family query, whose picked CTE is aliased ``s`` and whose
#: fund_facts join is ``ff``. (A naive f.->s. string replacement would corrupt
#: ``ff.aum`` into ``sf.aum``.)
_SORT_FAMILY = {
    "name": "s.scheme_name",
    "nav": "lt.nav DESC NULLS LAST",
    "aum": "ff.aum DESC NULLS LAST",
    "expense": "ff.expense_ratio ASC NULLS LAST",
    "return_5y": "ff.return_5year DESC NULLS LAST",
}

_STATS_SQL = """
    SELECT schemes_total, in_scope_total, in_scope_live, amcs, categories,
           nav_rows, nav_first, nav_last, enrichment_pct, dataset_version,
           source_content_hash, refreshed_at
    FROM mf.dataset_summary
    WHERE singleton
"""


def _f(v: Any) -> Optional[float]:
    return None if v is None else float(v)


#: Fund-level factsheet fields. A facts row is "usable" — i.e. the scheme code
#: actually has factsheet data of its own — when at least one of these is set.
#: Everything else (returns, Sharpe, exit load, ...) is per-code enrichment and
#: does not count as evidence that the factsheet covered this code. Both
#: benchmark representations are listed so the detail and batch endpoints apply
#: the identical "has its own facts" predicate (see :func:`_facts_usable_sql`
#: and :func:`_facts_are_usable`) — previously detail checked ``benchmark``
#: while batch checked ``benchmark_name`` and could disagree on a row carrying
#: only one representation.
_FACTS_IDENTITY_FIELDS = (
    "aum",
    "expense_ratio",
    "benchmark",
    "benchmark_name",
    "fund_manager_name",
    "inception_date",
)


def _facts_usable_sql(alias: str) -> str:
    """SQL fragment true when a facts row has at least one identity field.

    Generated from ``_FACTS_IDENTITY_FIELDS`` so every endpoint (detail and
    batch) shares one definition of "this scheme code has factsheet data of its
    own". Never string-interpolated from user input.
    """
    return " OR ".join(f"{alias}.{k} IS NOT NULL" for k in _FACTS_IDENTITY_FIELDS)

#: Every fund_facts column served by fund_detail. Single source of truth for the
#: column set so the SELECT, the "all fields" shape, and the family-safe
#: allowlist can never drift apart.
_ALL_FACTS_FIELDS = (
    "aum", "expense_ratio", "face_value", "inception_date", "sebi_category_name",
    "asset_class", "sub_asset_class", "taxability",
    "return_1day", "return_3month", "return_6month", "return_1year", "return_3year",
    "return_5year", "return_10year", "return_since_launch",
    "min_initial_investment_amount", "min_subsequent_investment_amount",
    "is_sip_allowed", "status", "transaction_status",
    "benchmark", "benchmark_name", "fund_manager_name", "risk_level",
    "base_expense_ratio", "registrar_agent", "expense_ratio_history",
    "crisil_rating", "sub_type", "exit_load_value", "exit_load",
    "lock_in_period", "portfolio_turnover", "return_1week", "return_1month",
    "return_9month", "sharpe_ratio", "beta", "std_deviation", "risk_rating",
    "holdings_analysis", "holdings_maturity", "category_return",
)

_FACTS_SELECT = (
    "SELECT " + ", ".join(_ALL_FACTS_FIELDS)
    + " FROM mf.fund_facts WHERE amfi_scheme_code = %(code)s"
)

#: Fields safe to borrow from a sibling. These are the fund-level factsheet
#: attributes that are invariant across a scheme family (AUM, expense ratio,
#: benchmark, manager, inception, classification, ratings, holdings profile).
#: Deliberately EXCLUDED are per-scheme fields that can differ even between
#: same-plan re-issues: SIP eligibility (is_sip_allowed), status /
#: transaction_status, exit load (exit_load, exit_load_value, lock_in_period),
#: published returns (return_*, category_return), risk ratios (sharpe_ratio,
#: beta, std_deviation, risk_rating), and expense_ratio_history. A borrowing
#: code keeps its own values for those and inherits only this allowlist.
_FAMILY_SAFE_FACTS_FIELDS = (
    "aum", "expense_ratio", "face_value", "inception_date",
    "sebi_category_name", "asset_class", "sub_asset_class", "taxability",
    "min_initial_investment_amount", "min_subsequent_investment_amount",
    "benchmark", "benchmark_name", "fund_manager_name", "risk_level",
    "base_expense_ratio", "registrar_agent", "crisil_rating", "sub_type",
    "portfolio_turnover", "holdings_analysis", "holdings_maturity",
)

#: Best facts-bearing sibling of the requested scheme. A sibling must be a
#: re-issue of the *same* plan/option/periodicity within the *same* AMC and
#: scheme classification, not merely share ``group_key``: the key is a
#: documented heuristic (see load.normalise.base_scheme_key) and can collide
#: across AMCs, and borrowing across plan/option/periodicity would leak
#: variant-specific facts (a Regular expense ratio shown on a Direct scheme,
#: or Monthly IDCW data shown on a Quarterly scheme). Among qualifying
#: re-issues the lowest AMFI code (oldest tranche) is canonical.
_SIBLING_SOURCE_SQL = f"""
    SELECT f.amfi_scheme_code AS source_code
    FROM mf.fund_variants v1
    JOIN mf.funds f1 ON f1.amfi_scheme_code = v1.amfi_scheme_code
    JOIN mf.fund_variants v2
         ON v2.group_key = v1.group_key
        AND v2.amfi_scheme_code <> v1.amfi_scheme_code
    JOIN mf.funds f ON f.amfi_scheme_code = v2.amfi_scheme_code
    JOIN mf.fund_facts ff ON ff.amfi_scheme_code = v2.amfi_scheme_code
    WHERE v1.amfi_scheme_code = %(code)s
      AND f.plan_type = f1.plan_type
      AND f.option_type = f1.option_type
      AND f.amc_id = f1.amc_id
      AND f.scheme_type = f1.scheme_type
      AND f.scheme_category = f1.scheme_category
      AND f.periodicity IS NOT DISTINCT FROM f1.periodicity
      AND ({_facts_usable_sql("ff")})
    ORDER BY f.amfi_scheme_code
    LIMIT 1
"""

#: Batched variant of _SIBLING_SOURCE_SQL: one best source per requested code.
_BATCH_SIBLING_SOURCE_SQL = f"""
    SELECT for_code, source_code FROM (
        SELECT v1.amfi_scheme_code AS for_code,
               f.amfi_scheme_code AS source_code,
               row_number() OVER (PARTITION BY v1.amfi_scheme_code
                  ORDER BY f.amfi_scheme_code) AS rn
        FROM mf.fund_variants v1
        JOIN mf.funds f1 ON f1.amfi_scheme_code = v1.amfi_scheme_code
        JOIN mf.fund_variants v2
             ON v2.group_key = v1.group_key
            AND v2.amfi_scheme_code <> v1.amfi_scheme_code
        JOIN mf.funds f ON f.amfi_scheme_code = v2.amfi_scheme_code
        JOIN mf.fund_facts ff ON ff.amfi_scheme_code = v2.amfi_scheme_code
        WHERE v1.amfi_scheme_code = ANY(%(codes)s::int[])
          AND f.plan_type = f1.plan_type
          AND f.option_type = f1.option_type
          AND f.amc_id = f1.amc_id
          AND f.scheme_type = f1.scheme_type
          AND f.scheme_category = f1.scheme_category
          AND f.periodicity IS NOT DISTINCT FROM f1.periodicity
          AND ({_facts_usable_sql("ff")})
    ) t WHERE rn = 1
"""


#: Facts columns served by /api/funds/batch; the fallback fills exactly these.
_BATCH_FACTS_KEYS = (
    "aum", "expense_ratio", "base_expense_ratio", "return_5year", "sharpe_ratio",
    "beta", "risk_level", "fund_manager_name", "benchmark_name", "inception_date",
)


def _facts_are_usable(facts: Optional[dict]) -> bool:
    """True when a facts row carries at least one fund-identity field."""
    if not facts:
        return False
    return any(facts.get(k) is not None for k in _FACTS_IDENTITY_FIELDS)


def _facts_from_sibling(conn, code: int) -> tuple[Optional[dict], Optional[int]]:
    """Resolve facts for a code that has none of its own.

    Schemes sharing a ``mf.fund_variants.group_key`` are re-issues of one
    portfolio. AMC factsheets only attribute facts to some of the codes in a
    group, so a code without fund-level facts borrows a sibling's facts. A
    sibling qualifies only if it is a re-issue of the same plan/option/
    periodicity within the same AMC and scheme classification (``group_key``
    alone is a heuristic and can collide across AMCs, and cross-plan/option/
    periodicity borrowing would leak variant-specific facts). Among qualifying
    re-issues the lowest AMFI code is canonical. Only the family-safe fields
    (see :data:`_FAMILY_SAFE_FACTS_FIELDS`) are returned for borrowing; the
    code's own non-NULL facts always win in :func:`fund_detail`.

    Returns ``(facts_row, source_code)``; both ``None`` when no qualifying
    sibling carries usable facts.
    """
    src = conn.execute(_SIBLING_SOURCE_SQL, {"code": code}).fetchone()
    if not src:
        return None, None
    source_code = int(src["source_code"])
    row = conn.execute(_FACTS_SELECT, {"code": source_code}).fetchone()
    return dict(row) if row else None, source_code


def _serialize_facts(facts: Optional[dict]) -> Optional[dict]:
    """Render a facts row for JSON: dates -> ISO, Decimal -> float, jsonb as object."""
    if not facts:
        return None
    facts = dict(facts)
    for k, v in facts.items():
        if v is None:
            continue
        if hasattr(v, "isoformat"):
            facts[k] = v.isoformat()
        elif isinstance(v, Decimal):
            facts[k] = float(v)
    # jsonb arrives as a dict via psycopg; guard the (rare) string case so the
    # API always emits an object, not a JSON string.
    ha = facts.get("holdings_analysis")
    if isinstance(ha, str):
        try:
            facts["holdings_analysis"] = json.loads(ha)
        except ValueError:
            facts["holdings_analysis"] = None
    return facts


def stats(conn) -> dict[str, Any]:
    """Exact persisted coverage summary; never aggregate NAV on request."""
    summary = conn.execute(_STATS_SQL).fetchone()
    if summary is None:
        raise RuntimeError("mf.dataset_summary is not initialized")
    return {
        "schemes_total": summary["schemes_total"],
        "in_scope_total": summary["in_scope_total"],
        "in_scope_live": summary["in_scope_live"],
        "amcs": summary["amcs"],
        "categories": summary["categories"],
        "nav_rows": summary["nav_rows"],
        "nav_first": summary["nav_first"].isoformat() if summary["nav_first"] else None,
        "nav_last": summary["nav_last"].isoformat() if summary["nav_last"] else None,
        "enrichment_pct": (
            float(summary["enrichment_pct"])
            if summary["enrichment_pct"] is not None
            else None
        ),
        "dataset_version": summary["dataset_version"],
        "source_content_hash": summary["source_content_hash"],
        "dataset_refreshed_at": summary["refreshed_at"].isoformat(),
    }


def list_funds(
    conn,
    *,
    q: Optional[str] = None,
    amc: Optional[str] = None,
    category: Optional[str] = None,
    option: Optional[str] = None,
    in_scope: bool = True,
    live: bool = True,
    page: int = 1,
    per_page: int = 50,
    sort: str = "name",
) -> dict[str, Any]:
    """Searchable, paginated fund list with each fund's latest NAV."""
    order = _SORT.get(sort, "f.scheme_name")
    where = ["TRUE"]
    params: dict[str, Any] = {}
    if in_scope:
        where.append("f.in_scope")
    if live:
        where.append("NOT f.is_defunct")
    if q:
        where.append("(f.scheme_name ILIKE %(q)s OR CAST(f.amfi_scheme_code AS text) = %(qeq)s "
                     "OR f.isin_primary = %(qeq)s)")
        params["q"] = f"%{q}%"
        params["qeq"] = q.strip()
    if amc:
        where.append("a.amfi_amc_name = %(amc)s")
        params["amc"] = amc
    if category:
        where.append("f.scheme_category = %(cat)s")
        params["cat"] = category
    if option:
        where.append("f.option_type = %(opt)s")
        params["opt"] = option

    where_sql = " AND ".join(where)
    # Count with a lightweight FROM — the lateral latest-NAV join and the facts
    # join must NOT run for a count, or we compute latest NAV for every row just
    # to count them.
    count_base = f"""
        FROM mf.funds f
        JOIN mf.amcs a ON a.amc_id = f.amc_id
        WHERE {where_sql}
    """
    total = conn.execute(f"SELECT count(*) AS n {count_base}", params).fetchone()["n"]
    params["per"] = per_page
    params["off"] = (page - 1) * per_page

    # Paginate FIRST over a cheap scan, then join the expensive bits (latest NAV,
    # facts) only for the page's rows. The sort key decides what the page CTE must
    # join: a name sort needs no join; a facts/NAV sort needs its column available
    # before ORDER BY. ``pos`` preserves page order through the outer joins.
    if sort in ("aum", "expense", "return_5y"):
        sort_join = ("LEFT JOIN mf.fund_facts fs "
                     "ON fs.amfi_scheme_code = f.amfi_scheme_code")
        order = {"aum": "fs.aum DESC NULLS LAST",
                 "expense": "fs.expense_ratio ASC NULLS LAST",
                 "return_5y": "fs.return_5year DESC NULLS LAST"}[sort]
    elif sort == "nav":
        sort_join = ("LEFT JOIN LATERAL (SELECT n.nav FROM mf.nav_history n "
                     "WHERE n.amfi_scheme_code = f.amfi_scheme_code "
                     "ORDER BY n.nav_date DESC LIMIT 1) lt ON true")
        order = "lt.nav DESC NULLS LAST"
    else:
        sort_join = ""
        order = "f.scheme_name"

    page_cte = f"""
        SELECT f.amfi_scheme_code, f.scheme_name, f.plan_type, f.option_type,
               f.scheme_category, f.is_active, f.is_defunct, f.isin_primary,
               a.amfi_amc_name,
               row_number() OVER (ORDER BY {order}, f.amfi_scheme_code) AS pos
        FROM mf.funds f
        JOIN mf.amcs a ON a.amc_id = f.amc_id
        {sort_join}
        WHERE {where_sql}
    """
    rows = conn.execute(
        f"""
        SELECT p.amfi_scheme_code, p.scheme_name, p.plan_type, p.option_type,
               p.scheme_category, p.is_active, p.is_defunct, p.isin_primary,
               p.amfi_amc_name, lt.nav AS latest_nav, lt.nav_date AS latest_nav_date,
               ff.aum, ff.expense_ratio, ff.return_5year
        FROM (
            SELECT * FROM ({page_cte}) t
            ORDER BY pos LIMIT %(per)s OFFSET %(off)s
        ) p
        LEFT JOIN LATERAL (
            SELECT n.nav, n.nav_date FROM mf.nav_history n
            WHERE n.amfi_scheme_code = p.amfi_scheme_code
            ORDER BY n.nav_date DESC LIMIT 1
        ) lt ON true
        LEFT JOIN mf.fund_facts ff ON ff.amfi_scheme_code = p.amfi_scheme_code
        ORDER BY p.pos
        """,
        params,
    ).fetchall()

    for r in rows:
        r["latest_nav"] = _f(r["latest_nav"])
        r["aum"] = _f(r["aum"])
        r["expense_ratio"] = _f(r["expense_ratio"])
        r["return_5year"] = _f(r["return_5year"])
        if r["latest_nav_date"]:
            r["latest_nav_date"] = r["latest_nav_date"].isoformat()

    return {"total": total, "page": page, "per_page": per_page, "results": rows}


def fund_detail(conn, code: int) -> Optional[dict[str, Any]]:
    """Everything about one fund: identity, latest NAV, facts, opinions, siblings."""
    fund = conn.execute(
        """
        SELECT f.amfi_scheme_code, f.scheme_name, f.plan_type, f.plan_source,
               f.option_type, f.periodicity, f.scheme_type, f.scheme_category,
               f.is_etf, f.is_defunct, f.is_active, f.in_scope,
               f.isin_growth_or_div_payout, f.isin_div_reinvest, f.isin_primary,
               a.amfi_amc_name, f.created_at, f.updated_at
        FROM mf.funds f JOIN mf.amcs a ON a.amc_id = f.amc_id
        WHERE f.amfi_scheme_code = %(code)s
        """,
        {"code": code},
    ).fetchone()
    if not fund:
        return None

    latest = conn.execute(
        """
        SELECT nav, nav_date FROM mf.nav_history
        WHERE amfi_scheme_code = %(code)s ORDER BY nav_date DESC LIMIT 1
        """,
        {"code": code},
    ).fetchone()

    facts = conn.execute(_FACTS_SELECT, {"code": code}).fetchone()
    facts = dict(facts) if facts else None
    # The AMC factsheet attributes fund-level data to only some codes of a
    # variant group (e.g. a recent AMFI re-issue under the same scheme name
    # has none of its own). Borrow the group's canonical variant's facts and
    # disclose the source so the page is never blank and never silently mixed.
    facts_source_code: Optional[int] = None
    if facts is None or not _facts_are_usable(facts):
        sibling_facts, source_code = _facts_from_sibling(conn, code)
        if sibling_facts is not None:
            facts_source_code = source_code
            # Keep the full facts shape. Every field starts as the code's own
            # value (null when the code has none), and only the family-safe
            # fund-level fields are then filled from the sibling where the code
            # has no value. Per-scheme fields — SIP eligibility, transaction
            # status, exit load, published returns, risk ratios, expense
            # history — are NEVER taken from the sibling; they stay as the
            # code's own (usually null), even when the sibling has them set.
            own = facts or {}
            borrowed: dict[str, Any] = {k: own.get(k) for k in _ALL_FACTS_FIELDS}
            for k in _FAMILY_SAFE_FACTS_FIELDS:
                if borrowed.get(k) is None and sibling_facts.get(k) is not None:
                    borrowed[k] = sibling_facts.get(k)
            facts = borrowed

    holdings = conn.execute(
        """
        SELECT holding_rank, company_name, sector_name, nature_name, weight_pct
        FROM mf.fund_holdings
        WHERE amfi_scheme_code = %(code)s
        ORDER BY holding_rank LIMIT 20
        """,
        {"code": code},
    ).fetchall()

    # siblings in the same variant group (Regular/Direct/IDCW twins)
    siblings = conn.execute(
        """
        SELECT v2.amfi_scheme_code, f2.scheme_name, f2.plan_type, f2.option_type
        FROM mf.fund_variants v1
        JOIN mf.fund_variants v2 ON v2.group_key = v1.group_key
        JOIN mf.funds f2 ON f2.amfi_scheme_code = v2.amfi_scheme_code
        WHERE v1.amfi_scheme_code = %(code)s
        ORDER BY f2.plan_type, f2.option_type
        """,
        {"code": code},
    ).fetchall()

    out = dict(fund)
    for k in ("created_at", "updated_at"):
        if out[k]:
            out[k] = out[k].isoformat()
    if latest:
        out["latest_nav"] = _f(latest["nav"])
        out["latest_nav_date"] = latest["nav_date"].isoformat()
    else:
        out["latest_nav"] = None
        out["latest_nav_date"] = None
    out["facts"] = _serialize_facts(facts)
    out["facts_source_code"] = facts_source_code
    # Our own fund identity (tlws_mf_id / slug / tags) — per fund family, the
    # entity for related-news retrieval and stable URLs.
    family = conn.execute(
        """
        SELECT ff.tlws_mf_id, ff.slug, ff.tags
        FROM mf.fund_variants v
        JOIN mf.fund_family ff ON ff.group_key = v.group_key
        WHERE v.amfi_scheme_code = %(code)s
        """,
        {"code": code},
    ).fetchone()
    out["family"] = dict(family) if family else None
    out["siblings"] = siblings
    for h in holdings:
        if isinstance(h.get("weight_pct"), Decimal):
            h["weight_pct"] = float(h["weight_pct"])
    out["holdings"] = holdings
    return out


def nav_series(
    conn, code: int, *, years: Optional[float] = None
) -> dict[str, Any]:
    """NAV time series for the chart, optionally windowed to the last ``years``.

    The cutoff is computed in Python, not SQL: make_interval() with a fractional
    bound parameter is not portable across engines, whereas a concrete date is.
    """
    if years is None:
        rows = conn.execute(
            "SELECT nav_date, nav FROM mf.nav_history "
            "WHERE amfi_scheme_code = %(code)s ORDER BY nav_date",
            {"code": code}).fetchall()
    else:
        mx = conn.execute(
            "SELECT max(nav_date) AS m FROM mf.nav_history "
            "WHERE amfi_scheme_code = %(code)s", {"code": code}).fetchone()["m"]
        if mx is None:
            return {"code": code, "points": [], "count": 0}
        cutoff = mx - timedelta(days=int(round(years * 365.25)))
        rows = conn.execute(
            "SELECT nav_date, nav FROM mf.nav_history "
            "WHERE amfi_scheme_code = %(code)s AND nav_date > %(cutoff)s "
            "ORDER BY nav_date",
            {"code": code, "cutoff": cutoff}).fetchall()
    points = [{"date": r["nav_date"].isoformat(), "nav": float(r["nav"])} for r in rows]
    return {"code": code, "points": points, "count": len(points)}


def returns(conn, code: int) -> dict[str, Any]:
    """Returns computed from our own NAV series (authoritative, not a source's).

    For each horizon we take the latest NAV and the NAV at or just before the
    horizon start. None where the series is too short.
    """
    latest = conn.execute(
        "SELECT nav, nav_date FROM mf.nav_history "
        "WHERE amfi_scheme_code = %(code)s ORDER BY nav_date DESC LIMIT 1",
        {"code": code}).fetchone()
    if not latest:
        return {"code": code, "horizons": {}}
    last_nav = float(latest["nav"])
    last_date = latest["nav_date"]

    horizons = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "3Y": 36, "5Y": 60}
    out: dict[str, Any] = {"code": code, "as_of": last_date.isoformat(), "horizons": {}}
    for label, months in horizons.items():
        # Cutoff computed in Python (see nav_series re: make_interval portability).
        cutoff = last_date - timedelta(days=int(round(months * 30.44)))
        row = conn.execute(
            """
            SELECT nav, nav_date FROM mf.nav_history
            WHERE amfi_scheme_code = %(code)s AND nav_date <= %(cutoff)s
            ORDER BY nav_date DESC LIMIT 1
            """,
            {"code": code, "cutoff": cutoff}).fetchone()
        if row and float(row["nav"]) > 0:
            pct = (last_nav / float(row["nav"]) - 1.0) * 100.0
            out["horizons"][label] = round(pct, 2)
        else:
            out["horizons"][label] = None
    return out


#: Annual risk-free rate assumed for Sharpe/Sortino, stated to the client.
#: Conventional India figure tracks the long-end G-Sec; not a live feed.
RISK_FREE_ANNUAL = 0.065
_TRADING_DAYS = 252.0


def _annualized(rets: list[float]) -> tuple[float, float]:
    """(mean, stdev) of daily returns -> annualized via *252 / *sqrt(252)."""
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    std = math.sqrt(var)
    return mean * _TRADING_DAYS, std * math.sqrt(_TRADING_DAYS)


def fund_analytics(conn, code: int) -> dict[str, Any]:
    """Risk/behaviour analytics derived from the fund's own NAV series.

    Every figure here is computed from ``mf.nav_history`` (the AMFI-sourced
    daily NAV), so coverage is 100% of funds regardless of which enrichment
    source populated ``fund_facts``. This deliberately *differs* from the
    Groww/Scripbox Sharpe/std-dev/beta columns, which use their own windows
    and assumptions and cover only 20-37% of funds.

    Returns ``{"points": n}`` (and nothing else) when the series is too short
    to be meaningful; the client hides the analytics cards in that case.
    """
    rows = conn.execute(
        "SELECT nav_date, nav FROM mf.nav_history "
        "WHERE amfi_scheme_code = %(code)s ORDER BY nav_date",
        {"code": code}).fetchall()
    if len(rows) < 30:
        return {"code": code, "points": len(rows), "too_short": True}

    dates = [r["nav_date"] for r in rows]
    navs = [float(r["nav"]) for r in rows]
    n = len(navs)
    out: dict[str, Any] = {
        "code": code, "points": n,
        "first_date": dates[0].isoformat(), "as_of": dates[-1].isoformat(),
        "risk_free_pct": round(RISK_FREE_ANNUAL * 100, 2),
    }

    # ---- daily log returns -------------------------------------------------
    rets = [math.log(navs[i] / navs[i - 1]) for i in range(1, n)
            if navs[i - 1] > 0 and navs[i] > 0]
    if not rets:
        out["too_short"] = True
        return out
    ann_return, ann_vol = _annualized(rets)
    out["annualized_return_pct"] = round(ann_return * 100, 2)
    out["annual_vol_pct"] = round(ann_vol * 100, 2)

    # ---- Sharpe / Sortino (risk-free annualised to daily) ------------------
    if ann_vol > 0:
        out["sharpe"] = round((ann_return - RISK_FREE_ANNUAL) / ann_vol, 2)
    rf_daily = RISK_FREE_ANNUAL / _TRADING_DAYS
    downside = math.sqrt(sum(min(r - rf_daily, 0.0) ** 2 for r in rets) / len(rets))
    if downside > 0:
        out["sortino"] = round(
            (ann_return - RISK_FREE_ANNUAL) / (downside * math.sqrt(_TRADING_DAYS)), 2)

    # ---- max drawdown (depth, trough window, recovery) ---------------------
    peak = navs[0]
    peak_i = 0
    max_dd = 0.0
    dd_peak_i = dd_trough_i = 0
    for i in range(n):
        if navs[i] >= peak:
            peak, peak_i = navs[i], i
        dd = navs[i] / peak - 1.0
        if dd < max_dd:
            max_dd, dd_peak_i, dd_trough_i = dd, peak_i, i
    trough_peak = max(navs[dd_peak_i:dd_trough_i + 1])
    recovery_i = next((j for j in range(dd_trough_i + 1, n)
                       if navs[j] >= trough_peak), None)
    out["max_drawdown_pct"] = round(max_dd * 100, 2)
    out["max_dd_peak_date"] = dates[dd_peak_i].isoformat()
    out["max_dd_trough_date"] = dates[dd_trough_i].isoformat()
    out["max_dd_recovery_date"] = (dates[recovery_i].isoformat()
                                   if recovery_i is not None else None)

    # ---- CAGR + Calmar -----------------------------------------------------
    years = (dates[-1] - dates[0]).days / 365.25
    if years > 0 and navs[0] > 0:
        cagr = (navs[-1] / navs[0]) ** (1 / years) - 1
        out["cagr_pct"] = round(cagr * 100, 2)
        if max_dd < 0:
            out["calmar"] = round(cagr / abs(max_dd), 2)

    # ---- win rates ---------------------------------------------------------
    out["win_rate_days_pct"] = round(100.0 * sum(1 for r in rets if r > 0) / len(rets), 1)

    # ---- calendar-year returns (each year vs prior year's last NAV) --------
    by_year: dict[int, list] = {}
    for d, v in zip(dates, navs):
        by_year.setdefault(d.year, []).append((d, v))
    yearly = []
    prev_last = None
    for year, pts in by_year.items():
        base = prev_last if prev_last is not None else pts[0][1]
        r = (pts[-1][1] / base - 1) * 100 if base > 0 else None
        yearly.append({"year": year, "return_pct": round(r, 2) if r is not None else None})
        prev_last = pts[-1][1]
    out["yearly"] = yearly

    # ---- monthly returns (month vs its own first NAV) -> heatmap ----------
    by_month: dict[tuple[int, int], list] = {}
    for d, v in zip(dates, navs):
        by_month.setdefault((d.year, d.month), []).append(v)
    monthly: dict[str, dict[str, float]] = {}
    pos = neg = 0
    for (year, month), vs in by_month.items():
        if vs[0] > 0:
            r = (vs[-1] / vs[0] - 1) * 100
            monthly.setdefault(str(year), {})[str(month)] = round(r, 2)
            if r > 0:
                pos += 1
            else:
                neg += 1
    out["monthly"] = monthly
    total_months = pos + neg
    if total_months:
        out["win_rate_months_pct"] = round(100.0 * pos / total_months, 1)
    return out


def fund_peers(conn, code: int) -> dict[str, Any]:
    """Peer ranking of one fund inside its SEBI category.

    The percentile is computed from *our own* fund_facts return columns (so it
    is reproducible and 100% ours), NOT from the source's category aggregate.
    For each horizon we rank the fund's return against every other in-scope
    fund in the same ``sebi_category_name`` that has a non-null return for that
    horizon. ``beats_pct`` is the share of peers the fund outperforms.

    A category with fewer than 10 scored funds is too small to rank
    meaningfully, so that horizon is reported with ``peer_count`` and a null
    ``beats_pct``; the client hides it.
    """
    row = conn.execute(
        """
        SELECT f.amfi_scheme_code, ff.sebi_category_name,
               ff.return_1year, ff.return_3year, ff.return_5year
        FROM mf.funds f LEFT JOIN mf.fund_facts ff
             ON ff.amfi_scheme_code = f.amfi_scheme_code
        WHERE f.amfi_scheme_code = %(code)s
        """, {"code": code}).fetchone()
    if not row or not row["sebi_category_name"]:
        return {"code": code, "category": None, "horizons": {}}

    cat = row["sebi_category_name"]
    colmap = {"1Y": ("return_1year", row["return_1year"]),
              "3Y": ("return_3year", row["return_3year"]),
              "5Y": ("return_5year", row["return_5year"])}
    out = {"code": code, "category": cat, "horizons": {}}
    for label, (col, mine) in colmap.items():
        peers = [r["r"] for r in conn.execute(
            f"""
            SELECT {col} AS r FROM mf.fund_facts ff
            JOIN mf.funds f ON f.amfi_scheme_code = ff.amfi_scheme_code
            WHERE ff.sebi_category_name = %(cat)s
              AND f.in_scope AND NOT f.is_defunct AND ff.{col} IS NOT NULL
            """, {"cat": cat}).fetchall()]
        n = len(peers)
        if n < 10 or mine is None:
            out["horizons"][label] = {
                "peer_count": n, "fund_return": _f(mine), "beats_pct": None}
            continue
        below = sum(1 for p in peers if _f(p) < _f(mine))
        out["horizons"][label] = {
            "peer_count": n, "fund_return": round(float(mine), 2),
            "beats_pct": round(100.0 * below / (n - 1), 1),
            "rank": n - below,
        }
    return out


def risk_reward(conn, code: int) -> dict[str, Any]:
    """Category risk-reward map: (annualized volatility, CAGR) per peer fund.

    Data comes from the derived ``mf.fund_risk_profile`` table (refreshed by
    scripts/refresh_risk_profile.py). Points are the fund's own SEBI-category
    peers; the fund itself is flagged so the client can highlight it. AUM is
    attached for bubble sizing. Returns ``{"category": ...}`` with an empty
    ``points`` list when the profile table is empty (not yet refreshed).
    """
    me = conn.execute(
        """
        SELECT f.amfi_scheme_code, f.scheme_name, ff.sebi_category_name, ff.aum
        FROM mf.funds f LEFT JOIN mf.fund_facts ff
             ON ff.amfi_scheme_code = f.amfi_scheme_code
        WHERE f.amfi_scheme_code = %(code)s
        """, {"code": code}).fetchone()
    if not me or not me["sebi_category_name"]:
        return {"code": code, "category": None, "points": []}

    rows = conn.execute(
        """
        SELECT f.amfi_scheme_code, f.scheme_name, ff.aum,
               rp.annual_vol, rp.cagr, rp.max_drawdown, rp.points AS nav_points
        FROM mf.fund_risk_profile rp
        JOIN mf.funds f ON f.amfi_scheme_code = rp.amfi_scheme_code
        JOIN mf.fund_facts ff ON ff.amfi_scheme_code = f.amfi_scheme_code
        WHERE ff.sebi_category_name = %(cat)s
          AND f.in_scope AND NOT f.is_defunct
        ORDER BY rp.annual_vol
        """, {"cat": me["sebi_category_name"]}).fetchall()

    points = [{
        "amfi_scheme_code": r["amfi_scheme_code"],
        "scheme_name": r["scheme_name"],
        "vol": _f(r["annual_vol"]),
        "return": _f(r["cagr"]),
        "max_drawdown": _f(r["max_drawdown"]),
        "aum": _f(r["aum"]),
        "self": r["amfi_scheme_code"] == code,
    } for r in rows if r["annual_vol"] is not None and r["cagr"] is not None]
    return {"code": code, "category": me["sebi_category_name"],
            "points": points, "refreshed": bool(points)}


def funds_batch(conn, ids: list[str]) -> dict[str, Any]:
    """Batch fetch: a mixed list of up to 50 AMFI codes and/or ISINs -> summaries.

    Numeric inputs are AMFI scheme codes; anything else is treated as an ISIN and
    matched against either ISIN column (an ISIN is a unique scheme identifier, so
    the match is unambiguous). Returns the matched funds in request order
    (de-duplicated, each tagged with every input that resolved to it) plus the
    inputs that resolved to nothing.
    """
    codes: list[int] = []
    isins: list[str] = []
    for i in ids:
        if i.isdigit():
            codes.append(int(i))
        else:
            isins.append(i.upper())
    codes = list(dict.fromkeys(codes))
    isins = list(dict.fromkeys(isins))

    rows = conn.execute(
        """
        SELECT f.amfi_scheme_code, f.scheme_name, f.plan_type, f.option_type,
               f.scheme_category, f.isin_primary, f.isin_growth_or_div_payout,
               f.isin_div_reinvest, f.in_scope, f.is_defunct,
               a.amfi_amc_name,
               ff.aum, ff.expense_ratio, ff.base_expense_ratio, ff.return_5year,
               ff.sharpe_ratio, ff.beta, ff.risk_level, ff.fund_manager_name,
               ff.benchmark, ff.benchmark_name, ff.inception_date,
               lt.nav AS latest_nav, lt.nav_date AS latest_nav_date
        FROM mf.funds f
        JOIN mf.amcs a ON a.amc_id = f.amc_id
        LEFT JOIN mf.fund_facts ff ON ff.amfi_scheme_code = f.amfi_scheme_code
        LEFT JOIN LATERAL (
            SELECT n.nav, n.nav_date FROM mf.nav_history n
            WHERE n.amfi_scheme_code = f.amfi_scheme_code
            ORDER BY n.nav_date DESC LIMIT 1
        ) lt ON true
        WHERE f.amfi_scheme_code = ANY(%(codes)s::int[])
           OR f.isin_growth_or_div_payout = ANY(%(isins)s::text[])
           OR f.isin_div_reinvest = ANY(%(isins)s::text[])
        """,
        {"codes": codes, "isins": isins},
    ).fetchall()

    by_code = {r["amfi_scheme_code"]: r for r in rows}
    by_isin: dict[str, int] = {}
    for r in rows:
        for col in ("isin_growth_or_div_payout", "isin_div_reinvest"):
            v = r[col]
            if v:
                by_isin.setdefault(v.upper(), r["amfi_scheme_code"])

    # Resolve each input (request order) to a scheme code; track matched_by.
    code_inputs: dict[int, list[str]] = {}
    order: list[int] = []
    not_found: list[str] = []
    for i in ids:
        if i.isdigit():
            code = int(i)
            resolved = code if code in by_code else None
        else:
            resolved = by_isin.get(i.upper())
        if resolved is None:
            if i not in not_found:
                not_found.append(i)
            continue
        if resolved not in code_inputs:
            code_inputs[resolved] = []
            order.append(resolved)
        if i not in code_inputs[resolved]:
            code_inputs[resolved].append(i)

    # Variant-group facts fallback (same rule as fund_detail): codes whose own
    # fund_facts row is empty borrow the family's canonical variant facts, so
    # batch summaries match what the detail page shows. The "is it empty" test
    # uses the same _FACTS_IDENTITY_FIELDS the detail endpoint uses (and the
    # sibling SQL filters on), so both endpoints agree on what counts as "has
    # its own facts".
    missing = [
        code for code in order
        if all(by_code[code][k] is None for k in _FACTS_IDENTITY_FIELDS)
    ]
    facts_source: dict[int, int] = {}
    source_facts: dict[int, dict[str, Any]] = {}
    if missing:
        picked = conn.execute(_BATCH_SIBLING_SOURCE_SQL, {"codes": missing}).fetchall()
        facts_source = {int(r["for_code"]): int(r["source_code"]) for r in picked}
        if facts_source:
            source_facts = {
                int(r["amfi_scheme_code"]): dict(r)
                for r in conn.execute(
                    "SELECT amfi_scheme_code, aum, expense_ratio, base_expense_ratio,"
                    " return_5year, sharpe_ratio, beta, risk_level, fund_manager_name,"
                    " benchmark_name, inception_date"
                    " FROM mf.fund_facts WHERE amfi_scheme_code = ANY(%(codes)s::int[])",
                    {"codes": sorted(set(facts_source.values()))},
                ).fetchall()
            }

    funds: list[dict[str, Any]] = []
    for code in order:
        r = by_code[code]
        source_code = facts_source.get(code)
        if source_code is not None:
            borrowed = source_facts.get(source_code, {})
            r = dict(r)
            for k in _BATCH_FACTS_KEYS:
                if r.get(k) is None and borrowed.get(k) is not None:
                    r[k] = borrowed[k]
        fund = {
            "amfi_scheme_code": code,
            "matched_by": code_inputs[code],
            "scheme_name": r["scheme_name"],
            "plan_type": r["plan_type"],
            "option_type": r["option_type"],
            "scheme_category": r["scheme_category"],
            "amfi_amc_name": r["amfi_amc_name"],
            "isin_primary": r["isin_primary"],
            "in_scope": r["in_scope"],
            "is_defunct": r["is_defunct"],
        }
        for k in ("aum", "expense_ratio", "base_expense_ratio", "return_5year",
                  "sharpe_ratio", "beta", "latest_nav"):
            if r.get(k) is not None:
                fund[k] = float(r[k])
        for k in ("inception_date", "latest_nav_date"):
            if r.get(k):
                fund[k] = r[k].isoformat()
        fund["fund_manager_name"] = r["fund_manager_name"]
        fund["benchmark_name"] = r["benchmark_name"]
        fund["risk_level"] = r["risk_level"]
        fund["facts_source_code"] = source_code
        funds.append(fund)

    return {
        "requested": len(dict.fromkeys(ids)),
        "found": len(funds),
        "not_found": not_found,
        "funds": funds,
    }


def holdings_overlap(conn, codes: list[int]) -> dict[str, Any]:
    """Pairwise top-holdings overlap for the compare page.

    For each pair of funds, reports how many of their top holdings (by
    company_name) are shared, the combined weight of the shared names, and the
    union size (for a Jaccard similarity). Company names are normalised
    (lower-cased, surrounding punctuation stripped) so "HDFC Bank Ltd." and
    "HDFC Bank Ltd" still match. Funds with no holdings are omitted from their
    pairs.
    """
    names: dict[int, list[str]] = {}
    for c in codes:
        rows = conn.execute(
            """
            SELECT company_name, weight_pct FROM mf.fund_holdings
            WHERE amfi_scheme_code = %(c)s ORDER BY holding_rank LIMIT 20
            """, {"c": c}).fetchall()
        names[c] = [r["company_name"] for r in rows if r["company_name"]]

    def norm(s: str) -> str:
        return " ".join(s.lower().replace(".", " ").replace(",", " ").split())

    pairs = []
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            a, b = codes[i], codes[j]
            sa, sb = {norm(n) for n in names.get(a, [])}, {norm(n) for n in names.get(b, [])}
            shared = sa & sb
            union = sa | sb
            pairs.append({
                "a": a, "b": b,
                "shared_count": len(shared),
                "union_count": len(union),
                "jaccard": round(len(shared) / len(union), 3) if union else None,
            })
    return {"codes": list(codes), "pairs": pairs}


def amcs(conn) -> list[dict[str, Any]]:
    """AMCs with in-scope fund counts, for the filter dropdown."""
    return conn.execute(
        """
        SELECT a.amfi_amc_name, count(*) FILTER (WHERE f.in_scope AND NOT f.is_defunct) AS live_funds
        FROM mf.amcs a
        LEFT JOIN mf.funds f ON f.amc_id = a.amc_id
        GROUP BY a.amfi_amc_name
        ORDER BY live_funds DESC, a.amfi_amc_name
        """).fetchall()


def categories(conn) -> list[dict[str, Any]]:
    """Scheme categories with live in-scope fund counts, for the filter dropdown."""
    return conn.execute(
        """
        SELECT scheme_category, count(*) AS live_funds
        FROM mf.funds
        WHERE in_scope AND NOT is_defunct
        GROUP BY scheme_category
        ORDER BY live_funds DESC
        """).fetchall()


def options(conn) -> list[str]:
    """Distinct in-scope option types, for the filter dropdown."""
    return [r["option_type"] for r in conn.execute(
        "SELECT DISTINCT option_type FROM mf.funds WHERE in_scope AND NOT is_defunct "
        "ORDER BY option_type").fetchall()]


def suggest(conn, q: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Autocomplete suggestions. Exact code/ISIN match first, then name prefix,
    then substring. Fast and lightweight (no joins to facts/holdings)."""
    q = (q or "").strip()
    if not q:
        return []
    rows = conn.execute(
        """
        SELECT f.amfi_scheme_code, f.scheme_name, f.plan_type, f.option_type,
               a.amfi_amc_name
        FROM mf.funds f
        JOIN mf.amcs a ON a.amc_id = f.amc_id
        WHERE f.in_scope AND NOT f.is_defunct AND (
            CAST(f.amfi_scheme_code AS text) = %(qeq)s
            OR f.isin_primary = %(qeq)s
            OR f.scheme_name ILIKE %(q)s
        )
        ORDER BY
            (CAST(f.amfi_scheme_code AS text) = %(qeq)s OR f.isin_primary = %(qeq)s) DESC,
            (f.scheme_name ILIKE %(qprefix)s) DESC,
            f.scheme_name
        LIMIT %(limit)s
        """,
        {"q": f"%{q}%", "qeq": q, "qprefix": f"{q}%", "limit": limit},
    ).fetchall()
    return rows


#: movers period -> approximate day span.
_MOVER_DAYS = {"1d": 1, "1w": 7, "1m": 30, "3m": 90, "6m": 182, "1y": 365}


def movers(
    conn, *, period: str = "1m", direction: str = "gainers", limit: int = 10
) -> dict[str, Any]:
    """Top gainers/losers over a period, computed from our own NAV history.

    For each in-scope fund: latest NAV vs the NAV at or just before the period
    start. The cutoff is computed in Python (portable)."""
    days = _MOVER_DAYS.get(period, 30)
    ref = conn.execute("SELECT max(nav_date) AS mx FROM mf.nav_history").fetchone()["mx"]
    if ref is None:
        return {"period": period, "direction": direction, "results": []}
    cutoff = ref - timedelta(days=days)
    # Single group-by scan over the recent window (fast on the embedded engine).
    # "prev" is the earliest NAV at/after the period start, "latest" the most recent.
    rows = conn.execute(
        """
        SELECT amfi_scheme_code,
               (array_agg(nav ORDER BY nav_date DESC))[1] AS latest_nav,
               (array_agg(nav ORDER BY nav_date ASC))[1]  AS prev_nav,
               max(nav_date) AS latest_nav_date,
               min(nav_date) AS prev_nav_date
        FROM mf.nav_history
        WHERE nav_date >= %(cutoff)s
          AND amfi_scheme_code IN (
              SELECT amfi_scheme_code FROM mf.funds WHERE in_scope AND NOT is_defunct)
        GROUP BY amfi_scheme_code
        """,
        {"cutoff": cutoff},
    ).fetchall()
    codes = [int(r["amfi_scheme_code"]) for r in rows]
    names = {}
    if codes:
        for r in conn.execute(
                "SELECT f.amfi_scheme_code, f.scheme_name, f.plan_type, f.option_type, a.amfi_amc_name "
                "FROM mf.funds f JOIN mf.amcs a ON a.amc_id = f.amc_id "
                "WHERE f.amfi_scheme_code = ANY(%(codes)s)",
                {"codes": codes}).fetchall():
            names[int(r["amfi_scheme_code"])] = r
    out = []
    for r in rows:
        latest, prev = r["latest_nav"], r["prev_nav"]
        if latest and prev and float(prev) > 0:
            meta = names.get(int(r["amfi_scheme_code"]), {})
            out.append({
                "amfi_scheme_code": int(r["amfi_scheme_code"]),
                "scheme_name": meta.get("scheme_name"),
                "plan_type": meta.get("plan_type"),
                "option_type": meta.get("option_type"),
                "amfi_amc_name": meta.get("amfi_amc_name"),
                "latest_nav": _f(latest), "prev_nav": _f(prev),
                "latest_nav_date": r["latest_nav_date"].isoformat(),
                "prev_nav_date": r["prev_nav_date"].isoformat(),
                "pct_change": round((float(latest) / float(prev) - 1.0) * 100.0, 2),
            })
    out.sort(key=lambda x: x["pct_change"], reverse=(direction == "gainers"))
    return {"period": period, "direction": direction,
            "as_of": ref.isoformat(), "from": cutoff.isoformat(), "results": out[:limit]}


# Broad families for the front-page category movers. AMFI's scheme_category
# mixes current names ("Equity Scheme - Large Cap Fund") with legacy ones
# ("Equity Schemes - Thematic Fund", "Income/Debt Oriented Schemes - X",
# "Other Scheme - Index Funds"), so mapping is by keyword, not exact string.
FAMILY_ORDER = ("Equity", "Debt", "Hybrid", "Index", "ETF", "FoF", "Solution", "Other")


def fund_family(scheme_category: Optional[str]) -> str:
    """Map an AMFI scheme_category to its broad family (Equity/Debt/…)."""
    c = (scheme_category or "").strip()
    cl = c.lower()
    if cl.startswith("equity scheme") or cl.startswith("elss"):
        return "Equity"            # ELSS is a tax-saver equity scheme
    if cl.startswith("income/debt oriented") or cl.startswith("debt scheme") or cl == "income":
        return "Debt"              # "Income" (legacy) was a debt income category
    if cl.startswith("hybrid scheme"):
        return "Hybrid"
    if "etf" in cl or "exchange traded fund" in cl:
        return "ETF"               # catches "… - Other ETFs", "Gold ETF", "Equity ETF"
    if cl.startswith("index fund") or "index funds" in cl:
        return "Index"
    if "fund of funds" in cl or "fof" in cl:
        return "FoF"
    if cl.startswith("solution oriented") or cl.startswith("children"):
        return "Solution"
    return "Other"


def category_movers(
    conn, *, period: str = "1m", limit: int = 5
) -> dict[str, Any]:
    """Top-N gainers AND losers per broad category family.

    Same period logic as ``movers`` (latest NAV vs the NAV at/just before the
    period start), but grouped by fund family so the front page can show one
    compact tile per family. One window scan; grouping/slicing in Python.

    Variants of one scheme (GROWTH/IDCW, and multiple IDCW payout periodicities
    — each is its own AMFI scheme code) move identically, so they would
    otherwise stack up the list. They are collapsed onto one row per
    ``mf.fund_variants`` family: the variant with the biggest absolute move is
    the representative, and its ``variants`` count tells the UI how many plan/
    option variants of that scheme were in the window.
    """
    days = _MOVER_DAYS.get(period, 30)
    ref = conn.execute("SELECT max(nav_date) AS mx FROM mf.nav_history").fetchone()["mx"]
    if ref is None:
        return {"period": period, "categories": []}
    cutoff = ref - timedelta(days=days)
    rows = conn.execute(
        """
        SELECT f.amfi_scheme_code, f.scheme_name, f.scheme_category, f.option_type,
               a.amfi_amc_name,
               coalesce(fv.group_key, CAST(f.amfi_scheme_code AS text)) AS fam_key,
               (array_agg(n.nav ORDER BY n.nav_date DESC))[1] AS latest_nav,
               (array_agg(n.nav ORDER BY n.nav_date ASC))[1]  AS prev_nav,
               max(n.nav_date) AS latest_nav_date
        FROM mf.nav_history n
        JOIN mf.funds f ON f.amfi_scheme_code = n.amfi_scheme_code
        JOIN mf.amcs a ON a.amc_id = f.amc_id
        LEFT JOIN mf.fund_variants fv ON fv.amfi_scheme_code = f.amfi_scheme_code
        WHERE n.nav_date >= %(cutoff)s
          AND f.in_scope AND NOT f.is_defunct
        GROUP BY f.amfi_scheme_code, f.scheme_name, f.scheme_category,
                 f.option_type, a.amfi_amc_name, fv.group_key
        """,
        {"cutoff": cutoff},
    ).fetchall()

    def item(r) -> dict[str, Any] | None:
        latest, prev = r["latest_nav"], r["prev_nav"]
        if not (latest and prev) or float(prev) <= 0:
            return None
        return {
            "amfi_scheme_code": int(r["amfi_scheme_code"]),
            "scheme_name": r["scheme_name"],
            "scheme_category": r["scheme_category"],
            "amfi_amc_name": r["amfi_amc_name"],
            "option_type": r["option_type"],
            "latest_nav": _f(latest),
            "latest_nav_date": r["latest_nav_date"].isoformat(),
            "pct_change": round((float(latest) / float(prev) - 1.0) * 100.0, 2),
        }

    # Collapse variants onto one row per scheme family.
    families: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        it = item(r)
        if it is None:
            continue
        families.setdefault(r["fam_key"], []).append(it)

    per_family: list[dict[str, Any]] = []
    for fam in families.values():
        # Biggest absolute move represents the family; prefer GROWTH/REGULAR on
        # ties so the row matches the fund's canonical variant elsewhere in the UI.
        rep = sorted(
            fam,
            key=lambda x: (-abs(x["pct_change"]), x["option_type"] != "GROWTH",
                           x["amfi_scheme_code"]),
        )[0]
        rep = dict(rep)
        rep["variants"] = len(fam)
        per_family.append(rep)

    by_family: dict[str, list[dict[str, Any]]] = {}
    for it in per_family:
        by_family.setdefault(fund_family(it["scheme_category"]), []).append(it)

    categories = []
    for fam in FAMILY_ORDER:
        items = by_family.get(fam)
        if not items:
            continue
        gainers = sorted(items, key=lambda x: x["pct_change"], reverse=True)[:limit]
        losers = sorted(items, key=lambda x: x["pct_change"])[:limit]
        categories.append({"category": fam, "funds": len(items),
                           "gainers": gainers, "losers": losers})
    return {"period": period, "as_of": ref.isoformat(),
            "from": cutoff.isoformat(), "categories": categories}


def compare(conn, codes: list[int], *, years: float = 1.0) -> dict[str, Any]:
    """Normalized NAV overlay for up to N funds.

    Each series is rebased to 100 at the window start so funds on different NAV
    scales compare fairly. Returns per-fund metadata plus date-aligned series.
    """
    out_funds: list[dict[str, Any]] = []
    for code in codes:
        fund = conn.execute(
            """
            SELECT f.amfi_scheme_code, f.scheme_name, f.plan_type, f.option_type,
                   a.amfi_amc_name,
                   ff.aum, ff.expense_ratio, ff.base_expense_ratio, ff.return_5year,
                   ff.sharpe_ratio, ff.beta, ff.risk_level, ff.fund_manager_name,
                   ff.benchmark_name, ff.inception_date, ff.registrar_agent,
                   lt.nav AS latest_nav, lt.nav_date AS latest_nav_date
            FROM mf.funds f
            JOIN mf.amcs a ON a.amc_id = f.amc_id
            LEFT JOIN mf.fund_facts ff ON ff.amfi_scheme_code = f.amfi_scheme_code
            LEFT JOIN LATERAL (
                SELECT n.nav, n.nav_date FROM mf.nav_history n
                WHERE n.amfi_scheme_code = f.amfi_scheme_code
                ORDER BY n.nav_date DESC LIMIT 1
            ) lt ON true
            WHERE f.amfi_scheme_code = %(code)s
            """, {"code": code}).fetchone()
        if not fund:
            continue
        fund = dict(fund)
        for k in ("aum", "expense_ratio", "base_expense_ratio", "return_5year",
                  "sharpe_ratio", "beta", "latest_nav"):
            if fund.get(k) is not None:
                fund[k] = float(fund[k])
        for k in ("inception_date", "latest_nav_date"):
            if fund.get(k):
                fund[k] = fund[k].isoformat()
        series = nav_series(conn, code, years=years)["points"]
        if series:
            base = series[0]["nav"]
            if base and base > 0:
                for pt in series:
                    pt["value"] = round(pt["nav"] / base * 100.0, 4)
        out_funds.append({"fund": fund, "points": series,
                          "returns": returns(conn, code)["horizons"]})
    return {"years": years, "funds": out_funds}


def list_fund_families(
    conn,
    *,
    q: Optional[str] = None,
    amc: Optional[str] = None,
    category: Optional[str] = None,
    option: Optional[str] = None,
    live: bool = True,
    page: int = 1,
    per_page: int = 50,
    sort: str = "name",
) -> dict[str, Any]:
    """One row per scheme family (base scheme), collapsing plan/option variants.

    A family's identity is the base-scheme key (``group_key``) plus AMC and
    scheme classification: ``group_key`` alone is a documented heuristic that
    can collide across AMCs (same-named funds from different AMCs), so the AMC
    and scheme type/category are part of the identity and ``variant_count``
    never mixes AMCs. The representative row is the Regular Growth variant
    where one exists, else the first in-scope variant. Funds with no variant
    group are their own family.
    """
    order = _SORT_FAMILY.get(sort, "s.scheme_name")
    where = ["f.in_scope"]
    params: dict[str, Any] = {}
    if live:
        where.append("NOT f.is_defunct")
    if q:
        where.append("(f.scheme_name ILIKE %(q)s OR CAST(f.amfi_scheme_code AS text) = %(qeq)s "
                     "OR f.isin_primary = %(qeq)s)")
        params["q"] = f"%{q}%"
        params["qeq"] = q.strip()
    if amc:
        where.append("a.amfi_amc_name = %(amc)s")
        params["amc"] = amc
    if category:
        where.append("f.scheme_category = %(cat)s")
        params["cat"] = category
    if option:
        where.append("f.option_type = %(opt)s")
        params["opt"] = option
    where_sql = " AND ".join(where)

    base = f"""
        WITH scoped AS (
            SELECT f.*, a.amfi_amc_name,
                   coalesce(fv.group_key, CAST(f.amfi_scheme_code AS text)) AS fam_key
            FROM mf.funds f
            JOIN mf.amcs a ON a.amc_id = f.amc_id
            LEFT JOIN mf.fund_variants fv ON fv.amfi_scheme_code = f.amfi_scheme_code
            WHERE {where_sql}
        ), picked AS (
            SELECT *,
                row_number() OVER (
                    PARTITION BY fam_key, amc_id, scheme_type, scheme_category
                    ORDER BY (option_type = 'GROWTH') DESC, (plan_type = 'REGULAR') DESC,
                             amfi_scheme_code) AS rn,
                count(*) OVER (
                    PARTITION BY fam_key, amc_id, scheme_type, scheme_category
                ) AS variant_count
            FROM scoped
        )
        SELECT * FROM picked
    """
    # Count distinct families cheaply — no window functions, no per-row joins.
    total = conn.execute(
        f"""
        SELECT count(DISTINCT (coalesce(fv.group_key, CAST(f.amfi_scheme_code AS text)),
                               f.amc_id, f.scheme_type, f.scheme_category)) AS n
        FROM mf.funds f
        JOIN mf.amcs a ON a.amc_id = f.amc_id
        LEFT JOIN mf.fund_variants fv ON fv.amfi_scheme_code = f.amfi_scheme_code
        WHERE {where_sql}
        """,
        params,
    ).fetchone()["n"]
    params["per"] = per_page
    params["off"] = (page - 1) * per_page
    rows = conn.execute(
        f"""
        SELECT s.amfi_scheme_code, s.scheme_name, s.plan_type, s.option_type,
               s.scheme_category, s.is_active, s.is_defunct, s.isin_primary,
               s.amfi_amc_name, s.variant_count, s.fam_key,
               lt.nav AS latest_nav, lt.nav_date AS latest_nav_date,
               ff.aum, ff.expense_ratio, ff.return_5year
        FROM ({base} WHERE rn = 1) s
        LEFT JOIN LATERAL (
            SELECT n.nav, n.nav_date FROM mf.nav_history n
            WHERE n.amfi_scheme_code = s.amfi_scheme_code
            ORDER BY n.nav_date DESC LIMIT 1
        ) lt ON true
        LEFT JOIN mf.fund_facts ff ON ff.amfi_scheme_code = s.amfi_scheme_code
        ORDER BY {order}, s.amfi_scheme_code
        LIMIT %(per)s OFFSET %(off)s
        """,
        params,
    ).fetchall()

    for r in rows:
        r["latest_nav"] = _f(r["latest_nav"])
        r["aum"] = _f(r["aum"])
        r["expense_ratio"] = _f(r["expense_ratio"])
        r["return_5year"] = _f(r["return_5year"])
        if r["latest_nav_date"]:
            r["latest_nav_date"] = r["latest_nav_date"].isoformat()
    return {"total": total, "page": page, "per_page": per_page, "results": rows}
