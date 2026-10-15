"""Offline, source-bound window diagnostics. Never accepts coverage or publishes analytics."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from importlib.metadata import version
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, model_validator

from mfdataindia.enrichment import idcw_catalog as catalog, idcw_discovery as decoder
from mfdataindia.enrichment.schemas import Model
from mfdataindia.ingest.amfi_nav_history import parse_nav_history_report
from mfdataindia.review.policy import fingerprint

HASH = r"^[0-9a-f]{64}$"
LISTING_URL = "https://www.hdfcfund.com/nav-and-idcw"
SID_URL = "https://files.hdfcfund.com/s3fs-public/SID/2025-11/SID%20-%20HDFC%20Flexi%20Cap%20Fund%20dated%20November%2021%2C%202025_0.pdf"
SAI_URL = "https://files.hdfcfund.com/s3fs-public/2025-09/SAI%20with%20updated%20Addendum%20dated%20August%2029%2C%202025.pdf"
REGULAR = "HDFC Flexi Cap Fund - Regular Plan - IDCW Option"
DIRECT = "HDFC Flexi Cap Fund - Direct Plan - IDCW Option"
HEADERS = [
    "Fund Category",
    "Fund Name",
    "Scheme Name",
    "Dividend Date",
    "Dividend Individual/ HUF (Rs) per Unit",
    "Dividend Others (Rs) per Unit",
]
ROLES = ("listing", "query", "observation", "nav", "sid", "sai")
LIMITS = dict(
    listing=65536,
    query=65536,
    observation=262144,
    nav=16 * 1024 * 1024,
    sid=decoder.MAX_BYTES,
    sai=decoder.MAX_BYTES,
)
_POLICY = {
    "id": "hdfc-idcw-window-readiness",
    "version": 1,
    "scope": "101763 Regular IDCW; FY2025-26 diagnostic only",
    "publication": "offline_diagnostic_not_coverage_certificate_or_total_returns",
    "listing": "exact historical query and all exported/displayed rows; Direct rows retained but not pilot observations",
    "coverage": "captured query is not proof of server pagination, truncation, revisions, no missing events or unit-event completeness",
    "nav": "exact dated reported NAV only; neither record-day nor next-day NAV is inferred to be a reinvestment price",
    "rules": "narrow dated issuer wording, not a verified amendment chain, event allotment price or quantified scheme levy",
    "stamp_duty": "reported percentage rule only; not applied, not a scheme levy, not personal tax or actual units received",
    "conflicts": "explicit discrepancies retain both values and block readiness; never latest-wins",
    "numeric": "finite positive decimal text; precision 50 for equality only; no return calculation or rounding",
    "preservation": "read-only original inputs, exclusive new report; no DB/network/API/eligibility writes",
    "acceptance": "always blocked for adjusted analytics; no override or caller-supplied proof flags",
    "bounds": {"sources": 6, "csv_rows": 40, "nav_days": 7, "pages_per_document": 2},
}


def policy():
    return deepcopy(_POLICY)


def code_hash():
    parts = [
        Path(__file__),
        Path(__file__).with_name("idcw_catalog.py"),
        Path(__file__).with_name("idcw_discovery.py"),
        Path(__file__).with_name("_idcw_discovery_worker.py"),
        Path(__file__).parents[1] / "ingest/amfi_nav_history.py",
        Path(__file__).parents[1] / "ingest/amfi_navall.py",
        Path(__file__).parents[1] / "review/policy.py",
    ]
    return fingerprint(
        {
            "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in parts},
            "catalog_dependencies": catalog.engine_hash(),
            "decoder_dependencies": decoder.code_hash(),
        }
    )


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("nonfinite JSON token")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


class Source(Model):
    file: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    sha256: str = Field(pattern=HASH)


class ReadinessRequest(Model):
    format: Literal["idcw-window-readiness-request-v1"]
    code: Literal[101763]
    start: Literal["2025-04-01"]
    end: Literal["2026-03-31"]
    expected_snapshot_id: str = Field(pattern=HASH)
    observed_at: datetime
    listing_url: Literal[LISTING_URL]
    sid_url: str = Field(max_length=2048)
    sai_url: Literal[SAI_URL]
    nav_start: date
    nav_end: date
    listing: Source
    query: Source
    observation: Source
    nav: Source
    sid: Source
    sai: Source

    @model_validator(mode="after")
    def bounded(self):
        parsed = urlsplit(self.sid_url)
        base = self.sid_url.split("?", 1)[0]
        query = parse_qs(parsed.query, keep_blank_values=True)
        if (
            base != SID_URL
            or parsed.fragment
            or parsed.query
            and (
                set(query) != {"VersionId"}
                or len(query["VersionId"]) != 1
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", query["VersionId"][0])
            )
        ):
            raise ValueError("exact official SID coordinates required")
        if (
            self.observed_at.tzinfo is None
            or self.observed_at.date() < date(2026, 4, 30)
            or not date(2025, 4, 1) <= self.nav_start <= self.nav_end <= date(2026, 4, 7)
            or (self.nav_end - self.nav_start).days > 6
            or len({getattr(self, role).file for role in ROLES}) != len(ROLES)
        ):
            raise ValueError("bounded distinct source files, dates and aware observation required")
        self.observed_at = self.observed_at.astimezone(timezone.utc)
        return self


class QueryCapture(Model):
    format: Literal["hdfc-idcw-query-context-v1"]
    url: Literal[LISTING_URL]
    selected_tab: Literal["IDCW"]
    category: Literal["Equity"]
    scheme: Literal["HDFC Flexi Cap Fund"]
    start: Literal["2025-04-01"]
    end: Literal["2026-03-31"]
    export_sha256: str = Field(pattern=HASH)
    displayed_rows: list[list[str]] = Field(max_length=40)
    pagination: Literal["no_pagination_controls_observed"]
    context_kind: Literal["operator_observed_browser_dom_not_server_response"]


def numeric(text):
    if not isinstance(text, str) or not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,8})?", text):
        raise ValueError("bounded positive decimal source token required")
    with localcontext() as ctx:
        ctx.prec = 50
        if not 0 < Decimal(text) < Decimal("1000000000"):
            raise ValueError("positive source amount required")
    return text


def csv_rows(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= LIMITS["listing"]:
        raise ValueError("bounded original CSV required")
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    if next(reader, None) != HEADERS:
        raise ValueError("unsupported issuer CSV header/order")
    result, seen = [], set()
    for row in reader:
        if (
            len(result) >= 40
            or len(row) != 6
            or row[:2] != ["Equity", "HDFC Flexi Cap Fund"]
            or row[2] not in (REGULAR, DIRECT)
        ):
            raise ValueError("unexpected issuer CSV row, scope or row bound")
        month_names = (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        )
        token = re.fullmatch(r"([0-9]{2}) ([A-Z][a-z]{2}), ([0-9]{4})", row[3])
        if not token or token[2] not in month_names:
            raise ValueError("unsupported issuer date")
        day = date(int(token[3]), month_names.index(token[2]) + 1, int(token[1]))
        if not date(2025, 4, 1) <= day <= date(2026, 3, 31):
            raise ValueError("issuer date outside requested window")
        key = (row[2], day)
        if key in seen:
            raise ValueError("duplicate or conflicting issuer CSV event")
        seen.add(key)
        result.append(
            {
                "scheme": row[2],
                "date": day.isoformat(),
                "individual_inr_per_unit": numeric(row[4]),
                "others_inr_per_unit": numeric(row[5]),
            }
        )
    return result


def observation(raw, request):
    value = strict_json(raw)
    snapshot = catalog.IdcwSnapshot.model_validate(value).model_dump(mode="json")
    if (
        value != snapshot
        or snapshot["snapshot_id"] != request.expected_snapshot_id
        or snapshot["snapshot_id"]
        != fingerprint({k: v for k, v in snapshot.items() if k != "snapshot_id"})
    ):
        raise ValueError("immutable observation seal/pin differs")
    if (
        snapshot["code"] != 101763
        or snapshot["identity"] != catalog.declared_identity(101763)
        or snapshot["identity_hash"] != fingerprint(snapshot["identity"])
        or snapshot["policy_hash"] != fingerprint(catalog.policy())
        or snapshot["engine_code_hash"] != catalog.engine_hash()
        or snapshot["request"]["identity"] != snapshot["identity"]
        or snapshot["window"] != {"start": request.start, "end": request.end}
    ):
        raise ValueError("observation identity/policy/code/window differs")
    for event in snapshot["events"]:
        for field in ("amount_inr_per_unit", "amount_others_inr_per_unit", "cum_idcw_nav_inr"):
            numeric(event[field])
    return snapshot


def nav_rows(raw, request):
    text = raw.decode("utf-8-sig")
    rows, checks = parse_nav_history_report(text)
    if (
        not checks.header_rows
        or checks.quarantined
        or checks.unparsed_nav
        or checks.unparsed_date
        or any(
            r.nav_date is None or not request.nav_start <= r.nav_date <= request.nav_end
            for r in rows
        )
    ):
        raise ValueError("invalid or out-of-window AMFI history")
    result, seen = [], set()
    for row in rows:
        if row.amfi_scheme_code != "101763":
            continue
        if (
            row.plan_type != "REGULAR"
            or row.option != "IDCW"
            or row.isin_div_payout_or_growth != "INF179K01582"
            or row.isin_div_reinvestment != "INF179K01590"
            or row.scheme_name != "HDFC Flexi Cap Fund - IDCW Plan"
            or row.amc != "HDFC Mutual Fund"
            or row.nav is None
            or row.nav_date in seen
            or row.warnings
        ):
            raise ValueError("AMFI pilot identity, NAV or uniqueness differs")
        seen.add(row.nav_date)
        result.append(
            {
                "date": row.nav_date.isoformat(),
                "nav_inr": numeric(str(row.nav)),
                "source_line": row.line_no,
                "meaning": "reported_nav_not_reinvestment_price",
            }
        )
    if not result:
        raise ValueError("pilot has no original AMFI NAV observations")
    return sorted(result, key=lambda r: r["date"])


def document_rules(sid_pages, sai_pages):
    def normalized(pages, expected):
        if [p["page"] for p in pages] != expected:
            raise ValueError("explicit issuer rule pages required")
        return [" ".join(p["text"].split()) for p in pages]

    sid = normalized(sid_pages, [2, 57])
    sai = normalized(sai_pages, [1, 167])
    if (
        "HDFC Flexi Cap Fund" not in sid[0]
        or "November 21, 2025" not in sid[0]
        or "HDFC Flexi Cap Fund-SID" not in sid[1]
        or "IDCW Distribution Procedure" not in sid[1]
        or not re.search(
            r"NAV will be adjusted .*?close of business hours on record date\.", sid[1]
        )
        or "HDFC Mutual Fund" not in sai[0]
        or "June 30, 2025" not in sai[0]
        or "June 30, 2025" not in sai[1]
        or "STAMP DUTY ON ALLOTMENT/TRANSFER OF UNITS" not in sai[1]
        or not re.search(r"reinvestment under IDCW Option\).*?0\.005%", sai[1], re.I)
    ):
        raise ValueError("unsupported issuer rule document anchors")
    return {
        "nav_adjustment_timing_wording": "record_date_close_of_business",
        "sid_pages": [2, 57],
        "sai_pages": [1, 167],
        "reinvestment_stamp_duty_rate_percent_as_reported": "0.005",
        "rule_application": "not_applied_to_investor_or_scheme_returns",
        "effective_amendment_history_verified": False,
        "event_reinvestment_date_verified": False,
        "event_reinvestment_nav_verified": False,
        "scheme_levy_quantified": False,
    }


def assess(request, sources):
    request = ReadinessRequest.model_validate(request.model_dump(mode="json"))
    if set(sources) != set(ROLES):
        raise ValueError("exact six source roles required")
    for role in ROLES:
        raw = sources[role]
        if (
            not isinstance(raw, bytes)
            or not 0 < len(raw) <= LIMITS[role]
            or hashlib.sha256(raw).hexdigest() != getattr(request, role).sha256
        ):
            raise ValueError("original source size/hash differs")
    query = QueryCapture.model_validate(strict_json(sources["query"]))
    rows = csv_rows(sources["listing"])
    expected_display = [
        [r["scheme"], r["date"], r["individual_inr_per_unit"], r["others_inr_per_unit"]]
        for r in rows
    ]
    if query.export_sha256 != request.listing.sha256 or query.displayed_rows != expected_display:
        raise ValueError("query capture/export rows or hash differ")
    snapshot = observation(sources["observation"], request)
    navs = nav_rows(sources["nav"], request)
    sid = decoder.decode(sources["sid"], [2, 57])
    sai = decoder.decode(sources["sai"], [1, 167])
    rules = document_rules(sid, sai)
    regular = [r for r in rows if r["scheme"] == REGULAR]
    actual = {r["date"]: r for r in regular}
    known = {r["record_date"]: r for r in snapshot["events"]}
    if len(known) != len(snapshot["events"]):
        raise ValueError("duplicate source snapshot event")
    differences = []
    for day in sorted(actual.keys() | known.keys()):
        a, b = actual.get(day), known.get(day)
        same = bool(
            a
            and b
            and Decimal(a["individual_inr_per_unit"]) == Decimal(b["amount_inr_per_unit"])
            and Decimal(a["others_inr_per_unit"]) == Decimal(b["amount_others_inr_per_unit"])
        )
        if not same:
            differences.append({"date": day, "issuer_listing": a, "retained_observation": b})
    matching_cum = []
    for event in snapshot["events"]:
        for nav in navs:
            if nav["date"] == event["record_date"] and Decimal(nav["nav_inr"]) == Decimal(
                event["cum_idcw_nav_inr"]
            ):
                matching_cum.append(event["record_date"])
    gaps = [
        "provider_listing_completeness_and_revision_history_unverified",
        "applicable_reinvestment_date_and_price_unverified",
        "scheme_levy_basis_unverified",
        "unit_event_window_unverified",
        "issuer_rule_amendment_chain_unverified",
        "current_live_identity_not_checked",
        "total_return_engine_and_private_publication_not_implemented",
    ]
    if differences:
        gaps.insert(0, "issuer_listing_and_retained_observation_disagree")
    if not regular:
        gaps.insert(0, "empty_listing_does_not_prove_no_distributions")
    if matching_cum:
        gaps.insert(0, "record_day_reported_nav_matches_cum_nav_not_verified_reinvestment_price")
    report = {
        "format": "idcw-window-readiness-report-v1",
        "state": "blocked",
        "scope": {
            "code": 101763,
            "plan": "REGULAR",
            "option": "IDCW",
            "start": request.start,
            "end": request.end,
        },
        "request": request.model_dump(mode="json"),
        "policy": policy(),
        "policy_hash": fingerprint(policy()),
        "engine_code_hash": code_hash(),
        "extractor_version": version("pypdf"),
        "snapshot_id": snapshot["snapshot_id"],
        "query_state": "captured_not_completeness_proof",
        "nav_source_url": "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt="
        + str(request.nav_start.day).zfill(2)
        + "-"
        + ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[
            request.nav_start.month - 1
        ]
        + "-"
        + str(request.nav_start.year)
        + "&todt="
        + str(request.nav_end.day).zfill(2)
        + "-"
        + ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[
            request.nav_end.month - 1
        ]
        + "-"
        + str(request.nav_end.year)
        + "&tp=1&mc=0",
        "listing_rows": rows,
        "listing_row_count": len(rows),
        "pilot_listing_event_count": len(regular),
        "listing_reconciliation": "conflicting" if differences else "corroborated_not_complete",
        "differences": differences,
        "reported_nav_observations": navs,
        "record_day_cum_nav_matches": matching_cum,
        "issuer_rule_observations": rules,
        "pdf_text_hashes": {
            role: [
                {"page": p["page"], "sha256": hashlib.sha256(p["text"].encode()).hexdigest()}
                for p in pages
            ]
            for role, pages in (("sid", sid), ("sai", sai))
        },
        "remaining_gaps": gaps,
        "coverage_state": "unknown",
        "complete_event_window_proven": False,
        "analytics_eligible": False,
        "readiness_certified": False,
        "source_rows_changed": 0,
        "database_imported": False,
        "derived_returns_generated": False,
    }
    return {**report, "report_hash": fingerprint(report)}
