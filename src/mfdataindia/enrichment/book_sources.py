"""Versioned whole-AMC source authority; frozen FS07 registry stays untouched."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from mfdataindia.enrichment import absl_expansion, multi_facts
from mfdataindia.enrichment.evidence import public_url
from mfdataindia.review.policy import fingerprint

CURRENT_VERSION = 1
REGISTRIES = {
    1: (
        "official-book-sources-v1.json",
        "5dec71683d2b62fd02880375e489b8a0dcecaeca3d0c85f361568a3098feadba",
    )
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
        if source["adapter"] != "absl-full-book-v1" or source["amc"] != absl_expansion.AMC:
            raise ValueError("unreviewed whole-AMC adapter")
        public_url(source["catalogue_landing"], {"mutualfund.adityabirlacapital.com"})
        if (
            source["archive_prefix"] != "official-monthly-facts/" + source["key"] + "/"
            or source["plan"] != "REGULAR"
            or source["in_scope"] is not True
            or source["period_count"] != 2
            or source["maximum_current_variants"] != 1024
            or source["preserved_projection_codes"] != [103173, 103174]
        ):
            raise ValueError("bounded whole-AMC adapter scope differs")
        for original in source["identity_documents"]:
            public_url(original["url"], {"nsearchives.nseindia.com"})
    return document


def source(key):
    matches = [s for s in registry()["sources"] if s["key"] == key]
    if len(matches) != 1:
        raise ValueError("registered whole-AMC source key required")
    return matches[0]


def specification(key):
    item = deepcopy(source(key))
    item["parser_policy"] = absl_expansion.policy()
    item["parser_policy_hash"] = fingerprint(item["parser_policy"])
    item["parser_engine_hash"] = hashlib.sha256(
        Path(absl_expansion.__file__).read_bytes()
    ).hexdigest()
    return item


def configuration():
    document = registry()
    return {
        "registry": document,
        "registry_hash": fingerprint(document),
        "source_specifications": {s["key"]: specification(s["key"]) for s in document["sources"]},
    }


def current_policy(version=None):
    document = registry(version)
    result = deepcopy(multi_facts.current_policy())
    result.update(
        version=3 + document["version"],
        book_source_registry_hash=fingerprint(document),
        scope="retained v3 assertion graph plus approved exact-identity whole-AMC book observations",
        publication_boundary="only complete receipted fund/period batches; frozen pilot excluded from replacement; unavailable fields never erase prior usable values",
    )
    return result


def current_policies():
    return [current_policy(version) for version in sorted(REGISTRIES)]
