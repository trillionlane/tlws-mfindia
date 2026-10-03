"""Map Scripbox ``factsheetData`` payloads onto ``mf.fund_facts`` / ``mf.fund_opinions``.

Scripbox is the enrichment layer (facts only, from the robot-allowed HTML
``__NEXT_DATA__``), joined to AMFI by ``amfi_code`` == ``mf.funds.amfi_scheme_code``.
Facts and the licensing-sensitive opinion fields are kept in separate tables so a
redistribution-safe export can drop one table.

Only rows whose ``amfi_code`` already exists in ``mf.funds`` are emitted — the
foreign key requires it, and a Scripbox-only scheme is out of the canonical
universe. The caller filters against :meth:`PostgresStore.fund_codes`.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Iterator, Optional

__all__ = [
    "FUND_FACTS_COLUMNS", "FUND_FACTS_TYPES",
    "FUND_OPINIONS_COLUMNS", "FUND_OPINIONS_TYPES",
    "factsheet_to_facts", "factsheet_to_opinions",
    "load_factsheets",
]

FUND_FACTS_COLUMNS: tuple[str, ...] = (
    "amfi_scheme_code", "scripbox_fund_id", "fund_slug", "plan_id", "sub_plan_id",
    "plan_name", "rta_scheme_code", "asset_class", "asset_class_code",
    "sub_asset_class", "sub_asset_class_code", "sebi_category_name", "taxability",
    "openended", "aum", "expense_ratio", "face_value", "inception_date",
    "status", "transaction_status", "is_active_status", "is_investable",
    "is_purchase_allowed", "is_withdrawal_allowed", "is_sip_allowed",
    "is_stp_allowed", "is_swp_allowed", "is_switch_in_allowed",
    "is_switch_out_allowed", "is_nfo", "min_initial_investment_amount",
    "min_subsequent_investment_amount", "min_withdrawal_amount",
    "min_investment_multiples", "returns_as_on_date", "return_1day",
    "return_3month", "return_6month", "return_1year", "return_2year",
    "return_3year", "return_4year", "return_5year", "return_7year",
    "return_10year", "return_since_launch", "source_nav", "source_nav_date",
    "composition", "sectorwise_holding", "asset_holding", "holdings_maturity",
    "exit_load", "sip", "stp", "swp", "stats_variables", "category_return",
    "fund_manager", "fund_variant", "source", "source_updated_at", "fetched_at",
    "raw_payload",
)

FUND_FACTS_TYPES: dict[str, str] = {
    "amfi_scheme_code": "integer",
    "scripbox_fund_id": "uuid",
    "plan_id": "integer",
    "openended": "boolean", "is_active_status": "boolean", "is_investable": "boolean",
    "is_purchase_allowed": "boolean", "is_withdrawal_allowed": "boolean",
    "is_sip_allowed": "boolean", "is_stp_allowed": "boolean", "is_swp_allowed": "boolean",
    "is_switch_in_allowed": "boolean", "is_switch_out_allowed": "boolean",
    "is_nfo": "boolean",
    "aum": "numeric", "expense_ratio": "numeric", "face_value": "numeric",
    "min_initial_investment_amount": "numeric",
    "min_subsequent_investment_amount": "numeric", "min_withdrawal_amount": "numeric",
    "min_investment_multiples": "numeric",
    "return_1day": "numeric", "return_3month": "numeric", "return_6month": "numeric",
    "return_1year": "numeric", "return_2year": "numeric", "return_3year": "numeric",
    "return_4year": "numeric", "return_5year": "numeric", "return_7year": "numeric",
    "return_10year": "numeric", "return_since_launch": "numeric",
    "source_nav": "numeric",
    "inception_date": "date", "returns_as_on_date": "date", "source_nav_date": "date",
    "composition": "jsonb", "sectorwise_holding": "jsonb", "asset_holding": "jsonb",
    "holdings_maturity": "jsonb", "exit_load": "jsonb", "sip": "jsonb", "stp": "jsonb",
    "swp": "jsonb", "stats_variables": "jsonb", "category_return": "jsonb",
    "fund_manager": "jsonb", "fund_variant": "jsonb", "raw_payload": "jsonb",
    "source_updated_at": "timestamptz", "fetched_at": "timestamptz",
}

FUND_OPINIONS_COLUMNS: tuple[str, ...] = (
    "amfi_scheme_code", "provider", "sb_recommendation",
    "fund_recommendation_rating", "es_score", "fund_score", "risk_level",
    "objective", "portfolio_audit_ic_blacklist", "source_updated_at", "fetched_at",
)

FUND_OPINIONS_TYPES: dict[str, str] = {
    "amfi_scheme_code": "integer",
    "sb_recommendation": "boolean", "portfolio_audit_ic_blacklist": "boolean",
    "fund_recommendation_rating": "integer", "es_score": "numeric",
    "fund_score": "jsonb",
    "source_updated_at": "timestamptz", "fetched_at": "timestamptz",
}


def _num(v: Any) -> Optional[str]:
    if v is None or v == "":
        return None
    try:
        return str(Decimal(str(v)))
    except Exception:
        return None


def _int(v: Any) -> Optional[str]:
    if v is None or v == "":
        return None
    try:
        return str(int(v))
    except (ValueError, TypeError):
        return None


def _bool(v: Any) -> Optional[str]:
    if v is None:
        return None
    return "true" if bool(v) else "false"


def _date(v: Any) -> Optional[str]:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _ts(v: Any) -> Optional[str]:
    """ISO timestamp or None. Accepts '2026-08-26T01:21:06Z'."""
    if not v:
        return None
    s = str(v).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return _date(s)


def _json(v: Any) -> Optional[str]:
    if v is None:
        return None
    try:
        return json.dumps(v, default=str)
    except (TypeError, ValueError):
        return None


def factsheet_to_facts(fs: dict[str, Any]) -> Optional[tuple]:
    """Map one ``factsheetData`` dict to a mf.fund_facts row. None if no code."""
    code = fs.get("amfi_code")
    if not code or not str(code).strip().isdigit():
        return None
    now = datetime.now().isoformat()
    ret_as_on = (fs.get("category_return") or {}).get("as_on_date")
    row = {
        "amfi_scheme_code": _int(code),
        "scripbox_fund_id": fs.get("fund_id"),
        "fund_slug": fs.get("fund_slug"),
        "plan_id": _int(fs.get("plan_id")),
        "sub_plan_id": fs.get("sub_plan_id"),
        "plan_name": fs.get("plan_name"),
        "rta_scheme_code": fs.get("rta_scheme_code"),
        "asset_class": fs.get("asset_class"),
        "asset_class_code": fs.get("asset_class_code"),
        "sub_asset_class": fs.get("sub_asset_class"),
        "sub_asset_class_code": fs.get("sub_asset_class_code"),
        "sebi_category_name": fs.get("sebi_category_name"),
        "taxability": fs.get("taxability"),
        "openended": _bool(fs.get("openended")),
        "aum": _num(fs.get("aum")),
        "expense_ratio": _num(fs.get("expense_ratio")),
        "face_value": _num(fs.get("face_value")),
        "inception_date": _date(fs.get("inception_date")),
        "status": fs.get("status"),
        "transaction_status": fs.get("transaction_status"),
        "is_active_status": _bool(fs.get("is_active_status")),
        "is_investable": _bool(fs.get("is_investable")),
        "is_purchase_allowed": _bool(fs.get("is_purchase_allowed")),
        "is_withdrawal_allowed": _bool(fs.get("is_withdrawal_allowed")),
        "is_sip_allowed": _bool(fs.get("is_sip_allowed")),
        "is_stp_allowed": _bool(fs.get("is_stp_allowed")),
        "is_swp_allowed": _bool(fs.get("is_swp_allowed")),
        "is_switch_in_allowed": _bool(fs.get("is_switch_in_allowed")),
        "is_switch_out_allowed": _bool(fs.get("is_switch_out_allowed")),
        "is_nfo": _bool(fs.get("is_nfo")),
        "min_initial_investment_amount": _num(fs.get("min_initial_investment_amount")),
        "min_subsequent_investment_amount": _num(fs.get("min_subsequent_investment_amount")),
        "min_withdrawal_amount": _num(fs.get("min_withdrawal_amount")),
        "min_investment_multiples": _num(fs.get("min_investment_multiples")),
        "returns_as_on_date": _date(ret_as_on),
        "return_1day": _num(fs.get("return_1day")),
        "return_3month": _num(fs.get("return_3month")),
        "return_6month": _num(fs.get("return_6month")),
        "return_1year": _num(fs.get("return_1year")),
        "return_2year": _num(fs.get("return_2year")),
        "return_3year": _num(fs.get("return_3year")),
        "return_4year": _num(fs.get("return_4year")),
        "return_5year": _num(fs.get("return_5year")),
        "return_7year": _num(fs.get("return_7year")),
        "return_10year": _num(fs.get("return_10year")),
        "return_since_launch": _num(fs.get("return_since_launch")),
        "source_nav": _num(fs.get("nav")),
        "source_nav_date": _date(fs.get("nav_date")),
        "composition": _json(fs.get("composition")),
        "sectorwise_holding": _json(fs.get("sectorwise_holding")),
        "asset_holding": _json(fs.get("asset_holding")),
        "holdings_maturity": _json(fs.get("holdings_maturity")),
        "exit_load": _json(fs.get("exit_load")),
        "sip": _json(fs.get("sip")),
        "stp": _json(fs.get("stp")),
        "swp": _json(fs.get("swp")),
        "stats_variables": _json(fs.get("stats_variables")),
        "category_return": _json(fs.get("category_return")),
        "fund_manager": _json(fs.get("fund_manager")),
        "fund_variant": _json(fs.get("fund_variant")),
        "source": "SCRIPBOX",
        "source_updated_at": _ts(fs.get("updated_at")),
        "fetched_at": now,
        "raw_payload": _json(fs),
    }
    return tuple(row.get(c) for c in FUND_FACTS_COLUMNS)


def factsheet_to_opinions(fs: dict[str, Any]) -> Optional[tuple]:
    """Map one ``factsheetData`` dict to a mf.fund_opinions row (licensing-sensitive)."""
    code = fs.get("amfi_code")
    if not code or not str(code).strip().isdigit():
        return None
    now = datetime.now().isoformat()
    row = {
        "amfi_scheme_code": _int(code),
        "provider": "SCRIPBOX",
        "sb_recommendation": _bool(fs.get("sb_recommendation")),
        "fund_recommendation_rating": _int(fs.get("fund_recommendation_rating")),
        "es_score": _num(fs.get("es_score")),
        "fund_score": _json(fs.get("fund_score")),
        "risk_level": fs.get("risk_level"),
        "objective": fs.get("objective"),
        "portfolio_audit_ic_blacklist": _bool(fs.get("portfolio_audit_ic_blacklist")),
        "source_updated_at": _ts(fs.get("updated_at")),
        "fetched_at": now,
    }
    return tuple(row.get(c) for c in FUND_OPINIONS_COLUMNS)


def load_factsheets(store: Any, factsheets: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Upsert fund_facts + fund_opinions from an iterable of factsheetData dicts.

    Skips any factsheet whose amfi_code is not present in mf.funds.
    """
    existing = store.fund_codes()
    facts_rows, opinion_rows = [], []
    skipped = 0
    for fs in factsheets:
        fr = factsheet_to_facts(fs)
        if fr is None:
            skipped += 1
            continue
        code = int(fr[0])
        if code not in existing:
            skipped += 1
            continue
        facts_rows.append(fr)
        opinion_rows.append(factsheet_to_opinions(fs))
    return {
        "fund_facts": store.upsert_table(
            "fund_facts", "amfi_scheme_code", FUND_FACTS_COLUMNS,
            iter(facts_rows), column_types=FUND_FACTS_TYPES).as_dict(),
        "fund_opinions": store.upsert_table(
            "fund_opinions", "amfi_scheme_code", FUND_OPINIONS_COLUMNS,
            iter(opinion_rows), column_types=FUND_OPINIONS_TYPES).as_dict(),
        "skipped": skipped,
    }
