"""Provider-free v4 transport adversaries; no DEV or PostgreSQL connection."""

from copy import deepcopy
import base64
import gzip
import hashlib
import random

import pytest

import reader_source_packets as packets
import reader_source_reference as old
import reader_source_variants as variants
from scripts import ci_risk_policy as ci_policy

GIT = "a" * 40
IDENTITY = "b" * 32
BUNDLE = {"run_id": 123, "attempt": 1, "head": "d" * 40,
          "checkout": GIT, "ci_binding": "e" * 64}


def legacy(raw="raw", semantic="semantic"):
    return {"format": "source-calibration-not-live-acceptance-v1",
            "authority": {"source": "synthetic"}, "compiler_git": GIT,
            "compiler_sha256": "c" * 64, "raw": {"value": raw},
            "semantic_reference": {"value": semantic},
            "permission_acceptance": False, "runtime_authority": False}


def extended(profile):
    return {"format": "extended-source-calibration-NOT-live-acceptance-v1",
            "profile": profile, "compiler_git": GIT, "compiler_sources": {},
            "base_authority_sha256": old.AUTHORITY_SHA,
            "variant_authority_sha256": variants.AUTHORITY_SHA,
            "authority": {"source": "synthetic"},
            "raw": {"format": "extended-CI-source-reference-NOT-admission-v1",
                    "legacy": {"value": profile}, "acl_rows": [{"value": profile}],
                    "relation_candidate": [], "inheritance_rows": []},
            "raw_sha256": "d" * 64, "grant_state": {"value": profile},
            "portable_sha256": "e" * 64, "portable_projection_format": "synthetic",
            "permission_acceptance": False, "runtime_authority": False}


@pytest.fixture(autouse=True)
def transport_only(monkeypatch):
    # Native tests execute the *real* logical validators. These synthetic
    # adversaries isolate the framing protocol without a fake source graph.
    monkeypatch.setattr(packets, "_validate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(variants, "assert_source_only_transition", lambda *_args: None)


def test_combined_over_old_8mib_roundtrips_without_raising_either_packet_cap():
    rng = random.Random(81)
    value = legacy(rng.randbytes(2_200_000).hex(), rng.randbytes(2_200_000).hex())
    assert len(old.canonical(value).encode()) > old.MAX_RAW
    lines = packets.pair(value, "LEGACY_BASE44", identity=IDENTITY, bundle=BUNDLE)
    assert packets.extract_pair(lines, "LEGACY_BASE44", expected_git=GIT,
                                bundle=BUNDLE) == value
    headers = [line for line in lines if '"header":' in line]
    assert len(headers) == 2
    assert all(len(line) < 61000 for line in lines)


def test_highly_compressible_logical_document_over_16mib_is_still_lossless():
    value = legacy("x" * (9 * 1024 * 1024), "y" * (9 * 1024 * 1024))
    assert len(old.canonical(value).encode()) > 16 * 1024 * 1024
    lines = packets.pair(value, "LEGACY_BASE44", identity=IDENTITY, bundle=BUNDLE)
    assert packets.extract_pair(lines, "LEGACY_BASE44", expected_git=GIT,
                                bundle=BUNDLE) == value


def replace_first_outer_packet(lines, change):
    marker = packets.MARKERS["LEGACY_BASE44", "A"]
    part = [line for line in lines if marker in line]
    value = old.extract_envelope([line.replace(marker, old.MARKER, 1) for line in part])
    change(value)
    replacement = [line.replace(old.MARKER, marker, 1)
                   for line in old.reference_envelope(value, identity=IDENTITY)]
    return [*replacement, *[line for line in lines if marker not in line]]


def test_inner_content_hash_is_checked_after_outer_envelope_validation():
    lines = packets.pair(legacy(), "LEGACY_BASE44", identity=IDENTITY,
                         bundle=BUNDLE)
    changed = replace_first_outer_packet(
        lines, lambda packet: packet["body"].update(raw_sha256="0" * 64))
    with pytest.raises(ValueError):
        packets.extract_pair(changed, "LEGACY_BASE44", expected_git=GIT,
                             bundle=BUNDLE)


def test_noncanonical_inner_json_rejects_even_with_matching_inner_hashes():
    lines = packets.pair(legacy(), "LEGACY_BASE44", identity=IDENTITY,
                         bundle=BUNDLE)

    def change(packet):
        raw = b'{"value":"raw", "extra":0}'
        compressed = gzip.compress(raw, mtime=0)
        packet["body"].update(raw_bytes=len(raw),
                              raw_sha256=hashlib.sha256(raw).hexdigest(),
                              compressed_bytes=len(compressed),
                              compressed_sha256=hashlib.sha256(compressed).hexdigest(),
                              gzip_base64=base64.b64encode(compressed).decode())

    changed = replace_first_outer_packet(lines, change)
    with pytest.raises(ValueError):
        packets.extract_pair(changed, "LEGACY_BASE44", expected_git=GIT,
                             bundle=BUNDLE)


def test_nondeterministic_gzip_mtime_rejects_with_all_self_hashes_recomputed():
    lines = packets.pair(legacy(), "LEGACY_BASE44", identity=IDENTITY,
                         bundle=BUNDLE)

    def change(packet):
        raw = old.canonical(legacy()["raw"]).encode()
        compressed = gzip.compress(raw, mtime=1)
        packet["body"].update(raw_bytes=len(raw),
                              raw_sha256=hashlib.sha256(raw).hexdigest(),
                              compressed_bytes=len(compressed),
                              compressed_sha256=hashlib.sha256(compressed).hexdigest(),
                              gzip_base64=base64.b64encode(compressed).decode())

    changed = replace_first_outer_packet(lines, change)
    with pytest.raises(ValueError):
        packets.extract_pair(changed, "LEGACY_BASE44", expected_git=GIT,
                             bundle=BUNDLE)


def test_all_six_packets_are_required_for_complete_bundle():
    values = {"LEGACY_BASE44": legacy()}
    values.update({name: extended(name) for name in variants.PROFILES})
    lines = [line for name in packets.PROFILES
             for line in packets.pair(values[name], name, identity=IDENTITY,
                                      bundle=BUNDLE)]
    assert packets.extract_bundle(lines, expected_git=GIT, bundle=BUNDLE) == values
    for bad in (lines[:-1], [*lines, *packets.pair(legacy(), "LEGACY_BASE44",
                                                  identity=IDENTITY, bundle=BUNDLE)],
                [*lines, old.MARKER + "{}"]):
        with pytest.raises((ValueError, KeyError, TypeError)):
            packets.extract_bundle(bad, expected_git=GIT, bundle=BUNDLE)


@pytest.mark.parametrize("mutation", ["gap", "swap", "corrupt", "identity", "git", "marker"])
def test_incomplete_cross_bound_pair_is_rejected(mutation):
    lines = packets.pair(legacy(), "LEGACY_BASE44", identity=IDENTITY,
                         bundle=BUNDLE)
    altered = deepcopy(lines)
    if mutation == "gap":
        altered.pop(1)
    elif mutation == "swap":
        other = packets.pair(legacy(semantic="different"), "LEGACY_BASE44",
                             identity=IDENTITY, bundle=BUNDLE)
        marker = packets.MARKERS["LEGACY_BASE44", "B"]
        altered = [line for line in lines if marker not in line]
        altered.extend(line for line in other if marker in line)
    elif mutation == "corrupt":
        altered[1] = altered[1].replace('"value":"', '"value":"X', 1)
    elif mutation == "identity":
        altered = [line.replace(IDENTITY, "c" * 32) if
                   packets.MARKERS["LEGACY_BASE44", "B"] in line else line for line in altered]
    elif mutation == "git":
        with pytest.raises(ValueError):
            packets.extract_pair(altered, "LEGACY_BASE44", expected_git="f" * 40,
                                 bundle=BUNDLE)
        return
    else:
        altered[0] = altered[0].replace(packets.MARKERS["LEGACY_BASE44", "A"],
                                        "SOURCE_REFERENCE_UNKNOWN_A_V4:", 1)
    with pytest.raises((ValueError, KeyError, TypeError)):
        packets.extract_pair(altered, "LEGACY_BASE44", expected_git=GIT,
                             bundle=BUNDLE)


def test_oversized_single_packet_still_rejects():
    with pytest.raises(ValueError):
        packets.pair(legacy(raw="x" * (packets.MAX_PART + 1)),
                     "LEGACY_BASE44", identity=IDENTITY, bundle=BUNDLE)
    with pytest.raises(ValueError):
        old.reference_envelope({"value": "x" * old.MAX_RAW}, identity=IDENTITY)


def test_profile_40mib_preflight_rejects_highly_compressible_logical_data():
    with pytest.raises(ValueError):
        packets.pair(legacy(raw="x" * (21 * 1024 * 1024),
                            semantic="x" * (21 * 1024 * 1024)),
                     "LEGACY_BASE44", identity=IDENTITY, bundle=BUNDLE)


def test_cross_run_pairs_cannot_be_mixed_at_same_git_head():
    values = {"LEGACY_BASE44": legacy()}
    values.update({name: extended(name) for name in variants.PROFILES})
    other = {**BUNDLE, "run_id": BUNDLE["run_id"] + 1}
    lines = [line for name in packets.PROFILES
             for line in packets.pair(values[name], name, identity=IDENTITY,
                                      bundle=other if name == variants.PROFILES[1] else BUNDLE)]
    with pytest.raises(ValueError):
        packets.extract_bundle(lines, expected_git=GIT, bundle=BUNDLE)


def test_complete_profile_order_is_fixed():
    values = {"LEGACY_BASE44": legacy()}
    values.update({name: extended(name) for name in variants.PROFILES})
    groups = [packets.pair(values[name], name, identity=IDENTITY, bundle=BUNDLE)
              for name in packets.PROFILES]
    with pytest.raises(ValueError):
        packets.extract_bundle([*groups[1], *groups[0], *groups[2]],
                               expected_git=GIT, bundle=BUNDLE)


def test_normal_ci_bundle_requires_exact_trusted_run_context(monkeypatch):
    for key, value in {"GITHUB_ACTIONS": "true",
                       "GITHUB_REPOSITORY": "trillionlane/tlws-mfindia",
                       "GITHUB_EVENT_NAME": "pull_request",
                       "GITHUB_RUN_ID": "123",
                       "GITHUB_RUN_ATTEMPT": "1"}.items():
        monkeypatch.setenv(key, value)
    plan = {"checkout": GIT, "head": BUNDLE["head"], "mode": "full",
            "event": "pull_request"}
    monkeypatch.setattr(ci_policy, "environment_plan", lambda: plan)
    monkeypatch.setattr(ci_policy, "verify_plan", lambda value: value)
    monkeypatch.setattr(ci_policy, "digest", lambda value: BUNDLE["ci_binding"])
    assert packets.normal_ci_bundle(GIT) == BUNDLE
    with pytest.raises(ValueError):
        packets.normal_ci_bundle("f" * 40)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    with pytest.raises(ValueError):
        packets.normal_ci_bundle(GIT)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_REPOSITORY", "unrelated/repo")
    with pytest.raises(ValueError):
        packets.normal_ci_bundle(GIT)
