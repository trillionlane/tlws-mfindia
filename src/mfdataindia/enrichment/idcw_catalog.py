"""Versioned issuer adapters and observations; v1 sources/policy stay frozen."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import re
from typing import Literal

from pydantic import Field, model_validator
from mfdataindia.enrichment import idcw_discovery as decoder
from mfdataindia.enrichment.distribution_schemas import DistributionEvent, DistributionWindow
from mfdataindia.enrichment.evidence import valid_isin
from mfdataindia.enrichment.schemas import Identity, Model
from mfdataindia.review.policy import fingerprint

COHORT = {
    101763: ("HDFC Flexi Cap Fund", "REGULAR", "INF179K01582", "INF179K01590"),
    118954: ("HDFC Flexi Cap Fund", "DIRECT", "INF179K01VL5", "INF179K01VM3"),
    102001: ("HDFC Large Cap Fund", "REGULAR", "INF179K01BC6", "INF179K01BD4"),
    119017: ("HDFC Large Cap Fund", "DIRECT", "INF179K01YT2", "INF179K01YU0"),
}
ADAPTER = "hdfc-idcw-history-v1"
HASH = r"^[0-9a-f]{64}$"


def declared_identity(code):
    name, plan, payout, reinvestment = COHORT[code]
    return {
        "code": code,
        "amc": "HDFC Mutual Fund",
        "scheme_name": name,
        "plan": plan,
        "option": "IDCW",
        "isins": sorted([payout, reinvestment]),
    }


def policy():
    return deepcopy(
        {
            "id": "reported-idcw-observations",
            "version": 2,
            "publication": "reported_observations_only_not_complete_event_history",
            "adapters": {
                ADAPTER: {
                    "cohort": [declared_identity(c) for c in sorted(COHORT)],
                    "layout": "separate Regular/Direct INR-per-unit history columns and exact issuer scheme-summary bridge",
                }
            },
            "amounts": "retain each plan and recipient-class decimal token; no rounding, rate conversion, or Regular-to-Direct substitution",
            "dates": "reported record date only, not payment/ex/reinvestment date",
            "coverage": "unknown including an empty selected window; summary is not a complete payout calendar",
            "inventory": "provider IDCW population; snapshot_count counts v2 snapshots only, null when identity/overflow withholds; unsupported never means no payouts",
            "announcements": "not acquired by this adapter; prior v1 notices retained and included in same-fund conflict checks",
            "conflicts": "retain all v2 snapshots plus applicable v1 assertions; numeric disagreement never resolved by ingestion order",
            "unknowns": "reinvestment price/date, levies and unit events remain unknown, never zero or inferred",
            "analytics": "ineligible for adjusted returns/risk/ranking/calculators; no existing eligibility changes",
            "identity": "exact current provider identity and issuer AMFI/payout/reinvestment ISIN bridge; drift withholds observations",
            "preservation": "append-only sidecars; v1 policy/snapshots/APIs and all source NAV/facts/results unchanged",
            "bounds": {
                "batch_funds": 4,
                "sources_per_request": 2,
                "window_days": 366,
                "events_per_snapshot": 20,
                "snapshots_per_fund": 100,
                "source_bytes": decoder.MAX_BYTES,
                "inventory_page": 100,
            },
        }
    )


def engine_hash():
    digest = sha256()
    for path in (
        Path(__file__),
        Path(decoder.__file__),
        Path(decoder.__file__).with_name("_idcw_discovery_worker.py"),
        Path(__file__).with_name("distribution_schemas.py"),
        Path(__file__).with_name("evidence.py"),
        Path(__file__).parents[1] / "review/policy.py",
    ):
        data = path.read_bytes()
        digest.update(path.name.encode() + b"\0" + len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


class IdcwRequest(Model):
    format: Literal["reported-idcw-request-v2"]
    adapter: Literal["hdfc-idcw-history-v1"]
    identity: Identity
    proposed_start: date
    proposed_end: date
    history_period: str = Field(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    history: decoder.IdcwDiscoverySource
    registry: decoder.IdcwDiscoverySource
    use_basis: Literal["operator_reported_authorization"]
    authority_reference: str = Field(min_length=8, max_length=500)

    @model_validator(mode="after")
    def bounded(self):
        if (
            self.identity.code not in COHORT
            or self.identity.model_dump() != declared_identity(self.identity.code)
            or not date(2010, 1, 1) <= self.proposed_start <= self.proposed_end
            or (self.proposed_end - self.proposed_start).days >= 366
            or self.proposed_end.isoformat()[:7] > self.history_period
            or len(self.history.pages) != 2
            or self.history.pages[0] != 1
            or self.registry.pages != [1]
            or self.history.content_hash == self.registry.content_hash
            or self.history.file == self.registry.file
            or any(x.observed_at.date() < self.proposed_end for x in (self.history, self.registry))
            or self.history.observed_at.strftime("%Y-%m") < self.history_period
            or any(ord(c) < 32 for c in self.authority_reference)
        ):
            raise ValueError("unsupported identity, layout or observation window")
        return self


class IdcwIdentityEvidence(Model):
    amfi_code: int
    scheme: str
    plan: Literal["REGULAR", "DIRECT"]
    option: Literal["IDCW"]
    payout_isin: str
    reinvestment_isin: str


class IdcwSnapshot(Model):
    format: Literal["reported-idcw-observations-v2"]
    snapshot_id: str = Field(pattern=HASH)
    code: int
    identity: Identity
    identity_hash: str = Field(pattern=HASH)
    identity_evidence: IdcwIdentityEvidence
    request: IdcwRequest
    adapter: Literal["hdfc-idcw-history-v1"]
    window: DistributionWindow
    history_reporting_month: str
    issuer_history_scope_label: Literal["Past 3 years"]
    source_lineage: dict
    extractor_version: str
    engine_code_hash: str = Field(pattern=HASH)
    policy_hash: str = Field(pattern=HASH)
    events: list[DistributionEvent] = Field(max_length=20)
    conditional_announcement: None
    announcement_evidence: Literal["not_captured_in_this_snapshot"]
    coverage_state: Literal["unknown"]
    complete_event_window_proven: Literal[False]
    analytics_eligible: Literal[False]
    freshness_certified: Literal[False]
    universe_complete: Literal[False]
    source_data_replaced: Literal[False]


State = Literal[
    "not_captured",
    "unsupported",
    "observed",
    "identity_changed",
    "conflicting_evidence",
    "too_many_snapshots",
]


class IdcwSummary(Model):
    snapshot_id: str
    window: DistributionWindow
    observed_event_count: int
    policy_hash: str


class IdcwCoverage(Model):
    code: int
    state: State
    reason: str
    coverage_state: Literal["unknown"]
    analytics_eligible: Literal[False]
    complete_event_window_proven: Literal[False]
    freshness_certified: Literal[False]
    universe_complete: Literal[False]
    observed_record_dates: list[date] | None
    conflicting_record_dates: list[date]
    snapshots: list[IdcwSummary] = Field(max_length=100)
    legacy_snapshot_ids: list[str] = Field(max_length=100)


class IdcwResponse(Model):
    code: int
    snapshot_id: str
    state: State
    reason: str
    coverage_state: Literal["unknown"]
    analytics_eligible: Literal[False]
    conflicting_record_dates: list[date]
    snapshot: IdcwSnapshot | None


class IdcwInventoryItem(Model):
    code: int
    state: State
    reason: str
    snapshot_count: int | None
    coverage_state: Literal["unknown"]
    analytics_eligible: Literal[False]


class IdcwInventory(Model):
    items: list[IdcwInventoryItem] = Field(max_length=100)
    next_after_code: int | None
    population: Literal["provider_idcw_schemes_not_certified_event_coverage"]
    coverage_state: Literal["unknown"]
    analytics_eligible: Literal[False]


def _label(text):
    return re.sub(r"\s*-\s*", " - ", " ".join(text.split())).upper()


def registry_identity(identity, text):
    text = " ".join(text.split())
    name, plan, payout, reinvest = COHORT[identity["code"]]
    if decoder._one(r"1 Fund Name (.*?) 2 Option Names", text) != name:
        raise ValueError("issuer fund identity differs")
    codes = decoder._one(r"29 AMFI Codes \(To be phased out\) (.*?) 30 SEBI Codes", text)
    rows = re.findall(r"([0-9]{6})\s*-\s*(.*?)(?= [0-9]{6}\s*-|$)", codes)
    if len(rows) != 4 or len({r[0] for r in rows}) != 4 or not re.match(r"^[0-9]{6}\s*-", codes):
        raise ValueError("ambiguous issuer AMFI section")
    option_label = (
        "IDCW Plan" if identity["code"] == 101763 else "IDCW Option - " + plan.title() + " Plan"
    )
    if [c for c, label in rows if _label(label) == _label(name + " - " + option_label)] != [
        str(identity["code"])
    ]:
        raise ValueError("issuer plan-specific AMFI mapping absent")
    section = decoder._one(r"28 ISINs (.*?) 29 AMFI Codes", text)
    mappings = re.findall(r"(.+?)\s*-\s*(INF[A-Z0-9]{9})(?= HDFC|$)", section)
    if len(mappings) != 6 or len({isin for _, isin in mappings}) != 6:
        raise ValueError("ambiguous issuer ISIN section")
    prefix = name + (" - DIRECT PLAN" if plan == "DIRECT" else "")
    term = "DIVIDEND" if name == "HDFC Flexi Cap Fund" else "IDCW"
    for kind, expected in (("PAYOUT", payout), ("REINVESTMENT", reinvest)):
        label = _label(prefix + " - " + term + " " + kind + " OPTION")
        if [isin for desc, isin in mappings if _label(desc) == label] != [
            expected
        ] or not valid_isin(expected):
            raise ValueError("issuer payout/reinvestment mapping differs")
    return {
        "amfi_code": identity["code"],
        "scheme": name,
        "plan": plan,
        "option": "IDCW",
        "payout_isin": payout,
        "reinvestment_isin": reinvest,
    }


def history_rows(text, *, identity, period):
    months = (
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
    )
    year, month = map(int, period.split("-"))
    decoder._one(r"(?m)^\s*[0-9]+\s*\|\s*" + months[month - 1] + " " + str(year) + r"\s*$", text)
    name = re.escape(identity["scheme_name"].upper())
    block = decoder._one(
        name + r" \(Past 3 years\) IDCW Option\s*(.*?)(?=HDFC [A-Z]|\Z)", text, re.S
    )
    parts = re.split(r"(?m)(?=^[A-Z][a-z]{2} [0-9]{2}, [0-9]{2} )", block, maxsplit=1)
    expected = "Record Date Regular Plan Direct Plan Cum IDCW NAV(₹) IDCW per Unit(₹) For Cum IDCW NAV(₹) IDCW per Unit(₹) For Individual & HUF Others Individual & HUF Others"
    if len(parts) != 2 or " ".join(parts[0].split()) != expected:
        raise ValueError("unsupported plan columns/history header")
    rows = []
    offset = 0 if identity["plan"] == "REGULAR" else 3
    for line in parts[1].splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(
            r"([A-Z][a-z]{2} [0-9]{2}, [0-9]{2}) ((?:[0-9.]+ ){5}[0-9.]+)\s*", line
        )
        if not match:
            raise ValueError("unparsed IDCW history row")
        day = decoder._date(match[1])
        values = [decoder._decimal(v) for v in match[2].split()]
        if day[:7] > period or any(
            Decimal(values[i + j]) >= Decimal(values[i]) for i in (0, 3) for j in (1, 2)
        ):
            raise ValueError("unsupported date or distribution/NAV relation")
        rows.append(
            {
                "record_date": day,
                "amount_inr_per_unit": values[offset + 1],
                "amount_others_inr_per_unit": values[offset + 2],
                "cum_idcw_nav_inr": values[offset],
                "amount_basis": "reported_history_not_net_investor_receipt",
                "source_role": "history",
                "ex_date": None,
                "payment_date": None,
                "reinvestment_date": None,
                "reinvestment_nav_inr": None,
                "scheme_levy_inr_per_unit": None,
                "unit_event_coverage": "unknown",
            }
        )
    if not 1 <= len(rows) <= 20 or [r["record_date"] for r in rows] != sorted(
        {r["record_date"] for r in rows}
    ):
        raise ValueError("missing, duplicate or unsorted history")
    return rows


def derive(request, sources):
    request = IdcwRequest.model_validate(request.model_dump(mode="json"))
    if set(sources) != {"history", "registry"}:
        raise ValueError("exact source roles required")
    extracted, lineage = {}, {}
    for role in ("history", "registry"):
        source, raw = getattr(request, role), sources[role]
        if not isinstance(raw, bytes) or sha256(raw).hexdigest() != source.content_hash:
            raise ValueError("original source hash differs")
        pages = decoder.decode(raw, source.pages)
        extracted[role] = pages
        lineage[role] = {
            **source.model_dump(mode="json"),
            "byte_length": len(raw),
            "text_hashes": [
                {"page": p["page"], "sha256": sha256(p["text"].encode()).hexdigest()} for p in pages
            ],
        }
    identity = request.identity.model_dump(mode="json")
    bridge = registry_identity(identity, extracted["registry"][0]["text"])
    month = (
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
    )[int(request.history_period[-2:]) - 1]
    if not re.search(
        r"\b" + month + r" (?:[0-9]{1,2}, )?" + request.history_period[:4] + r"\b",
        " ".join(extracted["history"][0]["text"].split()),
    ):
        raise ValueError("factsheet cover month differs")
    rows = history_rows(
        extracted["history"][1]["text"], identity=identity, period=request.history_period
    )
    start, end = request.proposed_start.isoformat(), request.proposed_end.isoformat()
    body = {
        "format": "reported-idcw-observations-v2",
        "code": identity["code"],
        "identity": identity,
        "identity_hash": fingerprint(identity),
        "identity_evidence": bridge,
        "request": request.model_dump(mode="json"),
        "adapter": request.adapter,
        "window": {"start": start, "end": end},
        "history_reporting_month": request.history_period,
        "issuer_history_scope_label": "Past 3 years",
        "source_lineage": lineage,
        "extractor_version": version("pypdf"),
        "engine_code_hash": engine_hash(),
        "policy_hash": fingerprint(policy()),
        "events": [
            {**r, "source_page": request.history.pages[1]}
            for r in rows
            if start <= r["record_date"] <= end
        ],
        "conditional_announcement": None,
        "announcement_evidence": "not_captured_in_this_snapshot",
        "coverage_state": "unknown",
        "complete_event_window_proven": False,
        "analytics_eligible": False,
        "freshness_certified": False,
        "universe_complete": False,
        "source_data_replaced": False,
    }
    return IdcwSnapshot.model_validate({**body, "snapshot_id": fingerprint(body)}).model_dump(
        mode="json"
    )
