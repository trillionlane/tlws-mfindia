"""Isolated v5-derived SBI v2 source authority; legacy registries stay sealed."""

import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from mfdataindia.enrichment import book_sources as legacy
from mfdataindia.enrichment import multi_facts, sbi_book, sbi_book_v2
from mfdataindia.review.policy import fingerprint

VERSION = 9
POLICY_VERSION = 15
REGISTRY_FILE = "official-book-sources-v9-sbi-document-date.json"
REGISTRY_SHA256 = "898dab72872e82610cf17648ddb34aad3404d23a7124b68954e800b230561fab"


def _version(version):
    selected = VERSION if version is None else version
    if type(selected) is not int or selected != VERSION:
        raise ValueError("isolated SBI v2 registry version required")
    return selected


@contextmanager
def select_registry(version):
    """Compatibility context for the isolated route; it mutates no shared state."""
    _version(version)
    yield


def registry(version=None, root=None):
    _version(version)
    root = root or (
        Path("/app")
        if (Path("/app/config") / REGISTRY_FILE).is_file()
        else Path(__file__).resolve().parents[3]
    )
    raw = (root / "config" / REGISTRY_FILE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != REGISTRY_SHA256:
        raise ValueError("isolated SBI v2 registry differs from reviewed pin")
    document = json.loads(raw)
    previous = legacy.registry(5, root)
    expected_sources = deepcopy(previous["sources"])
    next(source for source in expected_sources if source["key"] == sbi_book.KEY)[
        "adapter"
    ] = "sbi-full-book-v2"
    transition = document.get("authority_transition", {})
    if (
        document.get("format") != "official-amc-book-source-registry-v1"
        or document.get("version") != VERSION
        or document.get("job") != multi_facts.JOB
        or document.get("retained_source_registry_hash")
        != fingerprint(multi_facts.registry(root))
        or document.get("maximum_sources_per_execution")
        != 2 + len(document.get("sources", []))
        or document.get("sources") != expected_sources
        or transition.get("mode") != "sbi_document_date_revision"
        or transition.get("source_key") != sbi_book.KEY
        or transition.get("previous_source_version") != 5
        or transition.get("previous_registry_hash") != fingerprint(previous)
        or transition.get("previous_specification_hash")
        != fingerprint(legacy.specification(sbi_book.KEY, version=5))
        or transition.get("source_count_changed") is not False
        or transition.get("financial_field_rules_changed") is not False
        or transition.get("date_evidence_model_changed") is not True
        or transition.get("retain_previous_specifications") is not True
        or transition.get("source_rows_changed") != 0
        or transition.get("derived_from_current_version") != 5
        or transition.get("excluded_allocated_side_versions") != [6, 7, 8]
    ):
        raise ValueError("exact isolated v5-derived SBI document-date revision required")
    return document


def source(key, *, version=None):
    matches = [item for item in registry(version)["sources"] if item["key"] == key]
    if len(matches) != 1:
        raise ValueError("isolated SBI v2 source key required")
    return matches[0]


def specification(key, *, version=None):
    _version(version)
    if key != sbi_book.KEY:
        return legacy.specification(key, version=5)
    item = deepcopy(source(key))
    if item.get("adapter") != "sbi-full-book-v2":
        raise ValueError("isolated SBI v2 adapter required")
    from mfdataindia.enrichment import (
        amc_book_pdf,
        sbi_acquisition,
        sbi_archive,
        sbi_batch_v2,
        sbi_runtime_v2,
    )

    item["parser_policy"] = sbi_book_v2.policy()
    item["parser_policy_hash"] = fingerprint(item["parser_policy"])
    item["parser_engine_hash"] = hashlib.sha256(
        Path(sbi_book_v2.__file__).read_bytes()
    ).hexdigest()
    item["evidence_engine_hashes"] = {
        module.__name__.rsplit(".", 1)[-1]: hashlib.sha256(
            Path(module.__file__).read_bytes()
        ).hexdigest()
        for module in (sbi_acquisition, sbi_archive, sbi_batch_v2, sbi_runtime_v2)
    }
    item["decoder_engine_hash"] = hashlib.sha256(
        Path(amc_book_pdf.__file__).read_bytes()
        + Path(amc_book_pdf.__file__).with_name("_amc_book_pdf_worker.py").read_bytes()
    ).hexdigest()
    return item


def configuration(version=None):
    _version(version)
    document = registry()
    specifications = {
        item["key"]: specification(item["key"]) for item in document["sources"]
    }
    return {
        "retained_source_specification_hashes": {
            sbi_book.KEY: [fingerprint(legacy.specification(sbi_book.KEY, version=5))]
        },
        "registry": document,
        "registry_hash": fingerprint(document),
        "source_specifications": specifications,
        "source_specification_hashes": {
            key: fingerprint(value) for key, value in specifications.items()
        },
    }


def current_policy(version=None):
    _version(version)
    previous = legacy.current_policy(5)
    result = deepcopy(previous)
    document = registry()
    result.update(
        version=POLICY_VERSION,
        previous_policy_hash=fingerprint(previous),
        book_source_registry_hash=fingerprint(document),
        book_source_configuration_hash=fingerprint(configuration()),
        source_registry_version=VERSION,
        source_authority_transition=document["authority_transition"],
        sbi_dual_date_evidence={
            "reporting_period": "catalogue-derived calendar month-end remains canonical publication identity",
            "document_as_of": "exact shared Active/Passive first-page PDF date is sealed separately",
            "explicit_field_date": "field_effective_date equals document_as_of; never relabel reporting_period",
            "legacy_sbi_v1": "retained specification and historical batch/run behavior unchanged",
        },
    )
    return result
