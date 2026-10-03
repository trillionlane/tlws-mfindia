"""Parser for AMFI's ``spages/NAVAll.txt`` daily NAV report.

AMFI is the canonical authority for Indian mutual fund NAV and metadata.
**No India server is required**: ``www.amfiindia.com`` is geo-blocked from some
networks, but ``portal.amfiindia.com`` serves the same paths and is reachable
globally. This module is deliberately **dependency-free** (stdlib only) so it can
run anywhere, and is validated against both a real archived copy of the file and
the live portal feed — see ``docs/RESEARCH.md`` §3B.

Two file layouts exist and both are supported, selected by column count:

**Current (8 columns, 7 semicolons)** — AMFI added explicit ``Plan`` and
``Option`` columns, so plan/option classification no longer needs name parsing::

    Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date

    135762;INF846K01WO1;-;Axis Children's Fund;Direct Plan;Growth Option;29.0001;01-Oct-2026

**Legacy (6 columns, 5 semicolons)** — plan/option must be inferred from the
scheme name, which is what the archived 2024 fixture uses::

    Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date

    119551;INF209KA12Z1;INF209KA13Z9;<name> - DIRECT - IDCW;102.3377;27-Dec-2024

Shared structure — hierarchical section headers carrying type *and* category::

    Open Ended Schemes(Debt Scheme - Banking and PSU Fund)   <- schemeType(schemeCategory)

    Aditya Birla Sun Life Mutual Fund                        <- AMC / fund house

Rules encoded here:

* a line is a **data row** iff it contains exactly 5 or 7 semicolons;
* section headers yield ``scheme_type`` *and* ``scheme_category`` together;
* any other non-empty line is the current **AMC** name;
* missing ISINs are the literal ``-``;
* column A is dual-purpose — Growth ISIN for growth options, Div-Payout ISIN for IDCW options;
* dates are ``DD-Mon-YYYY``;
* the explicit ``Plan``/``Option`` columns win when recognisable, and the scheme
  name is the fallback — so a blank or novel cell never loses the classification.

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

#: The literal header line of the bulk NAV-history report (different column order).
_HEADER_PREFIX_HISTORY = "Scheme Code;NAV Name"


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
    #: How plan_type was determined: COLUMN / COLUMN_BLANK / COLUMN_UNRECOGNISED / NAME.
    #: Scope depends on this — see resolve_plan().
    plan_source: str = "NAME"
    #: How option was determined: COLUMN or NAME.
    option_source: str = "NAME"
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


#: Values seen in the explicit ``Plan`` column of the current 8-column feed.
#: ``Regular Plan`` / ``Direct Plan`` are the current spellings; the bare forms are
#: tolerated because AMFI has varied them. This is an exact-match map rather than a
#: substring test, so a plan cell can never be misread as an option word.
_EXPLICIT_PLAN = {
    "REGULAR PLAN": "REGULAR",
    "REGULAR": "REGULAR",
    "DIRECT PLAN": "DIRECT",
    "DIRECT": "DIRECT",
    "RETAIL PLAN": "RETAIL",
    "RETAIL": "RETAIL",
    "INSTITUTIONAL PLAN": "INSTITUTIONAL",
    "INSTITUTIONAL": "INSTITUTIONAL",
}


def _cell_key(raw: Optional[str]) -> str:
    """Fold a CSV cell for exact-map lookup: uppercase, single-spaced, stripped."""
    if not raw:
        return ""
    return " ".join(str(raw).upper().split())


def classify_plan_explicit(raw: Optional[str]) -> Optional[str]:
    """Map the ``Plan`` column to a plan label, or None if unrecognised.

    Returning None (rather than UNLABELLED) lets the caller fall back to name
    inference, so a blank or novel cell degrades gracefully instead of silently
    dropping a scheme out of scope.
    """
    key = _cell_key(raw)
    if not key:
        return None
    return _EXPLICIT_PLAN.get(key)


def resolve_plan(name: str, plan_cell: Optional[str]) -> tuple[str, str]:
    """Return ``(plan_type, source)`` preferring the explicit column over the name.

    ``source`` distinguishes three genuinely different situations, because scope
    depends on it:

    ``COLUMN``
        The feed supplied a recognisable plan. Authoritative.
    ``COLUMN_BLANK``
        The feed *has* a plan column but left it empty. No plan information was
        supplied, so an unlabelled result here is a data gap — not a Regular Plan.
    ``NAME``
        No plan column exists at all (legacy 6-column feed), so the name is the
        only signal. Here ``UNLABELLED`` means "a plan written without the word
        Regular", which is treated as in-scope.
    """
    if plan_cell is None:
        return classify_plan(name), "NAME"
    if _cell_key(plan_cell):
        explicit = classify_plan_explicit(plan_cell)
        if explicit:
            return explicit, "COLUMN"
        return classify_plan(name), "COLUMN_UNRECOGNISED"
    return classify_plan(name), "COLUMN_BLANK"


def resolve_option(name: str, option_cell: Optional[str]) -> tuple[str, Optional[str], str]:
    """Return ``(option, periodicity, source)`` preferring the explicit column.

    The ``Option`` cell carries both the option and its periodicity (``Monthly
    IDCW``, ``Quarterly IDCW Option``, ``Growth Option``), and :func:`classify_option`
    already understands that vocabulary, so it is reused verbatim here.
    """
    if _cell_key(option_cell):
        option, periodicity = classify_option(option_cell)
        if option != "UNKNOWN":
            return option, periodicity, "COLUMN"
    option, periodicity = classify_option(name)
    return option, periodicity, "NAME"


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
    #: Which NAVAll layout was parsed: "NAVALL_8COL" (current) or "NAVALL_6COL" (legacy),
    #: or "UNKNOWN" if no data rows were seen.
    format: str = "UNKNOWN"
    #: How plan/option were determined, per row: COLUMN (explicit feed column) or NAME.
    plan_class_source: Counter = field(default_factory=Counter)
    option_class_source: Counter = field(default_factory=Counter)
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
            f"file format        : {self.format}",
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
        lines.append("plan from          : " + ", ".join(
            f"{k}={v}" for k, v in self.plan_class_source.most_common()))
        lines.append("option from        : " + ", ".join(
            f"{k}={v}" for k, v in self.option_class_source.most_common()))
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

        # --- data row: 5 semicolons (legacy 6-col) or 7 (current 8-col) ---
        n_semis = s.count(";")
        if n_semis in (5, 7):
            parts = [p.strip() for p in s.split(";")]
            if n_semis == 7:
                # Current AMFI feed: explicit Plan and Option columns.
                (code, isin_a, isin_b, name,
                 plan_cell, option_cell, nav_raw, date_raw) = parts
                report.format = "NAVALL_8COL"
            else:
                (code, isin_a, isin_b, name, nav_raw, date_raw) = parts
                plan_cell = option_cell = None
                report.format = "NAVALL_6COL"

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

            plan, plan_src = resolve_plan(name, plan_cell)
            option, periodicity, option_src = resolve_option(name, option_cell)
            report.plan_class_source[plan_src] += 1
            report.option_class_source[option_src] += 1
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
            if plan_src == "COLUMN_BLANK":
                warnings.append("plan_column_blank")
            if plan_src == "COLUMN_UNRECOGNISED":
                warnings.append("plan_column_unrecognised")
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
                #
                # An unlabelled row counts as in-scope ONLY when the plan was inferred
                # from the name because no plan column existed (legacy feed). When the
                # current feed HAS a plan column and left it blank, "unlabelled" means
                # the plan is unknown — stored and flagged, never assumed to be Regular.
                in_scope=(plan == "REGULAR")
                or (plan == "UNLABELLED" and not is_etf and plan_src == "NAME"),
                plan_source=plan_src,
                option_source=option_src,
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
