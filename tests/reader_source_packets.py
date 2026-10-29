"""Fixed lossless CI-only source-reference pairs. Never runtime evidence.

The original v1 envelope is still the per-packet transport and retains every
8 MiB / 64-chunk check. This module adds an exact two-part logical document;
neither part, nor a partial set of profiles, is admissible by itself.
"""

from copy import deepcopy
import json
from pathlib import Path
import re

import reader_source_reference as old
import reader_source_variants as variants
import reader_source_variants_v2 as variants_v2

PROFILES = ("LEGACY_BASE44", *variants.PROFILES)
PARTS = ("A", "B")
MARKERS = {(profile, part): f"SOURCE_REFERENCE_{profile}_{part}_V2:"
           for profile in PROFILES for part in PARTS}
MAX_PACKETS = 6
MAX_RAW_TOTAL = MAX_PACKETS * old.MAX_RAW
MAX_CHUNKS_TOTAL = MAX_PACKETS * old.MAX_CHUNKS
MAX_ENCODED_CHARS = MAX_CHUNKS_TOTAL * old.CHUNK
FORMAT = "source-reference-fixed-packet-v2"


def require(condition):
    old.require(condition)


def sha(value):
    return old.digest(old.canonical(value).encode())


def _compiler_pin(payload, profile):
    return (payload["compiler_sha256"] if profile == "LEGACY_BASE44"
            else sha(payload["compiler_sources"]))


def _split(payload, profile):
    require(type(payload) is dict and profile in PROFILES)
    if profile == "LEGACY_BASE44":
        require(set(payload) == {"format", "authority", "compiler_git", "compiler_sha256",
                                 "raw", "semantic_reference", "permission_acceptance",
                                 "runtime_authority"})
        require(payload["format"] == "source-calibration-not-live-acceptance-v1")
        return deepcopy(payload["raw"]), {
            "semantic_reference": deepcopy(payload["semantic_reference"]),
            "metadata": {k: deepcopy(v) for k, v in payload.items()
                         if k not in ("raw", "semantic_reference")},
        }
    require(set(payload) == set("format profile compiler_git compiler_sources "
                                "base_authority_sha256 variant_authority_sha256 authority "
                                "raw raw_sha256 grant_state portable_sha256 "
                                "portable_projection_format runtime_authority "
                                "permission_acceptance".split()))
    require(payload["format"] == "extended-source-calibration-NOT-live-acceptance-v1"
            and payload["profile"] == profile)
    require(type(payload["raw"]) is dict and set(payload["raw"]) ==
            {"format", "legacy", "acl_rows", "relation_candidate", "inheritance_rows"})
    return deepcopy(payload["raw"]["legacy"]), {
        "extended": {k: deepcopy(payload["raw"][k]) for k in
                     ("format", "acl_rows", "relation_candidate", "inheritance_rows")},
        "metadata": {k: deepcopy(v) for k, v in payload.items() if k != "raw"},
    }


def _join(first, second, profile):
    require(type(first) is dict and type(second) is dict and type(second.get("metadata")) is dict)
    result = deepcopy(second["metadata"])
    if profile == "LEGACY_BASE44":
        require(set(second) == {"semantic_reference", "metadata"})
        result["raw"] = deepcopy(first)
        result["semantic_reference"] = deepcopy(second["semantic_reference"])
    else:
        require(set(second) == {"extended", "metadata"})
        require(type(second["extended"]) is dict and set(second["extended"]) ==
                {"format", "acl_rows", "relation_candidate", "inheritance_rows"})
        result["raw"] = {"legacy": deepcopy(first), **deepcopy(second["extended"])}
    left, right = _split(result, profile)
    require(left == first and right == second)
    return result


def _validate(payload, profile, *, expected_git):
    require(type(expected_git) is str and re.fullmatch(r"[0-9a-f]{40}", expected_git))
    require(payload["compiler_git"] == expected_git
            and payload["runtime_authority"] is payload["permission_acceptance"] is False)
    if profile == "LEGACY_BASE44":
        root = Path(__file__).resolve().parents[1]
        require(payload["authority"] == old.authority(root))
        require(payload["compiler_sha256"] == old.digest(
            (root / "tests/reader_source_reference.py").read_bytes()))
        require(payload["raw"]["runtime_authority"] is
                payload["raw"]["permission_acceptance"] is False)
        require(old.semantic_reference(payload["raw"]) == payload["semantic_reference"])
    else:
        # The existing extended validator checks every ACL, candidate,
        # inheritance, grant-state, source/projection and authority binding.
        variants_v2.validate_payload(payload, profile, expected_git=expected_git)


def pair(payload, profile, *, identity):
    require(profile in PROFILES and type(identity) is str
            and re.fullmatch(r"[0-9a-f]{32}", identity))
    first, second = _split(payload, profile)
    require(len(old.canonical(first).encode()) + len(old.canonical(second).encode()) <=
            2 * old.MAX_RAW)
    common = {"format": FORMAT, "profile": profile, "identity": identity,
              "git": payload["compiler_git"], "authority_sha256": old.AUTHORITY_SHA,
              "compiler_pin_sha256": _compiler_pin(payload, profile)}
    packets = {
        "A": {**common, "part": "A", "body": first, "peer_sha256": sha(second)},
        "B": {**common, "part": "B", "body": second, "peer_sha256": sha(first)},
    }
    lines = []
    for part in PARTS:
        marker = MARKERS[profile, part]
        lines.extend(line.replace(old.MARKER, marker, 1)
                     for line in old.reference_envelope(packets[part], identity=identity))
    return lines


def extract_pair(lines, profile, *, expected_git):
    require(profile in PROFILES)
    selected = {}
    for part in PARTS:
        marker = MARKERS[profile, part]
        source = [line for line in lines if marker in line]
        exact = [line.replace(marker, old.MARKER, 1) for line in source]
        selected[part] = old.extract_envelope(exact)
        header = json.loads(source[0].split(marker, 1)[1], object_pairs_hook=old.unique)
        require(set(header) == {"header"}
                and header["header"]["identity"] == selected[part]["identity"])
    a, b = selected["A"], selected["B"]
    for part, packet in selected.items():
        require(type(packet) is dict and set(packet) ==
                {"format", "profile", "identity", "git", "authority_sha256",
                 "compiler_pin_sha256", "part", "body", "peer_sha256"})
        require(packet["format"] == FORMAT and packet["profile"] == profile
                and packet["part"] == part and packet["git"] == expected_git
                and packet["authority_sha256"] == old.AUTHORITY_SHA)
        require(type(packet["identity"]) is str
                and re.fullmatch(r"[0-9a-f]{32}", packet["identity"]))
    require(a["identity"] == b["identity"] and a["peer_sha256"] == sha(b["body"])
            and b["peer_sha256"] == sha(a["body"])
            and a["compiler_pin_sha256"] == b["compiler_pin_sha256"])
    payload = _join(a["body"], b["body"], profile)
    require(a["compiler_pin_sha256"] == _compiler_pin(payload, profile))
    _validate(payload, profile, expected_git=expected_git)
    return payload


def extract_bundle(lines, *, expected_git):
    """Require the complete three-profile, six-packet set from one run."""
    require(type(lines) is list)
    markers = tuple(MARKERS.values())
    selected = [line for line in lines if "SOURCE_REFERENCE_" in line
                or "SOURCE_SEMANTIC_REFERENCE_" in line]
    require(all(sum(marker in line for marker in markers) == 1 for line in selected))
    require(len(selected) <= MAX_PACKETS * (old.MAX_CHUNKS + 2))
    chunk_count = encoded_chars = 0
    for line in selected:
        marker = next(marker for marker in markers if marker in line)
        record = json.loads(line.split(marker, 1)[1], object_pairs_hook=old.unique)
        if set(record) == {"chunk"}:
            chunk_count += 1
            encoded_chars += record["chunk"]["chars"]
    require(chunk_count <= MAX_CHUNKS_TOTAL and encoded_chars <= MAX_ENCODED_CHARS)
    result = {profile: extract_pair(selected, profile, expected_git=expected_git)
              for profile in PROFILES}
    variants.assert_source_only_transition(result[variants.PROFILES[0]],
                                           result[variants.PROFILES[1]])
    return result
