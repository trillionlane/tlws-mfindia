"""Source-reported observations, never a distribution-adjusted return series."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from mfdataindia.enrichment import idcw_discovery as discovery
from mfdataindia.review.policy import fingerprint

_POLICY = {
    "id": "reported-idcw-observations",
    "version": 1,
    "scope": "HDFC Flexi Cap Regular IDCW, AMFI 101763; explicitly selected window and original PDF pages",
    "publication": "reported_observations_only_not_complete_event_history",
    "identity": "exact current provider identity equals request; issuer AMFI and payout/reinvestment ISIN bridge required",
    "amounts": "retain source decimal tokens in INR per unit; no rounding or percentage conversion; recipient classes retained separately",
    "assertions": "reported historical amount is distinct from conditional announcement maximum; no net investor receipt claim",
    "dates": "reported record date only; ex/payment/reinvestment dates remain null",
    "prices": "cum-IDCW and pre-notice NAV retained only in their reported roles, never a reinvestment NAV",
    "coverage": "unknown even with zero observed rows; requested window is a filter, not complete coverage proof; no freshness or universe certification",
    "conflicts": "retain every snapshot; compare numeric historical amounts/cum-NAV and announcement ceilings for the same record date; never latest-source-wins",
    "unknowns": "scheme levy, applicable reinvestment NAV/date and unit-event coverage unknown, never imputed zero",
    "analytics": "ineligible for adjusted returns, risk, ranking and calculators; no existing eligibility changed",
    "preservation": "append-only separate sources and snapshots; existing sources, NAVs, facts, identities and results unchanged",
    "bounds": {
        "sources_per_snapshot": 3,
        "events_per_snapshot": 20,
        "snapshots_per_fund": 100,
        "source_bytes": discovery.MAX_BYTES,
        "window_days": 366,
    },
}


def policy():
    return deepcopy(_POLICY)


def engine_hash():
    digest = sha256()
    for path in (
        Path(__file__),
        Path(discovery.__file__),
        Path(discovery.__file__).with_name("_idcw_discovery_worker.py"),
        Path(discovery.__file__).with_name("evidence.py"),
        Path(__file__).with_name("distribution_schemas.py"),
        Path(__file__).parents[1] / "review/policy.py",
    ):
        content = path.read_bytes()
        digest.update(path.name.encode() + b"\0" + len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def derive(request, sources):
    # A separate policy wraps the unchanged discovery adapter. Discovery's old
    # report stays blocked; it is never relabelled a complete-history certificate.
    request = discovery.IdcwDiscoveryRequest.model_validate(request.model_dump(mode="json"))
    if (request.proposed_end - request.proposed_start).days >= 366:
        raise ValueError("bounded observation window required")
    report = discovery.discover(request, sources)
    start, end = request.proposed_start.isoformat(), request.proposed_end.isoformat()
    events = []
    for row in report["reported_history"]:
        if start <= row["record_date"] <= end:
            events.append(
                {**row, "source_role": "history", "source_page": request.history.pages[1]}
            )
    notice = report["conditional_announcement"]
    if not start <= notice["announced_record_date"] <= end:
        notice = None
    body = {
        "format": "reported-idcw-observations-v1",
        "code": request.identity.code,
        "identity": request.identity.model_dump(mode="json"),
        "identity_hash": report["identity_hash"],
        "identity_evidence": {
            k: v
            for k, v in report["identity_evidence"].items()
            if k != "current_database_identity_verified"
        },
        "window": {"start": start, "end": end},
        "history_reporting_month": request.history_period,
        "issuer_history_scope_label": "Past 3 years",
        "request": request.model_dump(mode="json"),
        "source_lineage": report["source_lineage"],
        "extractor_version": report["extractor_version"],
        "policy_hash": fingerprint(policy()),
        "engine_code_hash": engine_hash(),
        "events": events,
        "conditional_announcement": notice,
        "coverage_state": "unknown",
        "complete_event_window_proven": False,
        "analytics_eligible": False,
        "freshness_certified": False,
        "universe_complete": False,
        "source_data_replaced": False,
    }
    return {**body, "snapshot_id": fingerprint(body)}


def conflicts(documents):
    """Numeric equality ignores cosmetic decimal precision, not distinct facts."""
    from decimal import Decimal

    dates = {}
    for doc in documents:
        for row in doc["events"]:
            entry = dates.setdefault(row["record_date"], {"values": set(), "maxima": []})
            entry["values"].add(
                tuple(
                    Decimal(row[k])
                    for k in (
                        "amount_inr_per_unit",
                        "amount_others_inr_per_unit",
                        "cum_idcw_nav_inr",
                    )
                )
            )
        notice = doc["conditional_announcement"]
        if notice:
            entry = dates.setdefault(
                notice["announced_record_date"], {"values": set(), "maxima": []}
            )
            entry["maxima"].append(Decimal(notice["announced_maximum_inr_per_unit"]))
    return sorted(
        day
        for day, item in dates.items()
        if len(item["values"]) > 1
        or any(
            values[0] > ceiling or values[1] > ceiling
            for values in item["values"]
            for ceiling in item["maxima"]
        )
    )
