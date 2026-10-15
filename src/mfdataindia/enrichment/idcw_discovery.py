"""MF-E09 source gate. Discovery is not accepted events, coverage or analytics.

No DB, HTTP, API activation or synthetic fallback. Original PDFs are decoded in
a bounded worker; every extracted assertion retains its source and page lineage.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from importlib.metadata import version
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import Field, StrictInt, model_validator

from mfdataindia.enrichment.evidence import normalized, valid_isin
from mfdataindia.enrichment.schemas import Identity, Model
from mfdataindia.review.policy import fingerprint

MAX_BYTES = 16 * 1024 * 1024
NAME = "HDFC Flexi Cap Fund"
_POLICY = {
    "id": "hdfc-idcw-source-discovery", "version": 1,
    "scope": "AMFI 101763, Regular IDCW; explicit original PDF pages only",
    "supported_notice": "March 1 2026 announcement for conditional March 5 2026 record date only; other notice layouts/dates fail closed",
    "publication": "discovery_only_not_accepted_events",
    "identity": "issuer fund, exact regular AMFI label and checksum-valid payout/reinvestment ISINs; supplied DB identity is compared but live identity is not certified",
    "amount": "INR per unit, original decimal tokens; history separate from conditional announcement; no percentage conversion",
    "dates": "record date only when reported; never infer ex, payment, reinvestment or applicable valuation date",
    "prices": "cum-IDCW NAV and pre-notice NAV are not reinvestment prices",
    "coverage": "past-three-years summary does not establish complete calendar coverage; absence is unknown, never no payout",
    "levies": "generic if-any levy, TDS and stamp-duty wording is not a quantified zero or investor tax policy",
    "conflicts": "retain all source assertions; amount conflict blocks reconciliation, no latest-source-wins",
    "rounding": "none; finite source decimal text; precision 50 for comparisons",
    "maximum_source_bytes": MAX_BYTES, "maximum_pages": 400,
    "maximum_selected_pages": 2, "maximum_page_characters": 200000,
    "maximum_page_stream_bytes": 10000000, "worker_wall_seconds": 30,
    "worker_cpu_seconds": 20, "linux_worker_address_space_bytes": 512 * 1024 * 1024,
    "source_writes": "none; source and old policy/results remain unchanged",
    "acceptance": "source discovery cannot produce a certificate, unlock analytics or complete MF-E09",
}


def policy():
    return deepcopy(_POLICY)


def code_hash():
    return hashlib.sha256(
        Path(__file__).read_bytes()
        + Path(__file__).with_name("_idcw_discovery_worker.py").read_bytes()
        + Path(__file__).with_name("evidence.py").read_bytes()
        + Path(__file__).parents[1].joinpath("review/policy.py").read_bytes()
    ).hexdigest()


class IdcwDiscoverySource(Model):
    file: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.pdf$")
    source_url: str = Field(max_length=2048)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    pages: list[StrictInt] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def bounded(self):
        parts = urlsplit(self.source_url)
        # Offline provenance metadata, NEVER an environment-variable argument or
        # an arbitrary URL fetch. HDFC's actual notice filenames contain commas.
        path = unquote(parts.path)
        if (
            parts.scheme != "https" or parts.netloc != "files.hdfcfund.com"
            or parts.query or parts.fragment or not path.startswith("/s3fs-public/")
            or not re.fullmatch(r"[A-Za-z0-9 /_.,()-]+\.pdf", path)
            or any(p in (".", "..") for p in path.split("/"))
            or any(ord(c) < 33 or ord(c) > 126 for c in self.source_url)
            or self.observed_at.tzinfo is None
            or not all(1 <= p <= 400 for p in self.pages)
            or self.pages != sorted(set(self.pages))
        ):
            raise ValueError("bounded official source/page coordinates required")
        self.observed_at = self.observed_at.astimezone(timezone.utc)
        return self


class IdcwDiscoveryRequest(Model):
    format: str = Field(pattern=r"^idcw-source-discovery-request-v1$")
    identity: Identity
    proposed_start: date
    proposed_end: date
    history_period: str = Field(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    history: IdcwDiscoverySource
    notice: IdcwDiscoverySource
    registry: IdcwDiscoverySource
    use_basis: str = Field(pattern=r"^operator_reported_authorization$")
    authority_reference: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def bounded(self):
        if (
            self.identity.code != 101763 or self.identity.plan != "REGULAR"
            or self.identity.option != "IDCW"
            or normalized(self.identity.amc) != normalized("HDFC Mutual Fund")
            or normalized(self.identity.scheme_name) != normalized(NAME)
            or not date(2010, 1, 1) <= self.proposed_start <= self.proposed_end
            or self.proposed_end.isoformat()[:7] > self.history_period
            or len(self.history.pages) != 2
            or self.history.pages[0] != 1
            or self.notice.pages != [1] or self.registry.pages != [1]
            or len({x.file for x in (self.history, self.notice, self.registry)}) != 3
            or len({x.content_hash for x in (self.history, self.notice, self.registry)}) != 3
            or any(ord(c) < 32 for c in self.authority_reference)
            or any(x.observed_at.date() < self.proposed_end for x in (self.history, self.notice, self.registry))
            or self.history.observed_at.strftime("%Y-%m") < self.history_period
        ):
            raise ValueError("explicit bounded regular IDCW pilot and distinct source roles required")
        return self


def _one(pattern, text, flags=0):
    values = re.findall(pattern, text, flags)
    if len(values) != 1:
        raise ValueError("missing or ambiguous IDCW source anchor")
    return values[0]


def _decimal(value):
    if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,8})?", value):
        raise ValueError("unsupported distribution numeric token")
    with localcontext() as ctx:
        ctx.prec = 50
        if not 0 < Decimal(value) < Decimal("1000000000"):
            raise ValueError("positive finite source amount/NAV required")
    return value


def _date(value):
    months = {name: n for n, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )}
    match = re.fullmatch(r"([A-Z][a-z]{2}) ([0-9]{2}), ([0-9]{2})", value)
    if not match or match[1] not in months:
        raise ValueError("unsupported source record date")
    return date(2000 + int(match[3]), months[match[1]], int(match[2])).isoformat()


def registry_identity(identity, text):
    # Pypdf wraps long table cells. Whitespace normalization is not fuzzy name
    # matching: all fund and option tokens below must still match exactly.
    text = " ".join(text.split())
    if _one(r"1 Fund Name (.*?) 2 Option Names", text) != NAME:
        raise ValueError("issuer fund identity differs")
    isins = _one(r"28 ISINs (.*?) 29 AMFI Codes", text)
    codes = _one(r"29 AMFI Codes \(To be phased out\) (.*?) 30 SEBI Codes", text)
    label = NAME + " - IDCW Plan"
    code_rows = re.findall(r"([0-9]{6})\s*-\s*(.*?)(?= [0-9]{6}\s*-|$)", codes)
    if not re.match(r"^[0-9]{6}\s*-", codes) or len(code_rows) != 4 or len({c for c, _ in code_rows}) != 4:
        raise ValueError("ambiguous issuer AMFI section")
    if [c for c, name in code_rows if name == label] != ["101763"]:
        raise ValueError("exact regular IDCW AMFI mapping absent")
    payout = _one(r"HDFC FLEXI CAP FUND - DIVIDEND PAYOUT OPTION-\s*(INF[A-Z0-9]{9})(?= HDFC|$)", isins)
    reinvest = _one(r"HDFC FLEXI CAP FUND - DIVIDEND REINVESTMENT OPTION-\s*(INF[A-Z0-9]{9})(?= HDFC|$)", isins)
    if payout == reinvest or not all(valid_isin(x) for x in (payout, reinvest)):
        raise ValueError("invalid issuer payout/reinvestment identifiers")
    if len(identity["isins"]) != 2 or set(identity["isins"]) != {payout, reinvest}:
        raise ValueError("supplied identity and issuer ISINs differ")
    return {"amfi_code": 101763, "scheme": NAME, "plan": "REGULAR", "option": "IDCW",
            "payout_isin": payout, "reinvestment_isin": reinvest,
            "current_database_identity_verified": False}


def history_rows(text, *, period):
    months = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
    year, month = map(int, period.split("-"))
    _one(r"(?m)^\s*[0-9]+\s*\|\s*" + months[month - 1] + " " + str(year) + r"\s*$", text)
    block = _one(
        r"HDFC FLEXI CAP FUND \(Past 3 years\) IDCW Option\s*(.*?)\s*HDFC LARGE CAP FUND \(Past 3 years\) IDCW Option",
        text, re.S,
    )
    header, body = re.split(r"(?m)(?=^Mar [0-9]{2}, [0-9]{2} )", block, maxsplit=1) if re.search(r"(?m)^Mar [0-9]{2}, [0-9]{2} ", block) else ("", "")
    expected_header = "Record Date Regular Plan Direct Plan Cum IDCW NAV(₹) IDCW per Unit(₹) For Cum IDCW NAV(₹) IDCW per Unit(₹) For Individual & HUF Others Individual & HUF Others"
    if " ".join(header.split()) != expected_header:
        raise ValueError("unsupported IDCW history columns/order")
    rows = []
    for line in body.splitlines():
        if not line.strip():
            continue
        tokens = re.fullmatch(r"([A-Z][a-z]{2} [0-9]{2}, [0-9]{2}) ((?:[0-9.]+ ){5}[0-9.]+)\s*", line)
        if not tokens:
            raise ValueError("unparsed IDCW history row")
        day = _date(tokens[1])
        values = [_decimal(x) for x in tokens[2].split()]
        if day[:7] > period or Decimal(values[1]) != Decimal(values[2]):
            raise ValueError("history date or recipient-class amount differs")
        if Decimal(values[1]) >= Decimal(values[0]):
            raise ValueError("unsupported distribution/NAV relation")
        rows.append({"record_date": day, "amount_inr_per_unit": values[1],
                     "amount_others_inr_per_unit": values[2], "cum_idcw_nav_inr": values[0],
                     "amount_basis": "reported_history_not_net_investor_receipt",
                     "ex_date": None, "payment_date": None, "reinvestment_date": None,
                     "reinvestment_nav_inr": None, "scheme_levy_inr_per_unit": None,
                     "unit_event_coverage": "unknown"})
    if not 1 <= len(rows) <= 20 or [x["record_date"] for x in rows] != sorted({x["record_date"] for x in rows}):
        raise ValueError("missing, duplicate or unsorted history events")
    return rows


def notice_assertion(text):
    text = " ".join(text.split())
    _one(r"fixed Thursday, March 05, 2026 \(or the immediately following Business Day, if that day is not a Business Day\) as the Record Date", text)
    amount, pre_nav = _one(
        r"HDFC Flexi Cap Fund - Regular Plan - IDCW Option \(Payout and Reinvestment\) ([0-9.]+) ([0-9.]+) 10\.00 HDFC Flexi Cap Fund - Direct Plan -",
        text,
    )
    _one(r"Amount of Distribution \(` per unit\)#", text)
    _one(r"February 27, 2026 \(` per unit\) Face Value \(` per unit\)", text)
    _one(r"lower of that mentioned above or the available distributable surplus", text)
    _one(r"NAV of the IDCW Option\(s\).*?statutory levy, if any", text)
    _one(r"net of applicable TDS.*?applicable NAV per Unit \(adjusted for applicable stamp duty\)", text)
    _one(r"Date : March 01, 2026 Authorized Signatory", text)
    return {"notice_date": "2026-03-01", "announced_record_date": "2026-03-05",
            "record_date_business_day_condition": True,
            "announced_maximum_inr_per_unit": _decimal(amount), "face_value_inr_per_unit": "10.00",
            "pre_notice_nav_inr": _decimal(pre_nav), "pre_notice_nav_date": "2026-02-27",
            "amount_is_conditional": True, "statutory_levy_quantified": False,
            "tds_applicable_rate": None, "stamp_duty_quantified": False,
            "reinvestment_nav_inr": None, "reinvestment_date": None}


def decode(content, pages):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES or not content.startswith(b"%PDF-"):
        raise ValueError("bounded original IDCW PDF required")
    if not pages or len(pages) > 2 or any(type(p) is not int or not 1 <= p <= 400 for p in pages) or pages != sorted(set(pages)):
        raise ValueError("explicit bounded selected pages required")
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-m", "mfdataindia.enrichment._idcw_discovery_worker", *map(str, pages)],
            input=content, capture_output=True, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("IDCW PDF worker deadline exceeded") from None
    if result.returncode or len(result.stdout) > 3 * 1024 * 1024:
        raise ValueError("IDCW PDF worker failed or exceeded bounds")
    value = json.loads(result.stdout)
    if not isinstance(value, list) or len(value) != len(pages) or any(
        x.get("page") != number or not isinstance(x.get("text"), str)
        or not 0 < len(x["text"]) <= 200000 for number, x in zip(pages, value)
    ):
        raise ValueError("invalid IDCW PDF worker result")
    return value


def discover(request: IdcwDiscoveryRequest, sources: dict[str, bytes]):
    request = IdcwDiscoveryRequest.model_validate(request.model_dump(mode="json"))
    extracted, lineage = {}, {}
    for role in ("history", "notice", "registry"):
        source = getattr(request, role)
        content = sources.get(role)
        if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != source.content_hash:
            raise ValueError("source bytes/hash differ")
        pages = decode(content, source.pages)
        extracted[role] = pages
        lineage[role] = {**source.model_dump(mode="json"), "byte_length": len(content),
                         "text_hashes": [{"page": p["page"], "sha256": hashlib.sha256(p["text"].encode()).hexdigest()} for p in pages]}
    report = reconcile(request, extracted)
    report.update(source_lineage=lineage, policy=policy(), policy_hash=fingerprint(policy()),
                  engine_code_hash=code_hash(), extractor_version=version("pypdf"))
    report["report_hash"] = fingerprint(report)
    return report


def reconcile(request, extracted):
    """Pure parser seam for hermetic tests; this cannot produce a certificate."""
    identity = request.identity.model_dump(mode="json")
    bridge = registry_identity(identity, extracted["registry"][0]["text"])
    history = history_rows(extracted["history"][1]["text"], period=request.history_period)
    # First page must corroborate the month of this multi-page document too.
    first_page = " ".join(extracted["history"][0]["text"].split())
    month = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")[int(request.history_period[-2:]) - 1]
    if not re.search(r"\b" + month + r" (?:[0-9]{1,2}, )?" + request.history_period[:4] + r"\b", first_page):
        raise ValueError("factsheet cover period differs")
    notice = notice_assertion(extracted["notice"][0]["text"])
    matches = [row for row in history if row["record_date"] == notice["announced_record_date"]]
    if len(matches) != 1:
        raise ValueError("announcement lacks corroborating reported history")
    amount_ok = Decimal(matches[0]["amount_inr_per_unit"]) <= Decimal(notice["announced_maximum_inr_per_unit"])
    reasons = ["complete_record_date_listing_not_verified", "applicable_reinvestment_date_and_nav_not_disclosed",
               "scheme_levy_and_unit_event_coverage_not_verified", "current_database_identity_not_certified"]
    if not amount_ok:
        reasons.insert(0, "history_exceeds_conditional_announcement")
    return {
        "format": "idcw-source-discovery-report-v1", "state": "blocked",
        "publication": "discovery_only_not_accepted_events", "identity": identity,
        "identity_evidence": bridge, "identity_hash": fingerprint(identity),
        "proposed_window": {"start": request.proposed_start.isoformat(), "end": request.proposed_end.isoformat()},
        "history_reporting_month": request.history_period, "issuer_history_scope_label": "Past 3 years",
        "reported_history": history, "conditional_announcement": notice,
        "announcement_consistent_with_history": amount_ok,
        "proposed_window_reported_event_count": sum(request.proposed_start.isoformat() <= row["record_date"] <= request.proposed_end.isoformat() for row in history),
        "coverage_state": "unknown", "complete_event_window_proven": False,
        "accepted_event_count": 0, "analytics_eligible": False,
        "freshness_certified": False, "universe_complete": False, "mf_e09_complete": False,
        "blocking_reasons": reasons, "source_rows_changed": 0, "source_data_replaced": False,
        "representation": "source-discovery observations only; not a complete event ledger or product readiness certificate",
        "request": request.model_dump(mode="json"),
    }
