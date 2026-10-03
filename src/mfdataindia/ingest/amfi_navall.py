"""Parser for AMFI's ``spages/NAVAll.txt`` daily NAV report.

AMFI (amfiindia.com) is the canonical authority for Indian mutual fund metadata, but the site
is only reachable from an Indian IP. This module is deliberately **dependency-free** (stdlib
only) so it runs unchanged on an India server, and is validated against a real copy of the file
retrieved via the Wayback Machine — see ``docs/RESEARCH.md`` §3B.

File format (semicolons, CRLF, with hierarchical section headers)::

    Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

    Open Ended Schemes(Debt Scheme - Banking and PSU Fund)      <- schemeType(schemeCategory)

    Aditya Birla Sun Life Mutual Fund                           <- AMC / fund house

    119551;INF209KA12Z1;INF209KA13Z9;<name> - DIRECT - IDCW;102.3377;27-Dec-2024

Rules encoded here:

* a line is a **data row** iff it contains exactly 5 semicolons;
* section headers yield ``scheme_type`` *and* ``scheme_category`` together;
* any other non-empty line is the current **AMC** name;
* missing ISINs are the literal ``-``;
* column A is dual-purpose — Growth ISIN for growth options, Div-Payout ISIN for IDCW options;
* dates are ``DD-Mon-YYYY``, **not** mfapi.in's ``DD-MM-YYYY``.

Usage::

    python -m mfdataindia.ingest.amfi_navall NAVAll.txt --out navall.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

#: The three legal SEBI scheme types AMFI uses as section headers.
SCHEME_TYPES = ("Open Ended Schemes", "Close Ended Schemes", "Interval Fund Schemes")

_SECTION_RE = re.compile(
    r"^(?P<type>" + "|".join(re.escape(t) for t in SCHEME_TYPES) + r")\s*\((?P<cat>.+)\)\s*$"
)

#: AMFI writes missing ISINs as a bare hyphen, and unavailable NAVs as ``N.A.``.
_MISSING = {"", "-", "--", "na", "n.a.", "n/a", "null", "none"}

#: NAV cells AMFI uses when a scheme has no published NAV (typically long-dead schemes).
_NAV_NOT_PUBLISHED = {"n.a.", "na", "n/a", "-", "--", ""}

#: Plan labels, in precedence order. ``DIRECT`` must be tested first because some scheme names
#: contain both tokens, e.g. ``Axis Gilt Fund - Direct Plan - Regular IDCW Option``.
_PLAN_TOKENS = (
    ("DIRECT", "DIRECT"),
    ("REGULAR", "REGULAR"),
    ("RETAIL", "RETAIL"),
    ("INSTITUTIONAL", "INSTITUTIONAL"),
)

#: Dividend periodicity tokens, longest first so ``QUARTERLY`` beats ``DAILY``-style prefixes.
_PERIODICITY = (
    "FORTNIGHTLY", "QUARTERLY", "HALF YEARLY", "HALF-YEARLY", "MONTHLY", "WEEKLY",
    "ANNUAL", "DAILY", "PERIODIC",
)

#: SEBI renamed "dividend" to IDCW in 2021, but AMCs spell it inconsistently. Many schemes use
#: the full legal phrase instead of the acronym, e.g. Kotak / TrustMF rows read
#: ``... Monthly Payout of Income Distribution cum Capital Withdrawal Option`` with no "IDCW".
_IDCW_SPELLINGS = (
    "IDCW",
    "INCOME DISTRIBUTION",
    "CAPITAL WITHDRAWAL",
    "PAYOUT OF INCOME",
    "INCOME DIST",
)

#: The literal header line of NAVAll.txt — skipped, not quarantined.
_HEADER_PREFIX = "Scheme Code;ISIN Div Payout"


@dataclass(slots=True)
class AmfiScheme:
    """One parsed row of ``NAVAll.txt``."""

    amfi_scheme_code: str
    scheme_name: str
    nav: Optional[float]
    nav_date: Optional[date]
    scheme_type: Optional[str]
    scheme_category: Optional[str]
    amc: Optional[str]
    isin_div_payout_or_growth: Optional[str]
    isin_div_reinvestment: Optional[str]
    plan_type: str = "UNLABELLED"
    option: str = "UNKNOWN"
    periodicity: Optional[str] = None
    is_etf: bool = False
    is_defunct: bool = False
    nav_not_published: bool = False
    in_scope: bool = False
    line_no: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["nav_date"] = self.nav_date.isoformat() if self.nav_date else None
        return d


def _clean_isin(raw: str) -> Optional[str]:
    """Normalise an ISIN cell; AMFI uses ``-`` for 'none'."""
    v = (raw or "").strip()
    if v.lower() in _MISSING:
        return None
    return v.upper()


def classify_plan(name: str) -> str:
    """Return REGULAR / DIRECT / RETAIL / INSTITUTIONAL / UNLABELLED from an AMFI scheme name.

    ``DIRECT`` is tested first: names such as ``... - Direct Plan - Regular IDCW Option`` are
    direct plans whose *option* happens to be called "Regular IDCW".
    """
    up = (name or "").upper()
    for token, label in _PLAN_TOKENS:
        if token in up:
            return label
    return "UNLABELLED"


def classify_option(name: str) -> tuple[str, Optional[str]]:
    """Return ``(option, periodicity)``.

    ``option`` is one of GROWTH / IDCW / DIVIDEND / BONUS / UNKNOWN. AMFI renamed dividend
    options to IDCW in 2021, so both spellings are recognised and reported separately.
    """
    up = (name or "").upper()
    periodicity = next((p for p in _PERIODICITY if p in up), None)

    if any(sp in up for sp in _IDCW_SPELLINGS):
        return "IDCW", periodicity
    if "BONUS" in up:
        return "BONUS", periodicity
    if "DIVIDEND" in up or "DIV " in up:
        return "DIVIDEND", periodicity
    if "GROWTH" in up:
        return "GROWTH", periodicity
    return "UNKNOWN", periodicity


def _parse_date(raw: str) -> Optional[date]:
    """AMFI dates are ``27-Dec-2024``."""
    v = (raw or "").strip()
    if not v:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _parse_nav(raw: str) -> Optional[float]:
    v = (raw or "").strip().replace(",", "")
    if v.lower() in _MISSING:
        return None
    try:
        return float(v)
    except ValueError:
        return None


@dataclass(slots=True)
class ParseReport:
    """Diagnostics for one parse run — every rejected line is accounted for."""

    total_lines: int = 0
    header_rows: int = 0
    data_rows: int = 0
    section_headers: int = 0
    amc_headers: int = 0
    quarantined: int = 0
    quarantine_reasons: Counter = field(default_factory=Counter)
    plan_types: Counter = field(default_factory=Counter)
    options: Counter = field(default_factory=Counter)
    scheme_types: Counter = field(default_factory=Counter)
    distinct_amcs: int = 0
    distinct_categories: int = 0
    missing_isin_a: int = 0
    missing_isin_b: int = 0
    unparsed_nav: int = 0
    nav_not_published: int = 0
    defunct_schemes: int = 0
    unparsed_date: int = 0
    quarantined_samples: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"lines read         : {self.total_lines}",
            f"header rows        : {self.header_rows}",
            f"data rows parsed   : {self.data_rows}",
            f"section headers    : {self.section_headers}",
            f"AMC headers        : {self.amc_headers} (distinct {self.distinct_amcs})",
            f"distinct categories: {self.distinct_categories}",
            f"quarantined        : {self.quarantined}",
        ]
        if self.quarantine_reasons:
            lines.append("  reasons          : " + ", ".join(
                f"{k}={v}" for k, v in self.quarantine_reasons.most_common()))
        lines.append("plan types         : " + ", ".join(
            f"{k}={v}" for k, v in self.plan_types.most_common()))
        lines.append("options            : " + ", ".join(
            f"{k}={v}" for k, v in self.options.most_common()))
        lines.append("scheme types       : " + ", ".join(
            f"{k}={v}" for k, v in self.scheme_types.most_common()))
        lines.append(f"missing ISIN col-A : {self.missing_isin_a}")
        lines.append(f"missing ISIN col-B : {self.missing_isin_b}")
        lines.append(f"unparsed NAV       : {self.unparsed_nav}")
        lines.append(f"NAV = 'N.A.'       : {self.nav_not_published}")
        lines.append(f"defunct schemes    : {self.defunct_schemes}")
        lines.append(f"unparsed date      : {self.unparsed_date}")
        return "\n".join(lines)


def parse_navall(
    text: str,
    *,
    regular_only: bool = False,
    max_quarantine_samples: int = 25,
) -> tuple[list[AmfiScheme], ParseReport]:
    """Parse the full ``NAVAll.txt`` body.

    Returns ``(schemes, report)``. Malformed rows are **quarantined, never silently dropped** —
    they surface in ``report.quarantine_reasons`` together with samples.

    :param regular_only: if True, emit only rows whose ``in_scope`` is True (Regular Plan).
    """
    report = ParseReport()
    schemes: list[AmfiScheme] = []
    amcs: set[str] = set()
    categories: set[str] = set()

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

        # --- the literal column header: skip cleanly, never quarantine ---
        if s.startswith(_HEADER_PREFIX):
            report.header_rows += 1
            continue

        # --- data row: exactly 5 semicolons ---
        if s.count(";") == 5:
            code, isin_a, isin_b, name, nav_raw, date_raw = (p.strip() for p in s.split(";"))

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

            isin_a_c = _clean_isin(isin_a)
            isin_b_c = _clean_isin(isin_b)
            if isin_a_c is None:
                report.missing_isin_a += 1
            if isin_b_c is None:
                report.missing_isin_b += 1

            plan = classify_plan(name)
            option, periodicity = classify_option(name)
            up_name = name.upper()
            is_etf = "ETF" in up_name
            # Dead schemes: AMFI keeps them listed with NAV 0.0000 or N.A., and some AMCs also
            # mark them in the name ("... - Defunct - Growth option", "OLD-SBI Magnum ...").
            is_defunct = (
                "DEFUNCT" in up_name
                or up_name.startswith("OLD-")
                or nav_np
                or (nav is not None and nav <= 0)
            )

            warnings: list[str] = []
            if cur_type is None or cur_amc is None:
                warnings.append("no_section_or_amc_header")
            if nav is None:
                warnings.append("nav_not_published" if nav_np else "unparsed_nav")
            elif nav <= 0:
                warnings.append("non_positive_nav")
            if nav_date is None:
                warnings.append("unparsed_date")
            if isin_a_c is None:
                warnings.append("missing_isin")
            if option == "UNKNOWN":
                warnings.append("unrecognised_option")
            if is_defunct:
                warnings.append("defunct_scheme")

            rec = AmfiScheme(
                amfi_scheme_code=code,
                scheme_name=name,
                nav=nav,
                nav_date=nav_date,
                scheme_type=cur_type,
                scheme_category=cur_cat,
                amc=cur_amc,
                isin_div_payout_or_growth=isin_a_c,
                isin_div_reinvestment=isin_b_c,
                plan_type=plan,
                option=option,
                periodicity=periodicity,
                is_etf=is_etf,
                is_defunct=is_defunct,
                nav_not_published=nav_np,
                # In scope = Regular Plan, all options. Retail / Institutional are separate plan
                # labels: captured, but excluded from the default regular-only view.
                in_scope=(plan == "REGULAR") or (plan == "UNLABELLED" and not is_etf),
                line_no=line_no,
                warnings=warnings,
            )
            report.data_rows += 1
            if is_defunct:
                report.defunct_schemes += 1
            report.plan_types[plan] += 1
            report.options[option] += 1
            if cur_type:
                report.scheme_types[cur_type] += 1
            if cur_cat:
                categories.add(cur_cat)
            if not regular_only or rec.in_scope:
                schemes.append(rec)
            continue

        # --- section header: schemeType(schemeCategory) ---
        m = _SECTION_RE.match(s)
        if m:
            cur_type = m.group("type").strip()
            cur_cat = m.group("cat").strip()
            categories.add(cur_cat)
            report.section_headers += 1
            continue

        # --- anything else without semicolons is an AMC name ---
        if ";" not in s and not s.startswith("<"):
            cur_amc = s
            amcs.add(s)
            report.amc_headers += 1
            continue

        _quarantine("unclassified_line", line_no, s)

    report.distinct_amcs = len(amcs)
    report.distinct_categories = len(categories)
    return schemes, report


def parse_file(path: str | Path, **kwargs) -> tuple[list[AmfiScheme], ParseReport]:
    """Read and parse ``NAVAll.txt`` from disk."""
    p = Path(path)
    # AMFI serves latin-1-ish bytes; never fail the whole run over one bad character.
    return parse_navall(p.read_text(encoding="utf-8", errors="replace"), **kwargs)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Parse AMFI NAVAll.txt")
    ap.add_argument("path", help="path to NAVAll.txt")
    ap.add_argument("--out", help="write parsed records as JSONL")
    ap.add_argument("--regular-only", action="store_true",
                    help="emit only in-scope Regular Plan schemes")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    schemes, report = parse_file(args.path, regular_only=args.regular_only)

    if not args.quiet:
        print(report.summary(), file=sys.stderr)
        print(f"\nemitted records    : {len(schemes)}", file=sys.stderr)
        for q in report.quarantined_samples[:5]:
            print(f"  quarantined L{q['line_no']} [{q['reason']}]: {q['raw'][:110]}",
                  file=sys.stderr)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            for rec in schemes:
                fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        if not args.quiet:
            print(f"wrote {args.out}", file=sys.stderr)

    # Non-zero exit if the file did not look like a NAVAll.txt at all.
    return 0 if report.data_rows > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
