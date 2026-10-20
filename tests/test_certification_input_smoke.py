"""Provider-free harness mechanics; NOT actual Linux/image startup evidence."""

import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from mfdataindia.review import certification_input_transport as transport


spec = importlib.util.spec_from_file_location(
    "input_smoke_fixture", Path(__file__).resolve().parents[1]
    / "scripts/smoke_certification_input_transport.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


@pytest.fixture(autouse=True)
def short_synthetic_executable(monkeypatch):
    # Faked exec only: the macOS checkout path is longer than either CI image
    # executable and must not accidentally consume the fixed argv allowance.
    monkeypatch.setattr(smoke.sys, "executable", "/synthetic/python")


@pytest.mark.parametrize("prefix,budget", [
    ("RESERVE", transport.STARTUP_RESERVE_BYTES),
    ("MARGIN", transport.STARTUP_MARGIN_BYTES),
])
def test_padding_is_exact_including_names_equals_and_nuls_and_never_mutates(prefix, budget):
    before = {"ORIGINAL": "unchanged"}
    result = smoke.startup_padding(before, prefix, budget)
    assert before == {"ORIGINAL": "unchanged"} and result["ORIGINAL"] == "unchanged"
    added = {k: v for k, v in result.items() if k not in before}
    assert len(added) == 4
    assert sum(len(k.encode()) + len(v.encode()) + 2 for k, v in added.items()) == budget
    assert all(len(v.encode()) <= transport.MAX_VALUE_BYTES for v in added.values())


def test_padding_rejects_collision_or_unapproved_budget():
    with pytest.raises(smoke.NativeSmokeFailure, match="padding_entry"):
        smoke.startup_padding({"SYNTHETIC_STARTUP_RESERVE_0": "kept"}, "RESERVE", 131072)
    with pytest.raises(smoke.NativeSmokeFailure, match="padding_budget"):
        smoke.startup_padding({}, "RESERVE", 262144)


@pytest.mark.parametrize("reported_libc_limit", [131072, 4194304])
def test_full_profiles_require_real_probe_before_decoder_not_libc_limit_inference(
    monkeypatch, capsys, reported_libc_limit
):
    # All subprocesses below are fakes. Only real final-image CI can certify exec.
    monkeypatch.setattr(smoke.sys, "platform", "linux")
    monkeypatch.setattr(smoke.os, "environ", {})
    real_sysconf = smoke.os.sysconf
    monkeypatch.setattr(smoke.os, "sysconf", lambda name:
                        reported_libc_limit if name == "SC_ARG_MAX" else real_sysconf(name))
    calls = []

    def run(argv, *, env, capture_output, timeout):
        assert capture_output and 0 < timeout <= 30
        assert len(argv[2].encode()) == 131000
        assert transport.RAW not in env
        assert all(len(v.encode()) <= transport.MAX_VALUE_BYTES for v in env.values())
        header = json.loads(env[transport.HEADER])
        # Every encoded chunk remains intact through both native submissions.
        assert len([k for k in env if k.startswith(transport.PREFIX)]) == header["count"]
        logical = transport.decode_environment(
            {k: v for k, v in env.items() if not k.startswith("SYNTHETIC_STARTUP_")},
            **smoke.CONTEXT)
        assert len(logical[transport.RAW].encode()) == int(env["SYNTHETIC_RAW_BYTES"])
        calls.append((argv, dict(env), timeout))
        if len(calls) % 2:
            assert "native-startup-headroom-v1" in argv[2]
            assert "SYNTHETIC_STARTUP_MARGIN_0" in env
            return SimpleNamespace(returncode=0, stdout=smoke.HEADROOM_RECEIPT + b"\n")
        assert "SYNTHETIC_STARTUP_MARGIN_0" not in env
        assert "SYNTHETIC_STARTUP_RESERVE_0" in env
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "native_exec": "PASS", "chunks": header["count"],
            "raw_bytes": int(env["SYNTHETIC_RAW_BYTES"])}).encode())

    monkeypatch.setattr(smoke.subprocess, "run", run)
    smoke.main()
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert output[0]["native_startup_metadata"]["SC_ARG_MAX"] == reported_libc_limit
    profiles = output[1]["synthetic_native_input_exec"]
    assert len(calls) == 4 and [p["chunks"] for p in profiles] == [60, 64]
    for p in profiles:
        assert p["headroom"]["physical_string_bytes"] == p["budget"]["startup_required_bytes"]
        assert p["headroom"]["reserve_bytes"] == p["headroom"]["margin_bytes"] == 131072
        assert p["headroom"]["profile_deadline_seconds"] == 30
    assert output[1]["live_coverage"] is output[1]["pg_http_certification"] is False


@pytest.mark.parametrize("defect", ["E2BIG", "timeout", "nonzero", "bad_receipt"])
def test_rejected_headroom_never_runs_decoder_or_leaks_child_output(monkeypatch, defect):
    calls = []

    def run(*args, **kwargs):
        calls.append(True)
        if defect == "E2BIG":
            raise OSError(7, "private-argv-must-not-be-exported")
        if defect == "timeout":
            raise subprocess.TimeoutExpired("private-command", 1)
        return SimpleNamespace(returncode=1 if defect == "nonzero" else 0,
                               stdout=b"private-output-must-not-be-exported")

    monkeypatch.setattr(smoke.subprocess, "run", run)
    with pytest.raises(smoke.NativeSmokeFailure) as error:
        smoke.native_startup({"INPUT": "unchanged"}, "pass\n#")
    assert len(calls) == 1
    assert "private" not in str(error.value)


def test_probe_and_decoder_share_original_thirty_second_deadline(monkeypatch):
    clock = iter((0, 1, 30.001))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(clock))
    calls = []
    monkeypatch.setattr(smoke.subprocess, "run", lambda *a, **k:
        calls.append(True) or SimpleNamespace(returncode=0, stdout=smoke.HEADROOM_RECEIPT))
    with pytest.raises(smoke.NativeSmokeFailure, match="profile_deadline"):
        smoke.native_startup({}, "pass\n#")
    assert len(calls) == 1


@pytest.mark.parametrize("defect", ["nonzero", "invalid_json"])
def test_decoder_failure_is_not_headroom_or_native_acceptance(monkeypatch, defect):
    calls = []

    def run(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout=smoke.HEADROOM_RECEIPT)
        return SimpleNamespace(returncode=1 if defect == "nonzero" else 0,
                               stdout=b"not-a-receipt")

    monkeypatch.setattr(smoke.subprocess, "run", run)
    with pytest.raises(smoke.NativeSmokeFailure, match="decoder_"):
        smoke.native_startup({}, "pass\n#")
    assert len(calls) == 2


def test_original_plain_value_argv_chunk_request_bounds_and_no_resource_setters():
    assert (transport.MAX_ENV_BYTES, transport.MAX_VALUE_BYTES, transport.MAX_ARGUMENT_BYTES,
            transport.MAX_REQUEST_BYTES, transport.MAX_CHUNKS) == (
                1572864, 32768, 131072, 2097152, 64)
    source = Path(smoke.__file__).read_text()
    assert "setrlimit" not in source and "ulimit" not in source
    with pytest.raises(smoke.NativeSmokeFailure, match="submission_budget"):
        smoke.native_startup({"OVERSIZE": "x" * 32769}, "pass\n#")
