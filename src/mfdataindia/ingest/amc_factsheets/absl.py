"""ABSL (Aditya Birla Sun Life) factsheet parser.

Layout (verified against the consolidated "Empower" monthly factsheet —
September 2026 edition, 344 pages). One base scheme occupies a 3-page
block:

    page 1  detail  — fund description, "Fund Details" (inception,
                      Benchmark Name, NAV as on, Regular/Direct NAVs, AUM,
                      BER), and the "Quantitative Measures*" block
                      (1 std dev, 2 beta, 3 tracking error, 4 Sharpe,
                      5 tracking difference, 6 information ratio)
    page 2  changes — top-10 holdings changes, risk-o-meter change,
                      methodology descriptions
    page 3  perf    — "Investment Performance": CAGR table
                      (Since Inception / 10Y / 5Y / 3Y / 1Y) for the
                      scheme, "Benchmark - <name>", "Additional
                      Benchmark - <name>"

The performance figures are stated to be *Regular Plan - Growth Option*,
which is exactly the variant the loader fills.

Notes
-----
* Benchmark names wrap across lines ("Nifty Banking & PSU \nDebt Index
  A-II") — they are joined, not split.
* Metrics may be published as "NA" (all ABSL debt / money-market
  schemes). That normalises to ``None`` == "not fillable from this
  source"; the loader records the field either way so coverage is honest.
* Returns-table values may also be "NA" (e.g. a benchmark not available
  for a horizon), so value tokens are ``number%`` *or* ``NA``.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable, Iterator, List, Optional

from .base import (
    AmcFactsheetParser,
    PerformanceRow,
    QuantMetrics,
    SchemeFactsheet,
    to_num,
)

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December"
)
#: One returns-table value: a (signed) percent, or a bare NA token.
_VAL = r"(?:-?[0-9.]+%|\bNA\b)"
#: Quantitative-measures value: the (signalling) (%) sits in the LABEL, so
#: values are bare numbers or NA (e.g. "2 Beta 0.97", "2 Beta NA").
_QVAL = r"(?:-?[0-9.]+|\bNA\b)"
#: A row's trailing metrics: exactly five value tokens.
_FIVE = r"(?:" + r"\s+" + _VAL + r"){5}"


def _clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _norm_index(s: Optional[str]) -> Optional[str]:
    """Clean an index name.

    pypdf's text extraction splits index names around hyphens on kerning
    boundaries ("Debt Index A -II" for "A-II"); collapsing spaces around
    hyphens restores the published spelling. Applied to benchmark names
    only — never to fund names.
    """
    s = _clean(s)
    if s is None:
        return None
    return re.sub(r"\s*-\s*", "-", s)


def _parse_date(v: str) -> Optional[date]:
    for fmt in ("%d %B %Y", "%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _last5(row: str) -> List[Optional[float]]:
    """The trailing five value tokens of a returns-table row -> floats."""
    toks = re.findall(r"-?[0-9.]+%|\bNA\b", row)
    return [to_num(t) for t in toks[-5:]]


class AbSLParser(AmcFactsheetParser):
    """Parser for ABSL's consolidated monthly factsheet PDF."""

    amc = "absl"

    def __init__(self, factsheet_period: str):
        # e.g. "September 2026" — taken from the document title, not the page.
        self.factsheet_period = factsheet_period

    # -- page triage ---------------------------------------------------------

    @staticmethod
    def _is_detail(text: str) -> bool:
        # The "Quantitative Measures*" block (with metric numbers) plus the
        # "Fund Details" section only appear together on the detail page.
        return "Quantitative Measures*" in text and "Fund Details" in text

    def parse_pages(self, pages: Iterable[str]) -> Iterator[SchemeFactsheet]:
        page_list = list(pages)
        for i, t in enumerate(page_list):
            if self._is_detail(t):
                fs = self._parse_one(page_list, i)
                if fs is not None:
                    yield fs

    # -- assembly ------------------------------------------------------------

    def _parse_one(self, page_list: List[str], i: int) -> Optional[SchemeFactsheet]:
        detail = page_list[i]
        name = self._fund_name(detail)
        if not name:
            return None
        fs = SchemeFactsheet(
            amc=self.amc,
            scheme_name=name,
            factsheet_period=self.factsheet_period,
            page=i + 1,
            raw_detail=detail,
        )
        fs.quant = self._parse_quant(detail)
        fs.benchmark_name = _norm_index(self._grab(
            detail, r"Benchmark Name\s*(.*?)\s*NAV as on", re.S))
        v = self._grab(detail,
                       r"Date of Inception / Allotment\s*([0-9]{1,2} [A-Z][a-z]+ [0-9]{4})")
        fs.inception_date = _parse_date(v) if v else None
        v = self._grab(detail, r"NAV as on\s*((?:" + _MONTHS + r") [0-9]{1,2}, [0-9]{4})")
        fs.nav_date = _parse_date(v) if v else None
        fs.nav_growth_regular = to_num(self._grab(
            detail, r"Regular Plan - Growth Option\s*[₹$]\s*([0-9.]+)"))
        aum = self._grab(detail,
                         r"AUM as on [A-Z][a-z]+ [0-9]{1,2}, [0-9]{4}\s*([0-9,\.]+)")
        fs.aum_crore = to_num(aum.replace(",", "")) if aum else None
        fs.expense_ratio_regular = to_num(self._grab(
            detail, r"Regular Plan:\s*BER\s*([0-9.]+)%"))
        fs.expense_ratio_direct = to_num(self._grab(
            detail, r"Direct Plan:\s*BER\s*([0-9.]+)%"))

        perf = self._find_perf(page_list, i, name)
        if perf is not None:
            fs.performance = self._parse_perf(perf, name)
            # "Fund Category" sits on the performance page (after the
            # investment objective), not on the detail page.
            fs.fund_category = _clean(self._grab(
                perf, r"Fund Category:\s*(.+?)\s*\n"))
            for row in fs.performance:
                if row.role == "benchmark" and not fs.benchmark_name:
                    fs.benchmark_name = row.name
                if row.role == "additional_benchmark":
                    fs.additional_benchmark_name = row.name
        else:
            fs.fund_category = _clean(self._grab(detail, r"Fund Category:\s*(.+?)\s*\n"))
        return fs

    # -- field extractors ----------------------------------------------------

    @staticmethod
    def _grab(text: str, pattern: str, flags: int = 0) -> Optional[str]:
        m = re.search(pattern, text, flags)
        return m.group(1) if m else None

    @staticmethod
    def _fund_name(detail: str) -> Optional[str]:
        # The full "Aditya Birla Sun Life … Fund" name is followed on the
        # detail page by either the "Type of Scheme:" line or the month/page
        # footer. Anchoring on that keeps us from grabbing a truncated name.
        m = re.search(
            r"(Aditya Birla Sun Life [A-Z][A-Za-z0-9&\-\s/]*?Fund)"
            r"(?:\s*\nType of Scheme|\s*\n(?:" + _MONTHS + r") \d{4})",
            detail)
        return _clean(m.group(1)) if m else None

    @staticmethod
    def _parse_quant(text: str) -> QuantMetrics:
        q = QuantMetrics()
        q.std_deviation_pct = to_num(AbSLParser._grab(
            text, r"1 Standard Deviation \(%\)\s*(" + _QVAL + r")"))
        q.beta = to_num(AbSLParser._grab(text, r"2 Beta\s*(" + _QVAL + r")"))
        q.sharpe_ratio = to_num(AbSLParser._grab(
            text, r"4 Sharpe Ratio \(%\)\s*(" + _QVAL + r")"))
        q.information_ratio = to_num(AbSLParser._grab(
            text, r"6 Information Ratio\s*(" + _QVAL + r")"))
        # The two tracking-error values are separated by "1 year"/"3 years"
        # labels, so the labels must be part of the pattern.
        m = re.search(
            r"3 Tracking Error \(%\)\s*1 year\s*(" + _QVAL + r")\s*3 years\s*("
            + _QVAL + r")", text)
        if m:
            q.tracking_error_1y = to_num(m.group(1))
            q.tracking_error_3y = to_num(m.group(2))
        return q

    def _find_perf(self, page_list: List[str], i: int,
                   name: str) -> Optional[str]:
        for j in range(i, min(i + 4, len(page_list))):
            t = page_list[j]
            if "Investment Performance" in t and name in t:
                return t
        return None

    def _parse_perf(self, perf: str, name: str) -> List[PerformanceRow]:
        m = re.search(
            r"Investment Performance(.*?)(?:Past performancemay|SIP Performance)",
            perf, re.S)
        section = m.group(1) if m else perf
        rows: List[PerformanceRow] = []

        m = re.search(re.escape(name) + r"\s*" + _FIVE, section, re.S)
        if m:
            rows.append(PerformanceRow("scheme", name, *_last5(m.group(0))))

        # "Additional Benchmark -" first, then a plain "Benchmark -" that is
        # NOT preceded by "Additional " (fixed-width lookbehind).
        m = re.search(r"Additional Benchmark - (.*?)" + _FIVE, section, re.S)
        if m:
            rows.append(PerformanceRow(
                "additional_benchmark", _norm_index(m.group(1)),
                *_last5(m.group(0))))
        m = re.search(r"(?<!Additional )Benchmark - (.*?)" + _FIVE, section, re.S)
        if m:
            rows.append(PerformanceRow(
                "benchmark", _norm_index(m.group(1)), *_last5(m.group(0))))
        return rows
