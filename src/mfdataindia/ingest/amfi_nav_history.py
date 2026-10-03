"""Parser for AMFI's bulk NAV *history* report.

``portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt=..&todt=..&tp=..&mc=..``
returns every scheme's NAV for a date range — the mechanism used to backfill the
required 5-year window and to serve history without relying on api.mfapi.in.

The layout shares the 8-column width of the current ``NAVAll.txt`` but the column
ORDER differs, so it cannot reuse the daily parser:

    Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date

    139618;Taurus Investor Education Pool - Unclaimed Redemption - Growth;;Growth;;;10.0000;01-Jan-2024

Differences from ``NAVAll.txt``:

* column order is ``code;name;plan;option;isin_a;isin_b;nav;date``;
* section headers use spaced parens, e.g. ``Open Ended Schemes ( Money Market )``;
* one scheme appears once per NAV date in the range, so this parser yields many
  rows per scheme — feed them to the NAV loader, not the scheme loader;
* ``tp`` selects scheme type: 1 = Open Ended, 2 = Close Ended, 3 = Interval.

Plan/option classification reuses :mod:`mfdataindia.ingest.amfi_navall` so the two
sources can never disagree on the rule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from mfdataindia.ingest.amfi_navall import (
    AmfiScheme,
    ParseReport,
    _HEADER_PREFIX_HISTORY,
    _NAV_NOT_PUBLISHED,
    _SECTION_RE,
    _clean_isin,
    _parse_date,
    _parse_nav,
    resolve_option,
    resolve_plan,
)

__all__ = ["parse_nav_history_report", "parse_nav_history_file"]


def parse_nav_history_report(
    text: str,
    *,
    max_quarantine_samples: int = 25,
) -> tuple[list[AmfiScheme], ParseReport]:
    """Parse the bulk NAV-history report into per-(scheme, date) rows.

    Returns ``(rows, report)``. ``rows`` is one :class:`AmfiScheme` per NAV point
    (so the same ``amfi_scheme_code`` repeats across dates). Only the NAV-point
    fields — code, date, NAV — are reliable here; scheme identity is the job of
    the daily ``NAVAll.txt`` load, which is authoritative for ``mf.funds``.
    """
    report = ParseReport()
    report.format = "NAVHIST_8COL"
    rows: list[AmfiScheme] = []

    cur_type: Optional[str] = None
    cur_cat: Optional[str] = None
    cur_amc: Optional[str] = None

    def _quarantine(reason: str, line_no: int, raw: str) -> None:
        report.quarantined += 1
        report.quarantine_reasons[reason] += 1
        if len(report.quarantined_samples) < max_quarantine_samples:
            report.quarantined_samples.append(
                {"line_no": line_no, "reason": reason, "raw": raw[:300]})

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        report.total_lines += 1
        s = raw_line.strip()
        if not s:
            continue
        if s.startswith(_HEADER_PREFIX_HISTORY):
            report.header_rows += 1
            continue

        if s.count(";") != 7:
            m = _SECTION_RE.match(s)
            if m:
                cur_type = m.group("type").strip()
                cur_cat = m.group("cat").strip()
                report.section_headers += 1
                continue
            if ";" not in s and not s.startswith("<"):
                cur_amc = s
                report.amc_headers += 1
                continue
            _quarantine("unclassified_line", line_no, s)
            continue

        (code, name, plan_cell, option_cell,
         isin_a, isin_b, nav_raw, date_raw) = [p.strip() for p in s.split(";")]

        if not code.isdigit():
            _quarantine("non_numeric_scheme_code", line_no, s)
            continue
        if not name:
            _quarantine("empty_scheme_name", line_no, s)
            continue

        nav = _parse_nav(nav_raw)
        nav_date = _parse_date(date_raw)
        nav_np = nav_raw.strip().lower() in _NAV_NOT_PUBLISHED
        if nav is None:
            if nav_np:
                report.nav_not_published += 1
            else:
                report.unparsed_nav += 1
        if nav_date is None:
            report.unparsed_date += 1

        plan, plan_src = resolve_plan(name, plan_cell)
        option, periodicity, option_src = resolve_option(name, option_cell)
        report.plan_class_source[plan_src] += 1
        report.option_class_source[option_src] += 1

        up_name = name.upper()
        is_etf = "ETF" in up_name
        is_defunct = ("DEFUNCT" in up_name or up_name.startswith("OLD-")
                      or nav_np or (nav is not None and nav <= 0))

        warnings: list[str] = []
        if cur_type is None or cur_amc is None:
            warnings.append("no_section_or_amc_header")
        if nav is None:
            warnings.append("nav_not_published" if nav_np else "unparsed_nav")
        if nav_date is None:
            warnings.append("unparsed_date")
        if plan_src == "COLUMN_BLANK":
            warnings.append("plan_column_blank")

        rows.append(AmfiScheme(
            amfi_scheme_code=code,
            scheme_name=name,
            nav=nav,
            nav_date=nav_date,
            scheme_type=cur_type,
            scheme_category=cur_cat,
            amc=cur_amc,
            isin_div_payout_or_growth=_clean_isin(isin_a),
            isin_div_reinvestment=_clean_isin(isin_b),
            plan_type=plan,
            option=option,
            periodicity=periodicity,
            is_etf=is_etf,
            is_defunct=is_defunct,
            nav_not_published=nav_np,
            # History rows never decide scope; mf.funds (from NAVAll.txt) does.
            in_scope=False,
            plan_source=plan_src,
            option_source=option_src,
            line_no=line_no,
            warnings=warnings,
        ))
        report.data_rows += 1
        report.plan_types[plan] += 1
        report.options[option] += 1

    return rows, report


def parse_nav_history_file(path: str | Path, **kwargs) -> tuple[list[AmfiScheme], ParseReport]:
    """Read and parse a downloaded NAV-history report from disk."""
    p = Path(path)
    return parse_nav_history_report(
        p.read_text(encoding="utf-8", errors="replace"), **kwargs)
