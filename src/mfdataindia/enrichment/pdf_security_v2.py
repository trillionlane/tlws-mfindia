"""Dormant patched-PDF contract; never authorizes historical source execution.

Historical configuration remains readable. No registry has been allocated for
this contract and there is deliberately no environment/caller override.
"""

from importlib.metadata import version

from mfdataindia.review.policy import fingerprint

PYPDF_VERSION = "6.21.2"
WHEEL_SHA256 = "1d44d1a3a200f25062adff0085b02040e4646431ac2ccac22e42679e91466084"
SDIST_SHA256 = "0a3e1c5c886a1be03646c16a04827cc166c2a0575c971b26a4bf89686551c2d4"


def dependency_contract():
    return {"format": "patched-pdf-dependency-v2", "distribution": "pypdf",
            "version": PYPDF_VERSION, "wheel_sha256": WHEEL_SHA256,
            "sdist_sha256": SDIST_SHA256,
            "advisories": ["GHSA-5hx5-vpf9-5wcv", "GHSA-6c7f-9265-g8fw",
                           "GHSA-jr96-gvgh-j3r4", "GHSA-gmh7-3g9r-mjh5"],
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
