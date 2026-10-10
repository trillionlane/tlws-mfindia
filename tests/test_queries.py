"""Unit tests for the pure query helpers in mfdataindia.api.queries."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from mfdataindia.api.app import _plan_scope_or_422
from mfdataindia.api.queries import (
    DEFAULT_PLAN_SCOPE,
    _plan_predicate,
    fund_family,
    plan_scope,
)


@pytest.mark.parametrize(
    ("category", "family"),
    [
        # equity (current + legacy AMFI naming, plus standalone ELSS)
        ("Equity Scheme - Large Cap Fund", "Equity"),
        ("Equity Schemes - Thematic Fund", "Equity"),
        ("Equity Scheme - ELSS", "Equity"),
        ("ELSS", "Equity"),
        # debt (legacy "Income/Debt Oriented Schemes", current, legacy bare "Income")
        ("Income/Debt Oriented Schemes - Liquid Fund", "Debt"),
        ("Income/Debt Oriented Schemes - Ultra Short Term Fund", "Debt"),
        ("Debt Scheme - Gilt Fund", "Debt"),
        ("Income", "Debt"),
        # hybrid (legacy plural too)
        ("Hybrid Scheme - Arbitrage Fund", "Hybrid"),
        ("Hybrid Schemes - Aggressive Hybrid Fund", "Hybrid"),
        # ETFs (buried in "Other Scheme - ..." and the dedicated ETF category)
        ("Other Scheme - Other  ETFs", "ETF"),
        ("Other Scheme - Gold ETF", "ETF"),
        ("Exchange Traded Funds (ETFs) - Equity ETF", "ETF"),
        # index funds (dedicated + legacy "Other Scheme" bucket)
        ("Index Funds - Equity Funds", "Index"),
        ("Other Scheme - Index Funds", "Index"),
        # fund of funds
        ("Fund of Funds Scheme (Domestic) - Fund of Funds Scheme (Domestic)", "FoF"),
        ("Other Scheme - FoF Overseas", "FoF"),
        ("Overseas Fund of Funds - Fund of Funds investing overseas", "FoF"),
        # solution oriented
        ("Solution Oriented Scheme - Retirement Fund", "Solution"),
        ("Solution Oriented Schemes ** - Retirement Fund", "Solution"),
        ("Children’s Fund - Childrens' Fund", "Solution"),
        # fallthroughs
        ("", "Other"),
        (None, "Other"),
        ("Some Unknown Category", "Other"),
    ],
)
def test_fund_family_mapping(category, family):
    assert fund_family(category) == family


# -- plan scope policy --------------------------------------------------------

def test_default_plan_scope_is_regular():
    assert DEFAULT_PLAN_SCOPE == "regular"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "regular"),
        ("", "regular"),
        ("regular", "regular"),
        ("REGULAR", "regular"),
        ("  Direct  ", "direct"),
        ("ALL", "all"),
    ],
)
def test_plan_scope_normalises(raw, expected):
    assert plan_scope(raw) == expected


@pytest.mark.parametrize("raw", ["bogus", "regular,direct", "any", "everything", "plan"])
def test_plan_scope_rejects_unknown(raw):
    with pytest.raises(ValueError, match="unknown plan"):
        plan_scope(raw)


def test_plan_predicate_serves_regular_and_name_inferred_only():
    # UNLABELLED is in the default scope because in_scope admits name-inferred
    # Regular rows; DIRECT / RETAIL / INSTITUTIONAL never are.
    assert _plan_predicate() == "f.plan_type IN ('REGULAR', 'UNLABELLED')"
    assert _plan_predicate("direct") == "f.plan_type IN ('DIRECT')"
    assert _plan_predicate("all") == "TRUE"
    assert _plan_predicate("regular", alias="f2") == "f2.plan_type IN ('REGULAR', 'UNLABELLED')"


def test_plan_predicate_cannot_be_injected():
    # A hostile value is rejected before any SQL is built, and the emitted
    # fragment only ever contains whitelist literals.
    with pytest.raises(ValueError):
        _plan_predicate("REGULAR'); DROP TABLE mf.funds; --")


def test_route_helper_rejects_unknown_scope_before_the_database():
    assert _plan_scope_or_422("all") == "all"
    assert _plan_scope_or_422("Regular") == "regular"
    with pytest.raises(HTTPException) as raised:
        _plan_scope_or_422("everything")
    assert raised.value.status_code == 422
    assert "unknown plan" in str(raised.value.detail)
