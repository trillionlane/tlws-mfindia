"""Provider-free v2 transport adversaries; no DEV or PostgreSQL connection."""

from copy import deepcopy
import random

import pytest

import reader_source_packets as packets
import reader_source_reference as old
import reader_source_variants as variants

GIT = "a" * 40
IDENTITY = "b" * 32


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
    lines = packets.pair(value, "LEGACY_BASE44", identity=IDENTITY)
    assert packets.extract_pair(lines, "LEGACY_BASE44", expected_git=GIT) == value
    headers = [line for line in lines if '"header":' in line]
    assert len(headers) == 2
    assert all(len(line) < 61000 for line in lines)


def test_all_six_packets_are_required_for_complete_bundle():
    values = {"LEGACY_BASE44": legacy()}
    values.update({name: extended(name) for name in variants.PROFILES})
    lines = [line for name in packets.PROFILES
             for line in packets.pair(values[name], name, identity=IDENTITY)]
    assert packets.extract_bundle(lines, expected_git=GIT) == values
    for bad in (lines[:-1], [*lines, *packets.pair(legacy(), "LEGACY_BASE44",
                                                  identity=IDENTITY)],
                [*lines, old.MARKER + "{}"]):
        with pytest.raises((ValueError, KeyError, TypeError)):
            packets.extract_bundle(bad, expected_git=GIT)


@pytest.mark.parametrize("mutation", ["gap", "swap", "corrupt", "identity", "git", "marker"])
def test_incomplete_cross_bound_pair_is_rejected(mutation):
    lines = packets.pair(legacy(), "LEGACY_BASE44", identity=IDENTITY)
    altered = deepcopy(lines)
    if mutation == "gap":
        altered.pop(1)
    elif mutation == "swap":
        other = packets.pair(legacy(semantic="different"), "LEGACY_BASE44",
                             identity=IDENTITY)
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
            packets.extract_pair(altered, "LEGACY_BASE44", expected_git="f" * 40)
        return
    else:
        altered[0] = altered[0].replace(packets.MARKERS["LEGACY_BASE44", "A"],
                                        "SOURCE_REFERENCE_UNKNOWN_A_V2:", 1)
    with pytest.raises((ValueError, KeyError, TypeError)):
        packets.extract_pair(altered, "LEGACY_BASE44", expected_git=GIT)


def test_oversized_single_packet_still_rejects():
    with pytest.raises(ValueError):
        packets.pair(legacy(raw="x" * old.MAX_RAW), "LEGACY_BASE44", identity=IDENTITY)
