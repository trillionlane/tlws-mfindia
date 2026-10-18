"""Versioned whole-AMC source authority; frozen FS07 registry stays untouched."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from mfdataindia.enrichment import absl_expansion, multi_facts, sbi_book, kotak_book, uti_book_v2, uti_book, uti_book_v3, uti_book_v4
from mfdataindia.enrichment.evidence import public_url
from mfdataindia.review.policy import fingerprint
from mfdataindia.enrichment import _registered_book_policy_v6

CURRENT_VERSION = 5
# Registry versions count source waves; policy revisions are independently
# allocated and never reuse an already-registered (id, version) candidate.
POLICY_VERSIONS = {1: 4, 2: 5, 3: 7, 4: 8, 5: 11}
REGISTRIES = {
    1: (
        "official-book-sources-v1.json",
        "2696318170d417cdcd8de581a665252425984cc1b9dcb648d93fd30efcbccd8c",
    ),
    2: (
        "official-book-sources-v2.json",
        "9ee1d4c2c569ea8f78552d133244342e111ff58f1c1b7bb46784f8f1b6d00cf2",
    ),
    3: (
        "official-book-sources-v3.json",
        "9453e96cff8c94a7ac1b7bc8851246c71ccd9b63b10dcded06d7ae78e5c77727",
    ),
    4: (
        "official-book-sources-v4.json",
        "acb158e5258646e278b9aa1ecea6219bd8b3d987afa088a6bd8d308334c2a482",
    ),
    5: (
        "official-book-sources-v5-aum-row.json",
        "1c147b9b7a8b7df9b5c465aa28c5a9dbbfe17abbf40b2e08dca6f181552ea2ec",
    ),
}


def registry(version=None, root=None):
    version = CURRENT_VERSION if version is None else version
    if type(version) is not int or version not in REGISTRIES:
        raise ValueError("reviewed book registry version required")
    filename, pin = REGISTRIES[version]
    root = root or (
        Path("/app")
        if (Path("/app/config") / filename).is_file()
        else Path(__file__).resolve().parents[3]
    )
    raw = (root / "config" / filename).read_bytes()
    if hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError("reviewed whole-AMC source registry differs")
    document = json.loads(raw)
    if (
        document["format"] != "official-amc-book-source-registry-v1"
        or document["version"] != version
        or document["job"] != multi_facts.JOB
        or document["retained_source_registry_hash"] != fingerprint(multi_facts.registry(root))
        or document["maximum_sources_per_execution"] != 2 + len(document["sources"])
        or len({s["key"] for s in document["sources"]}) != len(document["sources"])
    ):
        raise ValueError("registered source task/cohort authority differs")
    for source in document["sources"]:
        if source["key"] == uti_book.KEY:
            if (
                version < 4
                or source["adapter"] != ("uti-english-monthly-book-v4" if version >= 5 else "uti-english-monthly-book-v1")
                or source["amc"] != uti_book.AMC
                or source["catalogue_api"] != uti_book.CATALOGUE
                or source["catalogue_landing"] != "https://www.utimf.com/downloads/fund-watch"
                or source["directory_url"] != uti_book.DIRECTORY
                or source["book_prefix"] != uti_book.CDN
                or source["maximum_identity_families"] != 200
                or source["identity_workers"] != 4
                or source["maximum_total_original_bytes"] != 83886080
                or source["archive_prefix"] != "official-monthly-facts/uti-expanded/"
                or source["plan"] != "REGULAR"
                or source["in_scope"] is not True
                or source["period_count"] != 2
                or source["minimum_detail_pages"] != {"active": 20, "passive": 15}
                or source["maximum_current_variants"] != 1024
                or source["preserved_projection_codes"] != [101763, 103173, 103174]
                or source["language"] != "English"
                or source["expense_basis"] != "TER_separate_not_BER"
                or source["supplemental_fields"] != ["reported_total_expense_ratio"]
                or source["minimum_catalogue_originals"] != 2
            ):
                raise ValueError("bounded English UTI source authority differs")
            continue
        if source["key"] == kotak_book.KEY:
            if (
                version < 3
                or source["adapter"] != "kotak-monthly-book-v1"
                or source["amc"] != kotak_book.AMC
                or source["catalogue_api"] != kotak_book.CATALOGUE
                or source["directory_url"] != kotak_book.DIRECTORY
                or source["book_prefix"] != kotak_book.BOOK_PREFIX
                or source["maximum_identity_families"] != 200
                or source["identity_workers"] != 4
                or source["maximum_total_original_bytes"] != 83886080
                or source["archive_prefix"] != "official-monthly-facts/kotak-expanded/"
                or source["plan"] != "REGULAR"
                or source["in_scope"] is not True
                or source["period_count"] != 2
                or source["expected_recent_period_count"] != 2
                or source["minimum_current_detail_pages"] != 20
                or source["maximum_current_variants"] != 1024
                or source["preserved_projection_codes"] != [101763, 103173, 103174]
                or source["reviewed_historical"]["url"] != kotak_book.HISTORICAL
                or source["reviewed_historical"]["sha256"] != kotak_book.HISTORICAL_PDF_SHA256
                or source["reviewed_historical"]["actual_reporting_period"] != "2026-07-31"
                or source["reviewed_historical"]["discovery_original_sha256"]
                != "cd412f92d887b8220559d911003b4a104eac9506ff92beaf81266a7d18364980"
            ):
                raise ValueError("bounded reviewed Kotak authority differs")
            continue
        if source["key"] == sbi_book.KEY:
            if (
                version < 2
                or source["adapter"] != "sbi-full-book-v1"
                or source["amc"] != sbi_book.AMC
                or source["catalogue_api"] != sbi_book.CATALOGUE
                or source["directory_url"] != sbi_book.DIRECTORY
                or source["maximum_identity_families"] != 200
                or source["identity_workers"] != 4
                or source["archive_prefix"] != "official-monthly-facts/sbi-expanded/"
                or source["plan"] != "REGULAR"
                or source["in_scope"] is not True
                or source["period_count"] != 2
                or source["maximum_current_variants"] != 1024
                or source["preserved_projection_codes"] != [101763, 103173, 103174]
            ):
                raise ValueError("bounded reviewed SBI source differs")
            continue
        if source["adapter"] != "absl-full-book-v1" or source["amc"] != absl_expansion.AMC:
            raise ValueError("unreviewed whole-AMC adapter")
        public_url(source["catalogue_landing"], {"mutualfund.adityabirlacapital.com"})
        if (
            source["archive_prefix"] != "official-monthly-facts/" + source["key"] + "/"
            or source["plan"] != "REGULAR"
            or source["in_scope"] is not True
            or source["period_count"] != 2
            or source["maximum_current_variants"] != 1024
            or source["preserved_projection_codes"] != [101763, 103173, 103174]
        ):
            raise ValueError("bounded whole-AMC adapter scope differs")
        for original in source["identity_documents"]:
            public_url(original["url"], {"nsearchives.nseindia.com"})
    if version == 5:
        prior = registry(4, root)
        expected = deepcopy(prior["sources"])
        expected[-1]["adapter"] = "uti-english-monthly-book-v4"
        transition = document.get("authority_transition", {})
        if (document["sources"] != expected
                or transition.get("mode") != "source_method_revision"
                or transition.get("source_key") != uti_book.KEY
                or transition.get("previous_source_version") != 4
                or transition.get("previous_registry_hash") != fingerprint(prior)
                or transition.get("previous_specification_hash") != fingerprint(specification(uti_book.KEY, version=4))
                or transition.get("source_count_changed") is not False
                or transition.get("financial_field_rules_changed") is not True
                or transition.get("source_rows_changed") != 0
                or transition.get("supersedes_unaccepted_policy_hash") != "c7b58aaca5294958ed94db2d6cfd313ad4200fcdfb1502b293c63fcea33120c4"
                or transition.get("supersedes_unaccepted_source_configuration_hash") != "25bbe7c6349c849b168d14ec017c749550399d9185804f465a506dd1985e44e2"):
            raise ValueError("exact bounded source-method revision required; no cohort replacement")
    elif version > 1 and document["sources"][:-1] != registry(version - 1, root)["sources"]:
        raise ValueError("historical registered source changed")
    return document


def source(key, *, version=None):
    matches = [s for s in registry(version)["sources"] if s["key"] == key]
    if len(matches) != 1:
        raise ValueError("registered whole-AMC source key required")
    return matches[0]


def specification(key, *, version=None):
    item = deepcopy(source(key, version=version))
    parser = {
        "sbi-full-book-v1": sbi_book,
        "absl-full-book-v1": absl_expansion,
        "kotak-monthly-book-v1": kotak_book,
        "uti-english-monthly-book-v1": uti_book,
        "uti-english-monthly-book-v2": uti_book_v2,
        "uti-english-monthly-book-v3": uti_book_v3,
        "uti-english-monthly-book-v4": uti_book_v4,
    }[item["adapter"]]
    item["parser_policy"] = parser.policy()
    item["parser_policy_hash"] = fingerprint(item["parser_policy"])
    item["parser_engine_hash"] = hashlib.sha256(Path(parser.__file__).read_bytes()).hexdigest()
    if parser is sbi_book:
        from mfdataindia.enrichment import (
            sbi_acquisition,
            sbi_archive,
            sbi_batch,
            sbi_runtime,
            amc_book_pdf,
        )

        item["evidence_engine_hashes"] = {
            module.__name__.rsplit(".", 1)[-1]: hashlib.sha256(
                Path(module.__file__).read_bytes()
            ).hexdigest()
            for module in (sbi_acquisition, sbi_archive, sbi_batch, sbi_runtime)
        }
        item["decoder_engine_hash"] = hashlib.sha256(
            Path(amc_book_pdf.__file__).read_bytes()
            + Path(amc_book_pdf.__file__).with_name("_amc_book_pdf_worker.py").read_bytes()
        ).hexdigest()
    if parser is kotak_book:
        from mfdataindia.enrichment import (
            amfi_summary_identity,
            kotak_acquisition,
            kotak_batch,
            kotak_known_document,
            kotak_runtime,
            layout_book_archive,
            layout_book_http,
            layout_book_pdf,
        )

        item["reviewed_known_document_policy"] = kotak_known_document.policy()

        item["evidence_engine_hashes"] = {
            module.__name__.rsplit(".", 1)[-1]: hashlib.sha256(
                Path(module.__file__).read_bytes()
            ).hexdigest()
            for module in (
                amfi_summary_identity,
                kotak_acquisition,
                kotak_batch,
                kotak_known_document,
                kotak_runtime,
                layout_book_archive,
                layout_book_http,
            )
        }
        item["decoder_engine_hash"] = hashlib.sha256(
            Path(layout_book_pdf.__file__).read_bytes()
            + Path(layout_book_pdf.__file__).with_name("_layout_book_pdf_worker.py").read_bytes()
        ).hexdigest()
    if parser is uti_book:
        from mfdataindia.enrichment import (
            amfi_summary_identity,
            layout_book_archive,
            layout_book_http,
            uti_acquisition,
            uti_batch,
            uti_book_pdf,
            uti_runtime,
        )

        item["evidence_engine_hashes"] = {
            module.__name__.rsplit(".", 1)[-1]: hashlib.sha256(
                Path(module.__file__).read_bytes()
            ).hexdigest()
            for module in (
                amfi_summary_identity,
                layout_book_archive,
                layout_book_http,
                uti_acquisition,
                uti_batch,
                uti_runtime,
            )
        }
        item["decoder_engine_hash"] = uti_book_pdf.engine_hash()
    if parser is uti_book_v2:
        from mfdataindia.enrichment import (
            amfi_summary_identity, layout_book_archive, layout_book_http, monthly_facts,
            uti_acquisition, uti_batch_v2, uti_book_pdf_v2, uti_runtime_v2,
        )

        item["evidence_engine_hashes"] = {
            module.__name__.rsplit(".", 1)[-1]: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (amfi_summary_identity, layout_book_archive, layout_book_http, uti_acquisition, uti_batch_v2, uti_runtime_v2)
        }
        item["retained_field_engine_hashes"] = {
            module.__name__.rsplit(".", 1)[-1]: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (uti_book, monthly_facts, absl_expansion)
        }
        item["decoder_engine_hash"] = uti_book_pdf_v2.engine_hash()
    if parser is uti_book_v3:
        from mfdataindia.enrichment import (
            amfi_summary_identity, layout_book_archive, layout_book_http, monthly_facts,
            uti_acquisition, uti_batch_v3, uti_book_pdf_v2, uti_header_evidence, uti_runtime_v3,
        )

        item["evidence_engine_hashes"] = {
            module.__name__.rsplit(".", 1)[-1]: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (amfi_summary_identity, layout_book_archive, layout_book_http, uti_acquisition, uti_batch_v3, uti_runtime_v3, uti_header_evidence)
        }
        item["retained_field_engine_hashes"] = {
            module.__name__.rsplit(".", 1)[-1]: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (uti_book, uti_book_v2, monthly_facts, absl_expansion)
        }
        item["decoder_engine_hash"] = uti_book_pdf_v2.engine_hash()
    if parser is uti_book_v4:
        from mfdataindia.enrichment import (
            amfi_summary_identity, layout_book_archive, layout_book_http, monthly_facts,
            uti_acquisition, uti_aum_row, uti_batch_v4, uti_book_pdf_v3, uti_header_evidence, uti_runtime_v4,
        )

        item["evidence_engine_hashes"] = {
            module.__name__.rsplit(".", 1)[-1]: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (amfi_summary_identity, layout_book_archive, layout_book_http, uti_acquisition,
                           uti_batch_v4, uti_runtime_v4, uti_header_evidence, uti_aum_row)
        }
        item["retained_field_engine_hashes"] = {
            module.__name__.rsplit(".", 1)[-1]: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (uti_book, uti_book_v2, uti_book_v3, monthly_facts, absl_expansion)
        }
        item["decoder_engine_hash"] = uti_book_pdf_v3.engine_hash()
    return item


def configuration(version=None):
    document = registry(version)
    specifications = {
        s["key"]: specification(s["key"], version=document["version"]) for s in document["sources"]
    }
    return {
        "registry": document,
        "registry_hash": fingerprint(document),
        "source_specifications": specifications,
        # The owner seals this entire configuration. Readers must not need
        # EXECUTE on the private canonical_json helper to select current facts.
        "source_specification_hashes": {
            key: fingerprint(value) for key, value in specifications.items()
        },
    }


def current_policy(version=None):
    document = registry(version)
    result = deepcopy(multi_facts.current_policy())
    result.update(
        version=POLICY_VERSIONS[document["version"]],
        previous_policy_hash=fingerprint(
            multi_facts.current_policy()
            if document["version"] == 1
            else current_policy(document["version"] - 1)
        ),
        book_source_registry_hash=fingerprint(document),
        book_source_configuration_hash=fingerprint(configuration(document["version"])),
        scope="retained v3 assertion graph plus approved exact-identity whole-AMC book observations",
        publication_boundary="only complete receipted fund/period batches; frozen pilot excluded from replacement; unavailable fields never erase prior usable values",
    )
    if document["version"] >= 2:
        result["book_v2_same_date_selection"] = {
            "accepted_values": "canonical distinct accepted values only; one selects, multiple conflict, none unavailable",
            "incomplete_evidence": "retain accepted same-date value and every unavailable assertion as provenance; never invent a value",
            "legacy_book_v1": "unchanged historical selection and conflict behavior",
        }
    if document["version"] >= 3:
        result["source_registry_version"] = document["version"]
    if document["version"] == 3:
        result["supersedes_unaccepted_policy_hash"] = _registered_book_policy_v6.POLICY_HASH
    if document["version"] == 5:
        result["source_authority_transition"] = document["authority_transition"]
        result["supersedes_unaccepted_policy_hash"] = document["authority_transition"]["supersedes_unaccepted_policy_hash"]
    return result


def current_policies():
    documents = [current_policy(version) for version in sorted(REGISTRIES)]
    if 3 in REGISTRIES:
        documents.append(_registered_book_policy_v6.policy())
    return sorted(documents, key=lambda document: document["version"])
