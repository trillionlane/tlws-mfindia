"""CI-only source evidence must bind the actual attempt, never mix attempts."""

import pytest

import reader_source_packets as packets
from ro02_historical_source import previous as ro02_previous
from test_reader_source_packets import legacy, extended, IDENTITY

GIT = "a" * 40


def context(monkeypatch, attempt="2"):
    plan = {"checkout": GIT, "head": GIT, "base": None, "paths": [],
            "mode": "full", "event": "push", "version": 1}
    for key, value in {
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "trillionlane/tlws-mfindia",
        "GITHUB_EVENT_NAME": "push", "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": attempt, "CI_PLAN_RESULT": "success",
        "CI_MODE": "full", "CI_BINDING": packets.ci_policy.digest(plan),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(packets.ci_policy, "environment_plan", lambda: plan)
    return plan


def test_current_bundle_records_actual_runner_attempt(monkeypatch):
    plan = context(monkeypatch)
    assert packets.normal_ci_bundle(GIT) == {
        "run_id": 123, "attempt": 2, "head": GIT, "checkout": GIT,
        "ci_binding": packets.ci_policy.digest(plan),
    }


@pytest.mark.parametrize("attempt", ["0", "-1", "01", "2.0", "true", " 2", "2 ", "٢", ""])
def test_runner_attempt_is_canonical_positive_ascii(monkeypatch, attempt):
    context(monkeypatch, attempt)
    with pytest.raises(ValueError):
        packets.normal_ci_bundle(GIT)


@pytest.mark.parametrize("field,value", [
    ("GITHUB_REPOSITORY", "other/repo"), ("GITHUB_EVENT_NAME", "workflow_dispatch"),
    ("CI_PLAN_RESULT", "failure"), ("CI_BINDING", "0" * 64), ("CI_MODE", "docs"),
])
def test_actual_attempt_does_not_replace_existing_ci_binding(monkeypatch, field, value):
    context(monkeypatch)
    monkeypatch.setenv(field, value)
    with pytest.raises(ValueError):
        packets.normal_ci_bundle(GIT)


def framing_only(monkeypatch):
    # Isolate framing here; original native cases retain all real logical proofs.
    monkeypatch.setattr(packets, "_validate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(packets.variants, "assert_source_only_transition", lambda *_args: None)


def bundle(attempt=2):
    return {"run_id": 123, "attempt": attempt, "head": GIT, "checkout": GIT,
            "ci_binding": "b" * 64}


def test_v5_pair_retains_complete_payload_and_exact_actual_attempt(monkeypatch):
    framing_only(monkeypatch)
    value = legacy()
    value["compiler_git"] = GIT
    lines = packets.pair(value, "LEGACY_BASE44", identity=IDENTITY, bundle=bundle())
    assert all("_V5:" in line for line in lines)
    assert packets.extract_pair(lines, "LEGACY_BASE44", expected_git=GIT,
                                bundle=bundle()) == value
    with pytest.raises(ValueError):
        packets.extract_pair(lines, "LEGACY_BASE44", expected_git=GIT, bundle=bundle(1))


def test_explicit_v4_keeps_original_attempt1_wire_semantics(monkeypatch):
    framing_only(monkeypatch)
    value = legacy()
    value["compiler_git"] = GIT
    lines = packets.historical_v4_pair(value, "LEGACY_BASE44", identity=IDENTITY,
                                       bundle=bundle(1))
    assert all("_V4:" in line for line in lines)
    assert packets.historical_v4_extract_pair(lines, "LEGACY_BASE44", expected_git=GIT,
                                               bundle=bundle(1)) == value
    with pytest.raises(ValueError):
        packets.historical_v4_pair(value, "LEGACY_BASE44", identity=IDENTITY,
                                  bundle=bundle(2))
    with pytest.raises(ValueError):
        packets.extract_pair(lines, "LEGACY_BASE44", expected_git=GIT,
                             bundle=bundle(1))  # No automatic legacy fallback.


@pytest.mark.parametrize("defect", ["attempt", "version", "run", "head", "binding", "identity"])
def test_complete_bundle_cannot_mix_versions_or_run_attempts(monkeypatch, defect):
    framing_only(monkeypatch)
    values = {"LEGACY_BASE44": legacy()}
    values.update({p: extended(p) for p in packets.variants.PROFILES})
    for value in values.values():
        value["compiler_git"] = GIT
    lines = []
    expected = bundle(1 if defect == "version" else 2)
    for index, profile in enumerate(packets.PROFILES):
        actual = dict(expected)
        version = 5
        identity = IDENTITY
        if index == 2:
            if defect == "version":
                version = 4
            elif defect == "identity":
                identity = "f" * 32
            else:
                key, replacement = {"attempt": ("attempt", 3), "run": ("run_id", 124),
                                    "head": ("head", "f" * 40),
                                    "binding": ("ci_binding", "f" * 64)}[defect]
                actual[key] = replacement
        producer = packets.pair if version == 5 else packets.historical_v4_pair
        lines.extend(producer(values[profile], profile, identity=identity, bundle=actual))
    with pytest.raises(ValueError):
        packets.extract_bundle(lines, expected_git=GIT, bundle=expected)


@pytest.mark.parametrize("attempt", [0, -1, True, "2", 2.0, 1000000])
def test_untrusted_packet_attempt_is_not_coerced(monkeypatch, attempt):
    framing_only(monkeypatch)
    with pytest.raises(ValueError):
        packets.pair(legacy(), "LEGACY_BASE44", identity=IDENTITY,
                     bundle=bundle(attempt))


def test_v4_wire_output_is_byte_exact_to_immutable_predecessor(monkeypatch):
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    path = "tests/reader_source_packets.py"
    raw = subprocess.check_output(["git", "show", "69037c0ec078ad67a8dfd708bc3cf86040b655e6:" + path], cwd=root)
    historical = {"__file__": str(root / path), "__name__": "immutable_ci_packets_v4"}
    exec(compile(raw, path, "exec"), historical)
    framing_only(monkeypatch)
    monkeypatch.setitem(historical, "_validate", lambda *_args, **_kwargs: None)
    values = {"LEGACY_BASE44": legacy()}
    values.update({p: extended(p) for p in packets.variants.PROFILES})
    for profile, value in values.items():
        previous = historical["pair"](value, profile, identity=IDENTITY, bundle=bundle(1))
        current = packets.historical_v4_pair(value, profile, identity=IDENTITY, bundle=bundle(1))
        assert current == previous
        assert packets.historical_v4_extract_pair(current, profile, expected_git=GIT,
                                                  bundle=bundle(1)) == value


def test_complete_current_bundle_is_fresh_and_deterministic_per_actual_attempt(monkeypatch):
    framing_only(monkeypatch)
    values = {"LEGACY_BASE44": legacy()}
    values.update({p: extended(p) for p in packets.variants.PROFILES})
    assert packets.bundle_identity(bundle(2)) != packets.bundle_identity(bundle(3))
    for attempt in (1, 2, 3):
        expected = bundle(attempt)
        identity = packets.bundle_identity(expected)
        lines = [line for profile in packets.PROFILES
                 for line in packets.pair(values[profile], profile, identity=identity,
                                          bundle=expected)]
        assert packets.extract_bundle(lines, expected_git=GIT, bundle=expected) == values


def test_intentional_parent_byte_changes_are_finite_and_tamper_checked():
    import hashlib
    from ci_source_attempt_historical import ROOT, BASE, authority, restore_ci_source_parent
    import subprocess
    rows = authority()
    assert len(rows) == 5
    for name, row in rows.items():
        current = ro02_previous(ROOT / name, (ROOT / name).read_bytes())
        assert hashlib.sha256(current).hexdigest() == row["current_sha256"]
        previous = subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)
        assert restore_ci_source_parent(ROOT / name, current) == previous
        with pytest.raises(AssertionError):
            restore_ci_source_parent(ROOT / name, current + b"\n# unreviewed")
