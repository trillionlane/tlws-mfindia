"""Map Groww ``mfServerSideData`` payloads onto ``mf.fund_facts`` / ``mf.fund_holdings``.

Groww is the backfill source for what Scripbox misses (~12% of in-scope codes) and
for fields Scripbox lacks (benchmark on multi-asset funds, expense-ratio history,
fund-manager bios, holdings with sector). The join key is ISIN, resolved by the
crawl (which validates the fetched page's ISIN against the fund).
"""

from __future__ import annotations

from typing import Any, Optional

from mfdataindia.load.scripbox_to_store import (
    FUND_FACTS_COLUMNS, FUND_FACTS_TYPES, _bool, _date, _int, _json, _num, _ts,
)

__all__ = ["groww_to_facts", "groww_to_holdings", "load_groww_fund"]


def groww_to_facts(sd: dict[str, Any], amfi_scheme_code: int) -> tuple:
    """Map one Groww ``mfServerSideData`` dict to a mf.fund_facts row.

    Only fields Groww reliably provides are mapped; the rest stay NULL so a
    Scripbox value already present is never clobbered by an empty Groww cell.
    ``launch_date`` is Groww's fund inception (DD-Mon-YYYY).
    """
    launch = _date(sd.get("launch_date") or sd.get("allotment_date"))
    row = {
        "amfi_scheme_code": str(amfi_scheme_code),
        "fund_slug": sd.get("search_id"),
        "groww_slug": sd.get("search_id"),
        "rta_scheme_code": sd.get("rta_scheme_code"),
        "asset_class": sd.get("category"),
        "sub_asset_class": sd.get("sub_category"),
        "super_category": sd.get("super_category"),
        "sub_category": sd.get("sub_category"),
        "aum": _num(sd.get("aum")),
        "expense_ratio": _num(sd.get("expense_ratio")),
        "base_expense_ratio": _num(sd.get("base_expense_ratio")),
        "face_value": _num(sd.get("face_value")),
        "inception_date": launch,
        "launch_date": launch,
        "benchmark": sd.get("benchmark"),
        "benchmark_name": sd.get("benchmark_name"),
        "fund_manager_name": sd.get("fund_manager"),
        "fund_manager_details": _json(sd.get("fund_manager_details")),
        "risk_level": sd.get("nfo_risk"),
        "registrar_agent": sd.get("registrar_agent"),
        "is_sip_allowed": _bool(sd.get("sip_allowed")),
        "is_purchase_allowed": _bool(sd.get("lumpsum_allowed")),
        "is_investable": _bool(sd.get("available_for_investment")),
        "is_stp_allowed": _bool(sd.get("stp_flag")),
        "is_swp_allowed": _bool(sd.get("swp_flag")),
        "min_initial_investment_amount": _num(sd.get("min_investment_amount")),
        "min_subsequent_investment_amount": _num(sd.get("min_sip_investment")),
        "min_withdrawal_amount": _num(sd.get("min_withdrawal")),
        "expense_ratio_history": _json(sd.get("historic_fund_expense")),
        "source_nav": _num(sd.get("nav")),
        "source_nav_date": _date(sd.get("nav_date")),
        "source": "GROWW",
        "fetched_at": _ts(sd.get("updated_at")) or _ts(__import__("datetime").datetime.now()),
    }
    return tuple(row.get(c) for c in FUND_FACTS_COLUMNS)


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
            h.get("company_name"),
            h.get("sector_name"),
            h.get("nature_name"),
            _num(h.get("market_value")),
            _num(h.get("corpus_per")),
            h.get("rating"),
        ))
    return out


def load_groww_fund(store: Any, sd: dict[str, Any], amfi_scheme_code: int) -> int:
    """Upsert one Groww fund into fund_facts and replace its holdings. Returns rows."""
    facts_row = groww_to_facts(sd, amfi_scheme_code)
    store.upsert_table(
        "fund_facts", "amfi_scheme_code", FUND_FACTS_COLUMNS,
        iter([facts_row]), column_types=FUND_FACTS_TYPES)
    holdings = groww_to_holdings(sd)
    if holdings:
        store.replace_holdings(amfi_scheme_code, holdings)
    return 1 + len(holdings)
