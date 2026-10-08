"""Unit tests for the AMC-factsheet parser (ABSL) and name matching.

Fixtures are verbatim pypdf-extracted pages from the real ABSL September
2026 "Empower" factsheet (tests/fixtures/absl/), so the tests pin the
production layout — a parser regression fails here, not at 2 a.m. during
a full enrichment run.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from mfdataindia.ingest.amc_factsheets import (
    AbSLParser,
    NameMatcher,
    NoMatchError,
    to_num,
)
FIX = Path(__file__).parent / "fixtures" / "absl"


def _fix(name: str) -> str:
    return (FIX / name).read_text()


class TestToNum:
    @pytest.mark.parametrize("raw,expected", [
        ("NA", None), ("N/A", None), ("", None), (None, None),
        ("0.97", 0.97), ("-0.69%", -0.69), ("14.08", 14.08), (" 3 ", 3.0),
        ("junk", None),
    ])
    def test_values(self, raw, expected):
        assert to_num(raw) == expected


class TestNameMatcher:
    def test_exact_fold_match(self):
        m = NameMatcher({"aditya birla sun life large cap fund": 100})
        assert m.match("Aditya Birla Sun Life Large Cap Fund") == 100

    def test_ampersand_vs_and(self):
        # AMFI spells "Banking & PSU"; the ABSL factsheet spells "Banking and PSU".
        m = NameMatcher({"Aditya Birla Sun Life Banking & PSU Debt Fund": 108273})
        assert m.match("Aditya Birla Sun Life Banking and PSU Debt Fund") == 108273

    def test_no_match(self):
        m = NameMatcher({"Some Other Fund": 1})
        with pytest.raises(NoMatchError):
            m.match("Aditya Birla Sun Life Large Cap Fund")

    def test_and_only_matching_is_deterministic(self):
        # The &/and pair: "Alpha & Beta Fund" (AMFI spelling) folds to
        # "ALPHA BETA FUND"; "Alpha and Beta Fund" (factsheet spelling) folds
        # to "ALPHA AND BETA FUND". Each factsheet spelling resolves via its
        # own key, so the pair can never be confused:
        m = NameMatcher({"Alpha & Beta Fund": 1, "Alpha and Beta Fund": 2})
        assert m.match("Alpha & Beta Fund") == 1      # exact (fold)
        assert m.match("Alpha and Beta Fund") == 2    # exact (fold)
        # A factsheet spelling that only reaches the AND-stripped key still
        # resolves deterministically to the & candidate…
        assert m.match("Alpha Beta Fund") == 1
        # …and a name with no candidate at all is a clean no-match.
        with pytest.raises(NoMatchError):
            m.match("Gamma Fund")


class TestAbSLQuant:
    def setup_method(self):
        self.parser = AbSLParser("September 2026")

    def test_populated_quant_equity_fund(self):
        q = self.parser._parse_quant(_fix("detail_largecap.txt"))
        assert q.std_deviation_pct == 14.08
        assert q.beta == 0.97
        assert q.sharpe_ratio == 0.38
        assert q.information_ratio == -0.45
        assert q.tracking_error_1y is None   # published NA

    def test_na_quant_debt_fund(self):
        # The point of the POC: debt funds publish NA — parsed as None,
        # never a crash and never a fabricated number.
        q = self.parser._parse_quant(_fix("detail_bankpsu.txt"))
        assert q.std_deviation_pct is None
        assert q.beta is None
        assert q.sharpe_ratio is None
        assert q.information_ratio is None

    def test_fund_name_anchor(self):
        assert self.parser._fund_name(_fix("detail_bankpsu.txt")) == \
            "Aditya Birla Sun Life Banking and PSU Debt Fund"
        assert self.parser._fund_name(_fix("detail_largecap.txt")) == \
            "Aditya Birla Sun Life Large Cap Fund"


class TestAbSLDetailPage:
    def setup_method(self):
        self.parser = AbSLParser("September 2026")

    def test_benchmark_wrapped_lines(self):
        detail = _fix("detail_bankpsu.txt")
        bench = self.parser._grab(detail, r"Benchmark Name\s*(.*?)\s*NAV as on", re.S)
        bench = re.sub(r"\s+", " ", bench).strip()
        assert bench == "Nifty Banking & PSU Debt Index A-II"

    def test_detail_fields(self):
        fs = self.parser._parse_one([_fix("detail_bankpsu.txt")], 0)
        assert fs.scheme_name == "Aditya Birla Sun Life Banking and PSU Debt Fund"
        assert fs.inception_date == date(2002, 4, 19)
        assert fs.nav_date == date(2026, 8, 31)
        assert fs.nav_growth_regular == 387.4003
        assert fs.aum_crore == 8682.46
        assert fs.expense_ratio_regular == 0.62
        assert fs.expense_ratio_direct == 0.33
        assert fs.benchmark_name == "Nifty Banking & PSU Debt Index A-II"
        assert fs.factsheet_period == "September 2026"
        assert fs.page == 1


class TestAbSLPerformancePage:
    def setup_method(self):
        self.parser = AbSLParser("September 2026")
        self.name = "Aditya Birla Sun Life Banking and PSU Debt Fund"

    def test_three_rows_and_values(self):
        rows = self.parser._parse_perf(_fix("perf_bankpsu.txt"), self.name)
        by_role = {r.role: r for r in rows}
        assert set(by_role) == {"scheme", "benchmark", "additional_benchmark"}
        s = by_role["scheme"]
        assert (s.since_inception, s.return_10y, s.return_5y,
                s.return_3y, s.return_1y) == (7.66, 6.84, 5.89, 6.72, 4.89)
        b = by_role["benchmark"]
        assert b.name == "Nifty Banking & PSU Debt Index A-II"
        assert (b.since_inception, b.return_10y, b.return_5y,
                b.return_3y, b.return_1y) == (7.59, 6.62, 5.63, 6.69, 4.87)
        a = by_role["additional_benchmark"]
        assert a.name == "10 Year Dated GOI"
        assert (a.since_inception, a.return_10y, a.return_5y,
                a.return_3y, a.return_1y) == (-0.69, -0.23, 2.24, -1.03, 5.71)

    def test_scheme_row_not_matched_in_top_header(self):
        # The fund name also appears in the page header (no numbers) — the
        # parser must still find the numbered row exactly once.
        rows = self.parser._parse_perf(_fix("perf_bankpsu.txt"), self.name)
        assert sum(1 for r in rows if r.role == "scheme") == 1


class TestAbSLFullBlock:
    def test_parse_pages_three_page_block(self):
        pages = [
            _fix("detail_bankpsu.txt"),     # detail
            "changes page (irrelevant)",    # major changes
            _fix("perf_bankpsu.txt"),       # performance
        ]
        out = list(AbSLParser("September 2026").parse_pages(pages))
        assert len(out) == 1
        fs = out[0]
        assert fs.scheme_name == "Aditya Birla Sun Life Banking and PSU Debt Fund"
        assert fs.page == 1
        assert fs.quant.beta is None            # published NA
        assert fs.benchmark_name == "Nifty Banking & PSU Debt Index A-II"
        assert fs.additional_benchmark_name == "10 Year Dated GOI"
        assert fs.fund_category == "Banking and PSU Debt Fund"
        roles = {r.role: r for r in fs.performance}
        assert roles["scheme"].return_1y == 4.89