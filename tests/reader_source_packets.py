"""Fixed lossless CI-only source-reference pairs. Never runtime evidence.

The original v1 envelope is still the per-packet transport and retains every
8 MiB / 64-chunk check. This module adds an exact two-part logical document;
neither part, nor a partial set of profiles, is admissible by itself.
"""

from copy import deepcopy
import base64
import gzip
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import re
import zlib

import reader_source_reference as old
import reader_source_variants as variants
import reader_source_variants_v2 as variants_v2

_policy_path = Path(__file__).resolve().parents[1] / "scripts/ci_risk_policy.py"
_policy_spec = spec_from_file_location("ci_risk_policy", _policy_path)
ci_policy = module_from_spec(_policy_spec)
_policy_spec.loader.exec_module(ci_policy)

PROFILES = ("LEGACY_BASE44", *variants.PROFILES)
PARTS = ("A", "B")
MARKERS = {(profile, part): f"SOURCE_REFERENCE_{profile}_{part}_V5:"
           for profile in PROFILES for part in PARTS}
_HISTORICAL_V4_MARKERS = {(profile, part): f"SOURCE_REFERENCE_{profile}_{part}_V4:"
                         for profile in PROFILES for part in PARTS}
MAX_PACKETS = 6
MAX_RAW_TOTAL = MAX_PACKETS * old.MAX_RAW
MAX_CHUNKS_TOTAL = MAX_PACKETS * old.MAX_CHUNKS
MAX_ENCODED_CHARS = MAX_CHUNKS_TOTAL * old.CHUNK
MAX_PART = 32 * 1024 * 1024
MAX_PROFILE = 40 * 1024 * 1024
MAX_LOGICAL_TOTAL = 128 * 1024 * 1024
FORMAT = "source-reference-compressed-fixed-packet-v5"
BODY_FORMAT = "bounded-gzip-canonical-json-v1"


def require(condition):
    old.require(condition)


def sha(value):
    return old.digest(old.canonical(value).encode())


def _wire(version):
    require(type(version) is int and version in (4, 5))
    return (("source-reference-compressed-fixed-packet-v4", _HISTORICAL_V4_MARKERS)
            if version == 4 else (FORMAT, MARKERS))


def _require_bundle(bundle, *, expected_git, version):
    _wire(version)
    require(type(bundle) is dict and set(bundle) ==
            {"run_id", "attempt", "head", "checkout", "ci_binding"})
    require(type(bundle["run_id"]) is int and bundle["run_id"] > 0)
    require(type(bundle["attempt"]) is int and
            (bundle["attempt"] == 1 if version == 4 else 1 <= bundle["attempt"] <= 999999))
    require(type(bundle["head"]) is str
            and re.fullmatch(r"[0-9a-f]{40}", bundle["head"]))
    require(type(bundle["checkout"]) is str and bundle["checkout"] == expected_git
            and re.fullmatch(r"[0-9a-f]{40}", bundle["checkout"]))
    require(type(bundle["ci_binding"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", bundle["ci_binding"]))


def require_bundle(bundle, *, expected_git):
    """Current v5: caller supplies the independently observed exact CI bundle."""
    _require_bundle(bundle, expected_git=expected_git, version=5)


def bundle_identity(bundle):
    """One deterministic CI-only identity for six freshly generated packets."""
    require(type(bundle) is dict)
    require_bundle(bundle, expected_git=bundle.get("checkout"))
    return sha(bundle)[:32]


def normal_ci_bundle(checkout):
    """Bind PR head and executed checkout independently via the CI plan."""
    require(os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "trillionlane/tlws-mfindia"
            and os.environ.get("GITHUB_EVENT_NAME") in ("pull_request", "push"))
    run = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    require(re.fullmatch(r"[1-9][0-9]{0,19}", run) is not None
            and re.fullmatch(r"[1-9][0-9]{0,5}", attempt) is not None)
    plan = ci_policy.verify_plan(ci_policy.environment_plan())
    require(plan["checkout"] == checkout and plan["mode"] == "full"
            and plan["event"] == os.environ["GITHUB_EVENT_NAME"])
    bundle = {"run_id": int(run), "attempt": int(attempt), "head": plan["head"],
              "checkout": plan["checkout"], "ci_binding": ci_policy.digest(plan)}
    require_bundle(bundle, expected_git=checkout)
    return bundle


def _encode_body(value):
    raw = old.canonical(value).encode()
    require(0 < len(raw) <= MAX_PART)
    compressed = gzip.compress(raw, mtime=0)
    return {"format": BODY_FORMAT, "raw_bytes": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "compressed_bytes": len(compressed),
            "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
            "gzip_base64": base64.b64encode(compressed).decode("ascii")}


def _decode_body(value):
    require(type(value) is dict and set(value) == {"format", "raw_bytes", "raw_sha256",
                                                  "compressed_bytes", "compressed_sha256",
                                                  "gzip_base64"})
    require(value["format"] == BODY_FORMAT and type(value["raw_bytes"]) is int
            and 0 < value["raw_bytes"] <= MAX_PART
            and type(value["compressed_bytes"]) is int
            and 0 < value["compressed_bytes"] <= old.MAX_RAW)
    require(type(value["gzip_base64"]) is str)
    compressed = base64.b64decode(value["gzip_base64"], validate=True)
    require(len(compressed) == value["compressed_bytes"]
            and hashlib.sha256(compressed).hexdigest() == value["compressed_sha256"])
    decoder = zlib.decompressobj(wbits=31)
    raw = decoder.decompress(compressed, MAX_PART + 1)
    require(len(raw) == value["raw_bytes"] and decoder.eof
            and not decoder.unused_data and not decoder.unconsumed_tail
            and hashlib.sha256(raw).hexdigest() == value["raw_sha256"])
    decoded = json.loads(raw, object_pairs_hook=old.unique)
    require(old.canonical(decoded).encode() == raw)
    require(gzip.compress(raw, mtime=0) == compressed
            and base64.b64encode(compressed).decode("ascii") == value["gzip_base64"])
    return decoded


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


def _pair(payload, profile, *, identity, bundle, version):
    require(profile in PROFILES and type(identity) is str
            and re.fullmatch(r"[0-9a-f]{32}", identity))
    wire_format, markers = _wire(version)
    _require_bundle(bundle, expected_git=payload["compiler_git"], version=version)
    first, second = _split(payload, profile)
    require(len(old.canonical(first).encode()) + len(old.canonical(second).encode()) <=
            MAX_PROFILE)
    common = {"format": wire_format, "profile": profile, "identity": identity,
              "git": payload["compiler_git"], "authority_sha256": old.AUTHORITY_SHA,
              "compiler_pin_sha256": _compiler_pin(payload, profile),
              "bundle": deepcopy(bundle)}
    packets = {
        "A": {**common, "part": "A", "body": _encode_body(first),
              "peer_sha256": sha(second)},
        "B": {**common, "part": "B", "body": _encode_body(second),
              "peer_sha256": sha(first)},
    }
    lines = []
    for part in PARTS:
        marker = markers[profile, part]
        lines.extend(line.replace(old.MARKER, marker, 1)
                     for line in old.reference_envelope(packets[part], identity=identity))
    return lines


def _extract_pair(lines, profile, *, expected_git, bundle, version):
    require(profile in PROFILES)
    wire_format, markers = _wire(version)
    _require_bundle(bundle, expected_git=expected_git, version=version)
    selected = {}
    for part in PARTS:
        marker = markers[profile, part]
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
                 "compiler_pin_sha256", "part", "body", "peer_sha256", "bundle"})
        require(packet["format"] == wire_format and packet["profile"] == profile
                and packet["part"] == part and packet["git"] == expected_git
                and packet["authority_sha256"] == old.AUTHORITY_SHA
                and packet["bundle"] == bundle)
        require(type(packet["identity"]) is str
                and re.fullmatch(r"[0-9a-f]{32}", packet["identity"]))
    first, second = _decode_body(a["body"]), _decode_body(b["body"])
    require(len(old.canonical(first).encode()) + len(old.canonical(second).encode()) <=
            MAX_PROFILE)
    require(a["identity"] == b["identity"] and a["peer_sha256"] == sha(second)
            and b["peer_sha256"] == sha(first)
            and a["compiler_pin_sha256"] == b["compiler_pin_sha256"])
    payload = _join(first, second, profile)
    require(a["compiler_pin_sha256"] == _compiler_pin(payload, profile))
    _validate(payload, profile, expected_git=expected_git)
    return payload


def _extract_bundle(lines, *, expected_git, bundle, version):
    """Require the complete three-profile, six-packet set from one run."""
    require(type(lines) is list)
    _require_bundle(bundle, expected_git=expected_git, version=version)
    markers = tuple(_wire(version)[1].values())
    selected = [line for line in lines if "SOURCE_REFERENCE_" in line
                or "SOURCE_SEMANTIC_REFERENCE_" in line]
    require(all(sum(marker in line for marker in markers) == 1 for line in selected))
    require(len(selected) <= MAX_PACKETS * (old.MAX_CHUNKS + 2))
    order = [next(index for index, marker in enumerate(markers) if marker in line)
             for line in selected]
    require(order == sorted(order) and set(order) == set(range(MAX_PACKETS)))
    chunk_count = encoded_chars = 0
    identities = set()
    for line in selected:
        marker = next(marker for marker in markers if marker in line)
        record = json.loads(line.split(marker, 1)[1], object_pairs_hook=old.unique)
        if set(record) == {"header"}:
            require(type(record["header"]) is dict
                    and type(record["header"].get("identity")) is str
                    and re.fullmatch(r"[0-9a-f]{32}", record["header"]["identity"]))
            identities.add(record["header"]["identity"])
        if set(record) == {"chunk"}:
            chunk_count += 1
            encoded_chars += record["chunk"]["chars"]
    require(chunk_count <= MAX_CHUNKS_TOTAL and encoded_chars <= MAX_ENCODED_CHARS)
    if version == 5:
        require(len(identities) == 1)
    result = {profile: _extract_pair(selected, profile, expected_git=expected_git,
                                    bundle=bundle, version=version)
              for profile in PROFILES}
    require(sum(len(old.canonical(body).encode())
                for profile, payload in result.items()
                for body in _split(payload, profile)) <= MAX_LOGICAL_TOTAL)
    variants.assert_source_only_transition(result[variants.PROFILES[0]],
                                           result[variants.PROFILES[1]])
    return result


def pair(payload, profile, *, identity, bundle):
    """Current producer emits v5 only; never negotiate an older schema."""
    return _pair(payload, profile, identity=identity, bundle=bundle, version=5)


def extract_pair(lines, profile, *, expected_git, bundle):
    return _extract_pair(lines, profile, expected_git=expected_git, bundle=bundle, version=5)


def extract_bundle(lines, *, expected_git, bundle):
    return _extract_bundle(lines, expected_git=expected_git, bundle=bundle, version=5)


def historical_v4_pair(payload, profile, *, identity, bundle):
    """Explicit historical wire compatibility, still strictly attempt1."""
    return _pair(payload, profile, identity=identity, bundle=bundle, version=4)


def historical_v4_extract_pair(lines, profile, *, expected_git, bundle):
    return _extract_pair(lines, profile, expected_git=expected_git, bundle=bundle, version=4)


def historical_v4_extract_bundle(lines, *, expected_git, bundle):
    return _extract_bundle(lines, expected_git=expected_git, bundle=bundle, version=4)
