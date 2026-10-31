"""Dormant patched-PDF contract; never authorizes historical source execution.

Historical configuration remains readable. No registry has been allocated for
this contract and there is deliberately no environment/caller override.
"""

from importlib.metadata import version

from mfdataindia.review.policy import fingerprint

PYPDF_VERSION = "6.21.4"
WHEEL_SHA256 = "33e4fb41ca352810eb4e9838aa0d82d6888a5f8bf682f43e99cce5faa666255c"
SDIST_SHA256 = "c8817e3f888d47a087872ded69812c1ef30cacb871d6e053e0c00bbaf0bc2163"


def dependency_contract():
    return {"format": "patched-pdf-dependency-v3", "distribution": "pypdf",
            "version": PYPDF_VERSION, "wheel_sha256": WHEEL_SHA256,
            "sdist_sha256": SDIST_SHA256,
            "advisories": ["GHSA-5hx5-vpf9-5wcv", "GHSA-6c7f-9265-g8fw",
                           "GHSA-jr96-gvgh-j3r4", "GHSA-gmh7-3g9r-mjh5",
                           "GHSA-62m5-6wh8-28x7", "GHSA-42gh-23hr-vphw", "GHSA-x3rh-gjgm-fmqc", "GHSA-4vqv-7265-gm2w", "GHSA-5849-834g-hvw5", "GHSA-mfr5-77xh-9j7h", "GHSA-cggf-277g-3q54"],
            "missing_contents": "reject; explicit zero-byte stream accepted"}


def require_runtime():
    if version("pypdf") != PYPDF_VERSION:
        raise ValueError("exact reviewed patched pypdf runtime required")


def reject_unallocated_source_authority():
    """Fail BEFORE DB/provider/write at operational source boundaries.

    Even a caller-supplied compatible-looking dictionary cannot allocate a
    source policy. A future reviewed registry transition must replace this deny.
    Pure decoder mechanics and read-only historical policy imports are separate.
    """
    raise ValueError("PDF source authority unallocated for patched runtime")


def admit_extraction(extraction, *, dependency, engine_hash, policy_hash):
    """Four independent evidence checks, not source activation permission."""
    require_runtime()
    if dependency != dependency_contract():
        raise ValueError("patched PDF dependency contract differs")
    if extraction.get("extractor_version") != PYPDF_VERSION:
        raise ValueError("exact PDF extractor version differs")
    if extraction.get("engine_hash") != engine_hash:
        raise ValueError("PDF decoder engine differs")
    if extraction.get("policy_hash") != policy_hash:
        raise ValueError("PDF decoder policy differs")
    body = {k: v for k, v in extraction.items() if k != "extraction_hash"}
    if extraction.get("extraction_hash") != fingerprint(body):
        raise ValueError("PDF extraction self-hash differs")
    return extraction
