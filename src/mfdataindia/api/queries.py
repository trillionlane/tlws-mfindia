"""Read-only queries for the API.

These functions take a psycopg connection (supplied by the pool) and return plain
dicts/lists. No writes happen here; the API is a pure read layer over the store.

Serialisation note: NAV comes back as ``Decimal`` (NUMERIC(18,4)). It is converted
to ``float`` only at the JSON boundary — never stored as float anywhere upstream.
"""

from __future__ import annotations

from datetime import date, timedelta
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


def _f(v: Any) -> Optional[float]:
    return None if v is None else float(v)


def stats(conn) -> dict[str, Any]:
    """Coverage summary for the header bar."""
    cov = conn.execute("SELECT * FROM mf.v_coverage").fetchone()
    span = conn.execute(
        "SELECT count(*) AS rows, min(nav_date) AS first_nav_date, "
        "max(nav_date) AS last_nav_date, count(DISTINCT amfi_scheme_code) AS schemes "
        "FROM mf.nav_history").fetchone()
    enr = conn.execute("SELECT * FROM mf.v_enrichment_coverage").fetchone()
    return {
        "schemes_total": cov["schemes_total"],
        "in_scope_total": cov["in_scope_total"],
        "in_scope_live": cov["in_scope_live"],
        "amcs": cov["amcs"],
        "categories": cov["categories"],
        "nav_rows": span["rows"],
        "nav_first": span["first_nav_date"].isoformat() if span["first_nav_date"] else None,
        "nav_last": span["last_nav_date"].isoformat() if span["last_nav_date"] else None,
        "enrichment_pct": float(enr["facts_pct"]) if enr["facts_pct"] is not None else None,
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
    base = f"""
        FROM mf.funds f
        JOIN mf.amcs a ON a.amc_id = f.amc_id
        LEFT JOIN LATERAL (
            SELECT n.nav, n.nav_date FROM mf.nav_history n
            WHERE n.amfi_scheme_code = f.amfi_scheme_code
            ORDER BY n.nav_date DESC LIMIT 1
        ) lt ON true
        LEFT JOIN mf.fund_facts ff ON ff.amfi_scheme_code = f.amfi_scheme_code
        WHERE {where_sql}
    """
    total = conn.execute(f"SELECT count(*) AS n {base}", params).fetchone()["n"]
    params["per"] = per_page
    params["off"] = (page - 1) * per_page
    rows = conn.execute(
        f"""
        SELECT f.amfi_scheme_code, f.scheme_name, f.plan_type, f.option_type,
               f.scheme_category, f.is_active, f.is_defunct, f.isin_primary,
               a.amfi_amc_name, lt.nav AS latest_nav, lt.nav_date AS latest_nav_date,
               ff.aum, ff.expense_ratio, ff.return_5year
        {base}
        ORDER BY {order}, f.amfi_scheme_code
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

    facts = conn.execute(
        """
        SELECT aum, expense_ratio, face_value, inception_date, sebi_category_name,
               asset_class, sub_asset_class, taxability,
               return_1day, return_3month, return_6month, return_1year, return_3year,
               return_5year, return_10year, return_since_launch,
               min_initial_investment_amount, min_subsequent_investment_amount,
               is_sip_allowed, status, transaction_status, scripbox_fund_id, fund_slug,
               benchmark, benchmark_name, fund_manager_name, risk_level,
               base_expense_ratio, super_category, sub_category, registrar_agent
        FROM mf.fund_facts WHERE amfi_scheme_code = %(code)s
        """,
        {"code": code},
    ).fetchone()

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
    if facts:
        facts = dict(facts)
        for k, v in facts.items():
            if v is None:
                continue
            if hasattr(v, "isoformat"):
                facts[k] = v.isoformat()
            elif isinstance(v, Decimal):
                facts[k] = float(v)
        out["facts"] = facts
    else:
        out["facts"] = None
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
    order = "DESC" if direction == "gainers" else "ASC"
    rows = conn.execute(
        f"""
        WITH latest AS (
            SELECT DISTINCT ON (amfi_scheme_code) amfi_scheme_code, nav, nav_date
            FROM mf.nav_history ORDER BY amfi_scheme_code, nav_date DESC
        ),
        prev AS (
            SELECT DISTINCT ON (amfi_scheme_code) amfi_scheme_code, nav, nav_date
            FROM mf.nav_history WHERE nav_date <= %(cutoff)s
            ORDER BY amfi_scheme_code, nav_date DESC
        )
        SELECT f.amfi_scheme_code, f.scheme_name, f.plan_type, f.option_type,
               a.amfi_amc_name, l.nav AS latest_nav, l.nav_date AS latest_nav_date,
               p.nav AS prev_nav, p.nav_date AS prev_nav_date,
               ((l.nav / nullif(p.nav, 0)) - 1.0) * 100.0 AS pct_change
        FROM latest l
        JOIN prev p ON p.amfi_scheme_code = l.amfi_scheme_code
        JOIN mf.funds f ON f.amfi_scheme_code = l.amfi_scheme_code
        JOIN mf.amcs a ON a.amc_id = f.amc_id
        WHERE f.in_scope AND NOT f.is_defunct AND p.nav > 0
        ORDER BY pct_change {order} NULLS LAST
        LIMIT %(limit)s
        """,
        {"cutoff": cutoff, "limit": limit},
    ).fetchall()
    for r in rows:
        r["latest_nav"] = _f(r["latest_nav"])
        r["prev_nav"] = _f(r["prev_nav"])
        r["pct_change"] = _f(r["pct_change"])
        r["latest_nav_date"] = r["latest_nav_date"].isoformat()
        r["prev_nav_date"] = r["prev_nav_date"].isoformat()
    return {"period": period, "direction": direction,
            "as_of": ref.isoformat(), "from": cutoff.isoformat(), "results": rows}


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
                   a.amfi_amc_name
            FROM mf.funds f JOIN mf.amcs a ON a.amc_id = f.amc_id
            WHERE f.amfi_scheme_code = %(code)s
            """, {"code": code}).fetchone()
        if not fund:
            continue
        series = nav_series(conn, code, years=years)["points"]
        if series:
            base = series[0]["nav"]
            if base and base > 0:
                for pt in series:
                    pt["value"] = round(pt["nav"] / base * 100.0, 4)
        out_funds.append({"fund": dict(fund), "points": series,
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

    The representative row is the Regular Growth variant where one exists, else
    the first in-scope variant. `variant_count` reports how many in-scope
    variants the family has. Funds with no variant group are their own family.
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
                row_number() OVER (PARTITION BY fam_key
                    ORDER BY (option_type = 'GROWTH') DESC, (plan_type = 'REGULAR') DESC,
                             amfi_scheme_code) AS rn,
                count(*) OVER (PARTITION BY fam_key) AS variant_count
            FROM scoped
        )
        SELECT * FROM picked
    """
    total = conn.execute(
        f"SELECT count(*) AS n FROM ({base} WHERE rn = 1) t", params).fetchone()["n"]
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
