"""Official ABSL monthly reported facts; never a substitute for calculated analytics."""

import hashlib
import re
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

from mfdataindia.enrichment import facts, monthly_pdf
from mfdataindia.review.policy import fingerprint

COHORT = {103174, 103173}
NAME = "Aditya Birla Sun Life Large Cap Fund"
MONTHS = {
    name: i
    for i, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}
RISK = r"(?:Low to Moderate|Moderately High|Very High|Moderate|High|Low)"
_POLICY = {
    "id": "official-absl-monthly-facts",
    "version": 1,
    "cohort": sorted(COHORT),
    "fund": NAME,
    "identity": "exact current AMC/name/Regular/Growth-or-IDCW/ISIN plus retained issuer-summary code and payout ISIN; no name-only or Direct-plan borrowing",
    "dates": "explicit NAV as-on date and matching fund/change-page calendar month; English month mapping, not locale; document-context date unless a separate field date is printed",
    "risk": "explicit Scheme Riskometer row, Previous Month then Current Month, on same fund's changes page; never gauge legend, pointer or benchmark risk",
    "expense": "Regular Plan BER only; change-table current value must corroborate detail; not TER; preserve exact decimal token",
    "money": "decimal strings; INR crore only when Cr explicitly printed; unlabelled AUM retained as unit-unknown reported evidence, never normalized to crore",
    "inception": "fund-level reported date only; never overwrite plan inception or drive canonical return calculations",
    "performance": "issuer-reported Regular/Direct/benchmark table retained separately; option basis unresolved, never advertised as IDCW total returns or substituted for canonical analytics",
    "holdings": "reported top ten only, no inferred ISIN/sector/exposure completeness or economic overlap",
    "unknown": "NA means not applicable when the document defines it; absent/ambiguous anchors remain explicitly unavailable; no zero/default inference",
    "review": "publish source-validated reported facts with needs_verification, not human-verified",
    "source_writes": "none; immutable originals and snapshots only",
    "pdf_policy_hash": fingerprint(monthly_pdf.policy()),
    "identity_adapter_policy_hash": fingerprint(facts.policy()),
}


def policy():
    return deepcopy(_POLICY)


def english_date(token):
    match = re.fullmatch(r"([A-Za-z]+) ([0-9]{1,2}),\s*([0-9]{4})", token)
    if not match or match[1] not in MONTHS:
        raise ValueError("explicit English as-on date required")
    return date(int(match[3]), MONTHS[match[1]], int(match[2])).isoformat()


def decimal(token, *, low=Decimal("0"), high=Decimal("1000000000000")):
    if not re.fullmatch(r"-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,8})?", token):
        raise ValueError("finite source decimal token required")
    value = Decimal(token.replace(",", ""))
    if not low <= value <= high:
        raise ValueError("source decimal outside policy bounds")
    return format(value, "f")


def parse(
    identity, detail, changes, registry, *, as_of, detail_lineage, changes_lineage, identity_lineage
):
    if (
        identity["code"] not in COHORT
        or facts.normalized(identity["scheme_name"]) != facts.normalized(NAME)
        or not any(
            facts.normalized(line) == facts.normalized(NAME) for line in changes.splitlines()
        )
    ):
        raise ValueError("bounded monthly exact identity required")
    token = facts.one(r"(?m)^NAV as on ([A-Za-z]+ [0-9]{1,2},\s*[0-9]{4})\s*$", detail)
    if english_date(token) != as_of:
        raise ValueError("monthly disclosure date differs")
    period = token.split()[0] + " " + str(date.fromisoformat(as_of).year)
    if period not in {line.strip() for line in changes.splitlines()}:
        raise ValueError("change-page month differs")
    # Reuse the frozen exact issuer code/ISIN bridge; its v1 lineage is recorded
    # separately, and its five-field contract is not widened or reinterpreted.
    prior = facts.parse(
        identity,
        detail,
        registry,
        as_of=as_of,
        detail_lineage=detail_lineage,
        identity_lineage=identity_lineage,
    )
    fields = {
        key: deepcopy(prior["fields"][key])
        for key in ("benchmark_name", "fund_managers", "base_expense_ratio")
    }

    def available(value, unit, scope, raw, anchor, *, field_date=None):
        return {
            "status": "accepted",
            "value": value,
            "unit": unit,
            "scope": scope,
            "disclosure_as_of": as_of,
            "date_basis": "explicit_field_date"
            if field_date
            else "document_context_not_field_effective_date",
            "field_effective_date": field_date,
            "raw_value": raw,
            "source_anchor": anchor,
            "reason": None,
        }

    def missing(reason, *, raw=None, anchor=None):
        return {
            "status": "unavailable",
            "value": None,
            "unit": None,
            "scope": None,
            "disclosure_as_of": as_of,
            "date_basis": "document_context_not_field_effective_date",
            "field_effective_date": None,
            "raw_value": raw,
            "source_anchor": anchor,
            "reason": reason,
        }

    def optional(key, pattern, build, *, text=detail):
        values = re.findall(pattern, text, re.MULTILINE)
        if len(values) != 1:
            fields[key] = missing("anchor_missing" if not values else "anchor_ambiguous")
        else:
            fields[key] = build(values[0])

    for item in fields.values():
        item["field_effective_date"] = None
    optional(
        "risk_level",
        r"^Scheme Riskometer (" + RISK + r") (" + RISK + r")\s*$",
        lambda value: available(
            value[1],
            "risk_category",
            "fund",
            " ".join(value),
            "changes_page / Changes in Risk-o-Meter of scheme / Scheme Riskometer / Current Month",
        ),
        text=changes,
    )
    if "Changes in Risk-o-Meter of scheme" not in changes:
        fields["risk_level"] = missing("scheme_risk_change_table_missing")
    optional(
        "ber_monthly_change",
        r"^Regular ([0-9]{1,2}\.[0-9]{1,4})% ([0-9]{1,2}\.[0-9]{1,4})%\s*$",
        lambda value: available(
            {"previous": value[0], "current": value[1]},
            "percent",
            "regular_plan",
            " ".join(value),
            "changes_page / Changes in Expense Ratio / Regular",
        ),
        text=changes,
    )
    change = fields["ber_monthly_change"]["value"]
    if change and Decimal(change["current"]) != Decimal(fields["base_expense_ratio"]["value"]):
        fields["base_expense_ratio"] = missing(
            "detail_and_current_month_BER_disagree",
            raw=fields["base_expense_ratio"]["raw_value"],
            anchor="Expense Ratio / Regular Plan: BER",
        )
    for key, label in (("aum", "AUM"), ("aaum", "AAUM")):
        optional(
            key,
            "^" + label + r" as on ([A-Za-z]+ [0-9]{1,2},\s*[0-9]{4}) ([0-9,]+\.[0-9]+)( Cr)?\s*$",
            lambda value, label=label: (
                available(
                    decimal(value[1]),
                    "INR_crore",
                    "fund",
                    value[1] + value[2],
                    "Fund Details / " + label,
                    field_date=english_date(value[0]),
                )
                if value[2] and english_date(value[0]) == as_of
                else missing(
                    "unit_not_explicit" if not value[2] else "field_date_differs",
                    raw=value[1] + value[2],
                    anchor="Fund Details / " + label,
                )
            ),
        )
    optional(
        "reported_fund_inception",
        r"^Date of Inception / Allotment (.+?)\s*$",
        lambda value: available(
            value, "date_text", "fund", value, "Fund Details / Date of Inception / Allotment"
        ),
    )
    optional(
        "manager_since",
        r"^Managing since (.+?)\s*$",
        lambda value: available(
            english_date(value), "date", "fund", value, "Fund Manager Details / Managing since"
        ),
    )
    optional(
        "minimum_additional_lumpsum",
        r"^Lumpsum ₹ ([0-9,]+) \(plus in multiplies of ([0-9,]+)\)\s*$",
        lambda value: available(
            {"amount": decimal(value[0]), "multiple": decimal(value[1])},
            "INR",
            "fund",
            " ".join(value),
            "Minimum Additional Investment / Lumpsum",
        ),
    )
    optional(
        "sip_additional_terms",
        r"^SIP ₹ (Daily/Weekly/Monthly: Minimum\s*\n[0-9]+/-and in multiples of [0-9]+/- thereafter)\s*$",
        lambda value: available(
            " ".join(value.split()), "terms", "fund", value, "Minimum Additional Investment / SIP"
        ),
    )
    optional(
        "exit_load_terms",
        r"^Exit Load: ([\s\S]{1,1500}?)\nExpense Ratio\s*$",
        lambda value: available(
            " ".join(value.split()), "terms", "fund", value, "Load Structure / Exit Load"
        ),
    )
    optional(
        "scheme_type",
        r"^Type of Scheme: (.+?)\s*$",
        lambda value: available(value, "description", "fund", value, "Type of Scheme"),
    )
    optional(
        "product_labelling",
        r"^Product labelling ([\s\S]{1,1500}?)\n\*Investors should consult",
        lambda value: available(
            " ".join(value.split()), "description", "fund", value, "Product labelling"
        ),
    )
    label = "Growth" if identity["option"] == "GROWTH" else "Annual IDCW"
    optional(
        "reported_month_end_nav",
        r"^Regular Plan - " + label + r" Option ₹ ([0-9,]+\.[0-9]+)\s*$",
        lambda value: available(
            decimal(value),
            "INR_per_unit",
            "exact_variant",
            value,
            "Fund Details / Regular Plan - " + label + " Option",
            field_date=as_of,
        ),
    )
    for key, pattern, unit in (
        (
            "reported_standard_deviation",
            r"^1 Standard Deviation \(%\) (-?[0-9.]+|NA)\s*$",
            "percent",
        ),
        ("reported_beta", r"^2 Beta (-?[0-9.]+|NA)\s*$", "ratio"),
        (
            "reported_sharpe",
            r"^4 Sharpe Ratio \(%\) (-?[0-9.]+|NA)\s*$",
            "publisher_labels_percent_basis_unverified",
        ),
        ("reported_information_ratio", r"^6 Information Ratio (-?[0-9.]+|NA)\s*$", "ratio"),
        ("reported_equity_turnover", r"^a\) Equity ([0-9.]+|NA)\s*$", "ratio"),
        ("reported_gross_turnover", r"^b\) Gross Exposure Turnover ([0-9.]+|NA)\s*$", "ratio"),
    ):
        optional(
            key,
            pattern,
            lambda value, key=key, unit=unit: (
                missing("publisher_not_applicable", raw="NA", anchor=key)
                if value == "NA"
                else available(
                    decimal(value, low=Decimal("-1000")),
                    unit,
                    "publisher_reported_fund_statistic",
                    value,
                    key,
                )
            ),
        )
    holding_rows = re.findall(r"(?m)^([1-9]|10) (.+?) ([0-9]{1,2}\.[0-9]{1,4})%\s*$", detail)
    if len(holding_rows) == 10 and sorted(int(v[0]) for v in holding_rows) == list(range(1, 11)):
        fields["reported_top_ten"] = available(
            [
                {
                    "rank": int(rank),
                    "name": name,
                    "weight_pct": decimal(weight, high=Decimal("100")),
                }
                for rank, name, weight in holding_rows
            ],
            "percent_of_net_assets",
            "fund_top_ten_only",
            "\n".join(" ".join(row) for row in holding_rows),
            "Portfolio Data / Top 10 Holdings",
        )
    else:
        fields["reported_top_ten"] = missing("top_ten_rows_missing_or_ambiguous")
    # Do not invent the missing interpretation or silently alias these to core
    # calculated returns, full holdings, expense ratio, or plan inception.
    fields["total_expense_ratio"] = missing("BER_is_not_TER")
    # Retain what the issuer actually prints without promoting it to this
    # variant's returns. In particular, IDCW readers must not mistake this
    # shared fund-page table for distribution-adjusted IDCW performance.
    number = r"(?:-?[0-9]{1,3}\.[0-9]{1,4}|NA)"
    rows = re.findall(
        r"Returns for last ([135])\s+years? ("
        + number
        + r") ("
        + number
        + r") ("
        + number
        + r")\s*\n",
        detail,
    )
    inception = re.findall(
        r"Returns since\s+inception (" + number + r") (" + number + r") (" + number + r")\s*\n",
        detail,
    )
    if len(rows) == 3 and {r[0] for r in rows} == {"1", "3", "5"} and len(inception) == 1:
        values = [
            {
                "period": horizon + "_years",
                "regular_pct": None if regular == "NA" else decimal(regular, low=Decimal("-1000")),
                "direct_pct": None if direct == "NA" else decimal(direct, low=Decimal("-1000")),
                "benchmark_pct": None
                if benchmark == "NA"
                else decimal(benchmark, low=Decimal("-1000")),
            }
            for horizon, regular, direct, benchmark in [*rows, ("since_inception", *inception[0])]
        ]
        fields["reported_performance_table"] = available(
            {
                "rows": values,
                "option_basis": "unresolved",
                "not_exact_variant_returns": True,
                "not_idcw_total_returns": True,
                "publisher_na_means": "not_applicable",
            },
            "percent_as_reported",
            "publisher_regular_direct_benchmark_table_not_variant_returns",
            "\n".join(" ".join(row) for row in [*rows, inception[0]]),
            "Fund Performance / Compounded Annualised Returns",
        )
    else:
        fields["reported_performance_table"] = missing("reported_table_missing_or_ambiguous")
    fields["full_portfolio"] = missing(
        "factsheet_top_ten_not_full_portfolio_use_separate_monthly_disclosure"
    )
    fields["riskometer_graphic"] = missing("graphic_not_decoded_explicit_scheme_table_used_instead")
    for key, anchor in (
        ("financial_year_return_chart", "Absolute Returns / financial-year bar chart"),
        ("major_risk_factors", "Major Risk Factors"),
        ("monthly_holding_changes", "changes_page / Top 10 changes in Holding"),
        ("credit_quality_changes", "changes_page / Changes in Credit Quality"),
        (
            "detailed_allocation_links",
            "Additional Information pertaining to Portfolio of the scheme",
        ),
        ("investor_document_links", "changes_page / Important Weblinks"),
    ):
        fields[key] = missing("retained_original_section_adapter_not_yet_certified", anchor=anchor)
    return {
        "fields": fields,
        "identity_evidence": prior["identity_evidence"],
        "detail_extraction": detail_lineage,
        "changes_extraction": changes_lineage,
        "identity_extraction": identity_lineage,
        "verification_status": "needs_verification",
        "canonical_analytics_replaced": False,
        "policy_hash": fingerprint(policy()),
        "engine_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
