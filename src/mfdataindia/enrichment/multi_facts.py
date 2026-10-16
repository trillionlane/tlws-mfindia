"""Reviewed source authority, distinct from the read-only AMFI directory census."""

import hashlib
import json
from copy import deepcopy
from datetime import date
from pathlib import Path

from mfdataindia.enrichment import monthly_current, monthly_pdf
from mfdataindia.enrichment.evidence import public_url
from mfdataindia.enrichment.schemas import Identity
from mfdataindia.review.policy import fingerprint

REGISTRY = "official-facts-sources-v1.json"
REGISTRY_SHA256 = "a097eb2436b0efb3b4283bf2fd8fb7c00d3c7135526736770a8817bd6fe52b93"
JOB = "tlws-mf-official-facts-refresh-dev"


def registry(root=None):
    root = root or (
        Path("/app")
        if (Path("/app/config") / REGISTRY).is_file()
        else Path(__file__).resolve().parents[3]
    )
    raw = (root / "config" / REGISTRY).read_bytes()
    if hashlib.sha256(raw).hexdigest() != REGISTRY_SHA256:
        raise ValueError("reviewed acquisition registry differs")
    document = json.loads(raw)
    if (
        set(document) != {"format", "version", "job", "maximum_sources_per_execution", "sources"}
        or document["format"] != "official-facts-source-registry-v1"
        or document["version"] != 1
        or document["job"] != JOB
        or document["maximum_sources_per_execution"] != 2
        or [s["key"] for s in document["sources"]] != ["absl-large-cap", "hdfc-flexi-cap"]
        or [s["cohort"] for s in document["sources"]] != [[103173, 103174], [101763]]
    ):
        raise ValueError("bounded source registry differs")
    source = document["sources"][1]
    Identity.model_validate(source["identity"])
    for role in ("identity_source", "edition"):
        public_url(source[role]["url"], {"files.hdfcfund.com"})
    return document


def source():
    return registry()["sources"][1]


def policy():
    return {
        "id": "registry-official-facts-refresh",
        "version": 1,
        "registry_hash": fingerprint(registry()),
        "authority": "checked-in immutable adapter/cohort/edition registry; directory entries are not transport permission",
        "bounds": {
            "sources": 2,
            "hdfc_requests": 2,
            "hdfc_bytes_per_original": 8388608,
            "retained_absl_book_bytes": 16777216,
        },
        "transport": "public DNS pinned TLS; no proxy/cookies, redirect, decompression or retry; fixed registered URL only",
        "hdfc_discovery": "reviewed September 2026 edition only; new edition or layout requires reviewed registry update; not latest/current-month certification",
        "pdf_policy_hash": fingerprint(monthly_pdf.policy()),
        "hdfc_scope": "exact registered Regular IDCW identity; fund benchmark/AUM/managers and Regular BER; manager footnote retained; risk unavailable, no riskometer inference",
        "hdfc_dates": "BER and AUM retain their explicit as-on dates; benchmark and managers retain document-context disclosure dates, not inferred field-effective dates",
        "publication": "immutable originals and complete registered cohort plus database-sealed successful run receipt; direct snapshots do not publish",
        "failures": "two fixed Cloud Run tasks, one registered adapter per index, parallelism one and zero retries; serial tasks avoid contending with each other for the shared publication lock; independent task timeout, transactions and receipts; one failure cannot roll back another source or erase last usable values; Cloud Run attempts all tasks and the job is unsuccessful on any task failure",
        "replay": "execution and source key identify a receipt; originals/snapshots are content-addressed; uncertain commit reconciled, never blindly retried",
        "preservation": "append-only sidecars; no source-record, NAV, identity or canonical calculation writes",
        "verification": "needs_verification is independent of publication; no synthetic human approvals",
        "all_amc_data_complete": False,
        "adjusted_idcw_analytics_enabled": False,
    }


def current_policy():
    document = deepcopy(monthly_current.policy())
    document.update(
        version=3,
        previous_policy_hash=fingerprint(monthly_current.policy()),
        multi_source_policy_hash=fingerprint(policy()),
        scope="retained v2 published inputs plus registered exact-identity HDFC Regular IDCW receipts",
        publication_boundary="retain the frozen v2 assertion graph; append only sealed successful multi-source receipts; missing HDFC risk never erases prior values",
    )
    return document


def registered_date():
    return date.fromisoformat(source()["edition"]["disclosure_as_of"])
