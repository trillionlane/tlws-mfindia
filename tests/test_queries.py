"""Unit tests for the pure query helpers in mfdataindia.api.queries."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from mfdataindia.api.app import _plan_scope_or_422
from mfdataindia.api.queries import (
    DEFAULT_PLAN_SCOPE,
    _plan_predicate,
    fund_family,
    plan_scope,
)

# The authoritative served-plan rule, spelled out here independently of
# queries.py so that changing either side breaks a test.
_IN_SCOPE_RULE = (
    "f.plan_type = 'REGULAR'"
    " OR (f.plan_type = 'UNLABELLED' AND NOT f.is_etf AND f.plan_source = 'NAME')"
)
_SCHEMA_FILE = Path(__file__).resolve().parents[1] / "sql" / "001_core_schema.sql"


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


def test_regular_scope_is_the_authoritative_rule_not_a_plan_type_list():
    # Review [P1]: `plan_type IN ('REGULAR', 'UNLABELLED')` admitted every
    # plan-unknown UNLABELLED row the curation contract rejects -- 5,705 of them
    # on the live feed, 57% of the table -- and they reappeared exactly where this
    # PR deliberately bypasses curation. UNLABELLED is served ONLY when the plan
    # was inferred from the name (legacy feed, no Plan column) and the scheme is
    # not an ETF.
    assert _plan_predicate() == f"({_IN_SCOPE_RULE})"
    assert _plan_predicate("direct") == "f.plan_type = 'DIRECT'"
    assert _plan_predicate("all") == "TRUE"
    # Aliased call sites (sibling navigation) must qualify all three columns.
    assert _plan_predicate("regular", alias="f2") == f"({_IN_SCOPE_RULE.replace('f.', 'f2.')})"


def test_regular_scope_predicate_matches_the_generated_column_text():
    # Textual drift guard: the schema owns the rule, queries.py mirrors it
    # deliberately (the scope must hold where in_scope is not consulted).
    # The behavioural guard is in tests/test_plan_scope.py, which evaluates the
    # emitted predicate against the live column over every row.
    #
    # The alternation allows exactly one level of nesting: a lazy match would
    # stop at the inner ')' (dropping it) and a greedy one would run on to the
    # NEXT generated column in the table (isin_primary ... STORED).
    generated = re.search(
        r"in_scope\s+boolean GENERATED ALWAYS AS \(((?:[^()]|\([^()]*\))*)\)\s*STORED",
        _SCHEMA_FILE.read_text(encoding="utf-8"),
        re.S,
    )
    assert generated, "mf.funds.in_scope is no longer a GENERATED ... STORED column"
    assert " ".join(generated.group(1).split()) == _IN_SCOPE_RULE.replace("f.", "")


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
