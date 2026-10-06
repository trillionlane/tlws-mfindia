"""Map Groww ``mfServerSideData`` payloads onto fund_facts / fund_holdings / amcs.

Groww is the backfill source that (a) fills the gaps Scripbox missed and (b)
supplies the fields only it has: the "Holdings analysis" breakdown, AMC-house
metadata, Groww/Crisil ratings, sub-type, exit-load / lock-in / portfolio
turnover, the full returns + category-comparison payload, and sector-tagged top
holdings.

The payload is *flat* (top-level ``mfServerSideData``; there is no
``fund_data``/``financials`` nesting). Join key is ISIN, validated by the crawl.

Because ``mf.fund_facts`` is keyed by ``amfi_scheme_code`` and already holds a
SCRIPBOX row for most funds, this loader *gap-fills* rather than merges
(``store.upsert_table(..., fill_only=True)``): Scripbox owns every shared column
and its value always wins, so Groww writes only into columns Scripbox left NULL.
The Groww-only columns in ``GROWW_OWNED_COLUMNS`` are exempt and stay refreshable
on a re-run. Holdings are written ``only_if_empty`` for the same reason — Groww
must never delete a snapshot it cannot attribute to itself.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from mfdataindia.load.scripbox_to_store import (
    FUND_FACTS_COLUMNS, FUND_FACTS_TYPES, _bool, _date, _int, _json, _num, _ts,
)

__all__ = [
    "GROWW_FUND_FACTS_COLUMNS", "GROWW_FUND_FACTS_TYPES",
    "GROWW_EXTRA_COLUMNS", "GROWW_OWNED_COLUMNS",
    "groww_to_facts", "groww_to_amc", "groww_to_holdings",
    "holdings_analysis", "load_groww_fund",
]

# Columns Groww additionally fills beyond the shared Scripbox shape.
GROWW_EXTRA_COLUMNS: tuple[str, ...] = (
    "groww_rating", "crisil_rating", "sub_type", "exit_load_value",
    "lock_in_period", "portfolio_turnover",
    "return_1week", "return_1month", "return_9month",
    "sharpe_ratio", "beta", "std_deviation", "risk_rating",
    "groww_return_stats", "holdings_analysis", "groww_fetched_at",
    # Groww-only columns the mapper has always produced but which were missing
    # from this tuple, so groww_to_facts emitted them and upsert_table silently
    # dropped them. Every field groww_to_facts maps must appear here.
    #
    # super_category / sub_category are intentionally ABSENT. Groww's
    # `super_category` is the fund name, not a category, and `sub_category`
    # duplicates sub_asset_class. Those two columns stay NULL permanently; do not
    # add them back without re-probing the payload. See sql/008.
    "base_expense_ratio", "expense_ratio_history", "registrar_agent",
)
GROWW_FUND_FACTS_COLUMNS: tuple[str, ...] = FUND_FACTS_COLUMNS + GROWW_EXTRA_COLUMNS

# Columns Groww *owns*: everything it adds beyond the Scripbox shape. On a merge
# these keep incoming-wins semantics; every other (Scripbox-shaped) column is
# gap-fill only, so Groww can never replace a value Scripbox already provided.
GROWW_OWNED_COLUMNS: tuple[str, ...] = GROWW_EXTRA_COLUMNS

GROWW_FUND_FACTS_TYPES: dict[str, str] = {
    **FUND_FACTS_TYPES,
    "groww_rating": "numeric(4,2)", "sub_type": "text", "exit_load_value": "text",
    "lock_in_period": "text", "portfolio_turnover": "numeric(9,2)",
    "return_1week": "numeric(9,4)", "return_1month": "numeric(9,4)",
    "return_9month": "numeric(9,4)",
    "sharpe_ratio": "numeric(9,4)", "beta": "numeric(9,4)",
    "std_deviation": "numeric(9,4)", "risk_rating": "text",
    "groww_return_stats": "jsonb", "holdings_analysis": "jsonb",
    "groww_fetched_at": "timestamptz",
    "base_expense_ratio": "numeric", "expense_ratio_history": "jsonb",
    "registrar_agent": "text",
}

# nature_name value -> display label for the asset-class split in the analysis.
_ASSET_CLASS_LABEL = {
    "EQUITY": "Equity", "DEBT": "Debt", "CASH": "Cash", "MF": "Fund of Funds",
    "REALEST": "Real Estate", "COMM": "Commodities",
}


def _format_lock_in(li: Any) -> Optional[str]:
    """Groww ``lock_in`` {years, months, days} -> '2y 6m' / 'Nil' / None."""
    if not isinstance(li, dict):
        return None
    parts = []
    if li.get("years"):
        parts.append(f"{li['years']}y")
    if li.get("months"):
        parts.append(f"{li['months']}m")
    if li.get("days"):
        parts.append(f"{li['days']}d")
    return " ".join(parts) if parts else "Nil"


def holdings_analysis(sd: dict[str, Any]) -> Optional[dict]:
    """Aggregate Groww's sector/nature-tagged holdings into the "Holdings analysis".

    Returns ``{asset_class, sector, as_on_date, source, computed_at}`` or None when
    there is nothing to aggregate. ``asset_class`` sums weights by instrument
    nature; a residual ``Other`` is added so the split reconciles to ~100 when the
    listed holdings do not (top-N portfolios). ``sector`` sums weights by
    sector_name (equity side; empty for pure debt funds).
    """
    holdings = sd.get("holdings") or []
    if not holdings:
        return None
    asset: dict[str, float] = {}
    sector: dict[str, float] = {}
    as_on: Optional[str] = None
    for h in holdings:
        if not isinstance(h, dict):
            continue
        try:
            w = float(h.get("corpus_per") or 0)
        except (TypeError, ValueError):
            w = 0.0
        if as_on is None:
            as_on = h.get("portfolio_date")
        nat = (h.get("nature_name") or "").upper()
        if nat:
            label = _ASSET_CLASS_LABEL.get(nat, nat.title())
            asset[label] = asset.get(label, 0.0) + w
        sec = h.get("sector_name")
        if sec:
            sector[sec] = sector.get(sec, 0.0) + w
    total_asset = sum(asset.values())
    if 0 < total_asset < 99.5:
        asset["Other"] = asset.get("Other", 0.0) + (100.0 - total_asset)
    return {
        "asset_class": {k: round(v, 2)
                        for k, v in sorted(asset.items(), key=lambda x: -x[1])},
        "sector": {k: round(v, 2)
                   for k, v in sorted(sector.items(), key=lambda x: -x[1])},
        "as_on_date": _date(as_on),
        "source": "GROWW",
        "computed_at": datetime.now().isoformat(),
    }


def groww_to_facts(sd: dict[str, Any], amfi_scheme_code: int,
                   *, fetched_at: Optional[datetime] = None) -> tuple:
    """Map one Groww ``mfServerSideData`` dict to a mf.fund_facts row.

    Fields Groww reliably provides are mapped; the rest stay NULL so a Scripbox
    value already present is never clobbered (the caller uses a merge upsert).
    Trailing returns come from ``return_stats[0]`` (annualised), the same basis as
    the published CAGR figures; the raw payloads are kept whole in
    ``groww_return_stats`` for re-derivation without re-fetching.
    """
    now = fetched_at or datetime.now()
    stats = (sd.get("return_stats") or [{}])[0] if sd.get("return_stats") else {}
    row = {
        "amfi_scheme_code": str(amfi_scheme_code),
        # identity / classification
        "fund_slug": sd.get("search_id"),
        "groww_slug": sd.get("search_id"),
        "rta_scheme_code": sd.get("rta_scheme_code"),
        "asset_class": sd.get("category"),
        "sub_asset_class": sd.get("sub_category"),
        # super_category / sub_category are deliberately NOT mapped. Groww's
        # `super_category` field holds the FUND NAME, not a category -- a live
        # probe found `super_category == fund_name` on every fund sampled. And
        # Groww's real hierarchy (`category` -> `sub_category`) is already
        # captured on the two lines above as asset_class / sub_asset_class, which
        # are 99.9% populated on live in-scope funds. Mapping them would put
        # scheme names into a category column and duplicate data we already hold.
        # See the DROPPED ENTIRELY note in sql/008_fund_data_status_v2.sql.
        "sub_type": (sd.get("category_info") or {}).get("sub_type"),
        # sizes / costs
        "aum": _num(sd.get("aum")),
        "expense_ratio": _num(sd.get("expense_ratio")),
        "base_expense_ratio": _num(sd.get("base_expense_ratio")),
        "face_value": _num(sd.get("face_value")),
        "inception_date": _date(sd.get("launch_date") or sd.get("allotment_date")),
        "launch_date": _date(sd.get("launch_date") or sd.get("allotment_date")),
        # risk / transactional
        "benchmark": sd.get("benchmark"),
        "benchmark_name": sd.get("benchmark_name"),
        "fund_manager_name": sd.get("fund_manager"),
        "fund_manager_details": _json(sd.get("fund_manager_details")),
        "risk_level": sd.get("nfo_risk"),
        "registrar_agent": sd.get("registrar_agent"),
        "exit_load_value": sd.get("exit_load"),
        "lock_in_period": _format_lock_in(sd.get("lock_in")),
        "portfolio_turnover": _num(sd.get("portfolio_turnover")),
        "is_sip_allowed": _bool(sd.get("sip_allowed")),
        "is_purchase_allowed": _bool(sd.get("lumpsum_allowed")),
        "is_investable": _bool(sd.get("available_for_investment")),
        "is_stp_allowed": _bool(sd.get("stp_flag")),
        "is_swp_allowed": _bool(sd.get("swp_flag")),
        "min_initial_investment_amount": _num(sd.get("min_investment_amount")),
        "min_subsequent_investment_amount": _num(sd.get("min_sip_investment")),
        "min_withdrawal_amount": _num(sd.get("min_withdrawal")),
        "expense_ratio_history": _json(sd.get("historic_fund_expense")),
        # source NAV snapshot
        "source_nav": _num(sd.get("nav")),
        "source_nav_date": _date(sd.get("nav_date")),
        "returns_as_on_date": _date(sd.get("nav_date")),
        # annualised trailing returns (from return_stats)
        "return_1day": _num(stats.get("return1d")),
        "return_1week": _num(stats.get("return1w")),
        "return_3month": _num(stats.get("return3m")),
        "return_6month": _num(stats.get("return6m")),
        "return_1year": _num(stats.get("return1y")),
        "return_2year": _num(stats.get("return2y")),
        "return_3year": _num(stats.get("return3y")),
        "return_4year": _num(stats.get("return4y")),
        "return_5year": _num(stats.get("return5y")),
        "return_7year": _num(stats.get("return7y")),
        "return_10year": _num(stats.get("return10y")),
        "return_9month": _num(stats.get("return9m")),
        "return_1month": _num(stats.get("return1m")),
        "return_since_launch": _num(stats.get("return_since_created")),
        # risk metrics
        "sharpe_ratio": _num(stats.get("sharpe_ratio")),
        "beta": _num(stats.get("beta")),
        "std_deviation": _num(stats.get("standard_deviation")),
        "risk_rating": stats.get("risk_rating") or stats.get("risk"),
        # Groww-only enriched fields
        "groww_rating": _num(sd.get("groww_rating")),
        "crisil_rating": sd.get("crisil_rating"),
        "groww_return_stats": _json({
            "annualized": stats,
            "cumulative": sd.get("simple_return"),
        }),
        "holdings_analysis": _json(holdings_analysis(sd)),
        "source": "GROWW",
        "fetched_at": _ts(now),
        "groww_fetched_at": _ts(now),
    }
    return tuple(row.get(c) for c in GROWW_FUND_FACTS_COLUMNS)


def groww_to_amc(sd: dict[str, Any], *, fetched_at: Optional[datetime] = None) -> dict:
    """Extract AMC-house metadata from Groww ``amc_info`` for update_amc_info.

    Returns a dict of the groww-provided ``amcs`` columns; None values are dropped
    by the store. (No `amfi_amc_name` here — the store keys the update by the
    fund's AMFI-registered AMC name threaded through the job.)
    """
    ai = sd.get("amc_info") or {}
    now = fetched_at or datetime.now()
    return {
        "amc_aum": _num(ai.get("aum")),
        "amc_rank": _int(ai.get("rank")),
        "amc_launch_date": _date(ai.get("launch_date")),
        "amc_address": ai.get("address"),
        "amc_description": ai.get("description"),
        "amc_sponsor": ai.get("sponsor"),
        "amc_source": "GROWW",
        "amc_fetched_at": _ts(now),
    }


def groww_to_holdings(sd: dict[str, Any]) -> list[tuple]:
    """Map Groww ``holdings`` to fund_holdings row bodies for replace_holdings."""
    out: list[tuple] = []
    holdings = sd.get("holdings") or []
    portfolio_date = _date(sd.get("portfolio_date")) or _date(sd.get("nav_date"))
    for rank, h in enumerate(holdings, start=1):
        if not isinstance(h, dict):
            continue
        out.append((
            portfolio_date or _date(h.get("portfolio_date")),
            rank,
            h.get("company_name") or h.get("instrument_name"),
            h.get("sector_name"),
            h.get("nature_name"),
            _num(h.get("market_value")),
            _num(h.get("corpus_per")),
            h.get("rating"),
        ))
    return out


def load_groww_fund(
    store: Any,
    sd: dict[str, Any],
    amfi_scheme_code: int,
    amc_name: Optional[str] = None,
) -> int:
    """Enrich one Groww fund: gap-fill fund_facts, enrich the AMC, add holdings.

    Returns the number of rows written (facts row + holdings). ``amc_name`` is the
    fund's AMFI-registered AMC header (threaded through from the job); when given,
    Groww's ``amc_info`` is merged onto that existing AMC row.

    Groww is a *secondary* source and never replaces data it does not own:

    * ``fund_facts`` is gap-filled (``fill_only``) — Scripbox's value wins on every
      shared column and Groww writes only where it is still NULL. The columns
      listed in ``GROWW_OWNED_COLUMNS`` are exempt, so Groww's own ratings,
      holdings analysis and fetch stamp still refresh on a re-run.
    * holdings are written ``only_if_empty`` — a fund that already has a snapshot
      (Scripbox's) keeps it untouched. ``fund_holdings`` carries no source column,
      so a replace there would delete a snapshot we cannot attribute.
    """
    now = datetime.now()
    facts_row = groww_to_facts(sd, amfi_scheme_code, fetched_at=now)
    store.upsert_table(
        "fund_facts", "amfi_scheme_code", GROWW_FUND_FACTS_COLUMNS,
        iter([facts_row]), column_types=GROWW_FUND_FACTS_TYPES,
        fill_only=True,
        overwrite_columns=GROWW_OWNED_COLUMNS,
        update_columns=[c for c in GROWW_FUND_FACTS_COLUMNS
                        if c not in ("amfi_scheme_code", "source")],
    )
    if amc_name:
        store.update_amc_info(amc_name, groww_to_amc(sd, fetched_at=now))
    holdings = groww_to_holdings(sd)
    if holdings:
        store.replace_holdings(amfi_scheme_code, holdings, only_if_empty=True)
    return 1 + len(holdings)
