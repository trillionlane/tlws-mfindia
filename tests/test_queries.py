"""Unit tests for the pure query helpers in mfdataindia.api.queries."""

from __future__ import annotations

import pytest

from mfdataindia.api.queries import fund_family


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