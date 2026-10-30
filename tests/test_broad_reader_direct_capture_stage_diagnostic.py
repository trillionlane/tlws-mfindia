"""Provider-free, value-free diagnostics for one future direct capture."""

import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import stat
import subprocess

import pytest

from mfdataindia.review import broad_reader_direct_delivery_spec as spec


ROOT = Path(__file__).resolve().parents[1]
PARENT = "ae752f711f422da6ac7fad8df847e9eda7de2372"
WORKER = "scripts/collect_broad_reader_direct_dev.py"
PRESERVATION = "tests/test_broad_reader_direct_delivery_preservation.py"
THIS_TEST = "tests/test_broad_reader_direct_capture_stage_diagnostic.py"
ADDED_PRESERVATION_LINE = (
    b'    "tests/test_broad_reader_direct_capture_stage_diagnostic.py",\n'
)
WORKER_SPEC = spec_from_file_location(
    "_stage_direct_capture_worker", ROOT / "scripts" / "collect_broad_reader_direct_dev.py")
assert WORKER_SPEC is not None and WORKER_SPEC.loader is not None
worker = module_from_spec(WORKER_SPEC)
WORKER_SPEC.loader.exec_module(worker)
FIXTURE_SPEC = spec_from_file_location(
    "_stage_direct_capture_existing_fixture",
    ROOT / "tests" / "test_broad_reader_direct_capture_worker.py")
assert FIXTURE_SPEC is not None and FIXTURE_SPEC.loader is not None
fixture = module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(fixture)


def test_exact_pr132_successor_delta_and_original_guard_bytes():
    changed = subprocess.check_output(
        ["git", "diff", "--name-status", "--no-renames", PARENT], cwd=ROOT
    ).decode().splitlines()
    assert set(changed) == {
        "M\t" + WORKER, "M\t" + PRESERVATION, "A\t" + THIS_TEST,
    }
    worker_path = ROOT / WORKER
    guard_path = ROOT / PRESERVATION
    new_test_path = ROOT / THIS_TEST
    for path in (worker_path, guard_path, new_test_path):
        assert path.is_file() and not path.is_symlink()
        assert stat.S_ISREG(path.stat().st_mode)
        assert not path.stat().st_mode & 0o111
    assert hashlib.sha256(worker_path.read_bytes()).hexdigest() == (
        "3383bb431a077c0ef19eff57ded435d4950adbdf7a46b1662ddbce9fc3e9d350"
    )
    current_guard = guard_path.read_bytes()
    assert current_guard.count(ADDED_PRESERVATION_LINE) == 1
    old_guard = current_guard.replace(ADDED_PRESERVATION_LINE, b"", 1)
    assert hashlib.sha256(old_guard).hexdigest() == (
        "8e364a74954053086f36f5403745e7f5083a32a3e9ff11e9a1c5a6cce67f25b0"
    )
    assert old_guard == subprocess.check_output(
        ["git", "show", PARENT + ":" + PRESERVATION], cwd=ROOT
    )


def fail(error):
    raise error


@pytest.mark.parametrize("stage", sorted(worker._STAGES))
def test_static_stage_preserves_exact_original_error_instance(stage):
    error = spec.DeliverySpecError("STATIC_ORIGINAL_REASON_SENTINEL_SECRET_SQL_PAYLOAD")
    with pytest.raises(spec.DeliverySpecError) as captured:
        worker._spec_stage(stage, lambda: fail(error))
    assert captured.value is error
    assert str(error) == "STATIC_ORIGINAL_REASON_SENTINEL_SECRET_SQL_PAYLOAD"
    assert worker._safe_error_label(error) == "DeliverySpecError:" + stage
    worker._spec_stage("seal", lambda: None)
    with pytest.raises(spec.DeliverySpecError):
        worker._spec_stage("seal", lambda: fail(error))
    assert worker._safe_error_label(error) == "DeliverySpecError:seal"


def test_unknown_or_forged_stage_cannot_reach_output():
    calls = []
    with pytest.raises(ValueError, match="UNAPPROVED_DIRECT_CAPTURE_DIAGNOSTIC_STAGE"):
        worker._spec_stage("SECRET_RUNTIME_VALUE", lambda: calls.append("called"))
    assert calls == []
    forged = spec.DeliverySpecError("SECRET_RUNTIME_VALUE")
    forged._safe_direct_capture_stage = (object(), "dsn")
    assert worker._safe_error_label(forged) == "DeliverySpecError"
    forged._safe_direct_capture_stage = (worker._STAGE_TAG, "SECRET_RUNTIME_VALUE")
    assert worker._safe_error_label(forged) == "DeliverySpecError"


@pytest.mark.parametrize("stage", sorted(worker._STAGES))
def test_failure_channel_exact_one_ascii_line_no_leak(monkeypatch, capsys, stage):
    error = spec.DeliverySpecError("SENTINEL_SECRET_SQL_PAYLOAD_EVIDENCE")
    with pytest.raises(spec.DeliverySpecError):
        worker._spec_stage(stage, lambda: fail(error))
    monkeypatch.setattr(worker, "main", lambda: fail(error))
    with pytest.raises(SystemExit) as stopped:
        worker._main_guarded()
    assert stopped.value.code == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == ("direct read capture failed:DeliverySpecError:" + stage
                       + ";STOP_NO_RETRY\n")
    assert out.err.isascii()
    for forbidden in ("SENTINEL", "SECRET", "SQL", "PAYLOAD", "EVIDENCE",
                      "Traceback", "repr", "\n\n"):
        assert forbidden not in out.out + out.err


@pytest.mark.parametrize("stage", ["startup", "package", "environment", "payload",
                                   "window", "dsn", "connect", "capture", "seal"])
def test_each_real_boundary_is_labeled_once_and_keeps_connection_order(monkeypatch, stage):
    env, _ = fixture._environment()
    worker._consumed = stage == "startup"
    calls = []

    class FakeConnection:
        def close(self):
            calls.append("close")

    def connect(**_):
        calls.append("connect")
        if stage == "connect":
            raise spec.DeliverySpecError("STATIC_CONNECT_SENTINEL")
        return FakeConnection()

    monkeypatch.setattr(worker.psycopg, "connect", connect)
    if stage == "package":
        monkeypatch.setattr(worker.controller, "package_hashes",
                            lambda *_: fail(spec.DeliverySpecError("STATIC_PACKAGE_SENTINEL")))
    elif stage == "environment":
        monkeypatch.setattr(worker.spec, "worker_environment",
                            lambda *_, **__: fail(spec.DeliverySpecError("STATIC_ENV_SENTINEL")))
    elif stage == "payload":
        monkeypatch.setattr(worker.spec, "validate_capture_payload",
                            lambda *_, **__: fail(spec.DeliverySpecError("STATIC_PAYLOAD_SENTINEL")))
    elif stage == "window":
        monkeypatch.setattr(worker.policy, "_live",
                            lambda *_, **__: fail(spec.DeliverySpecError("STATIC_WINDOW_SENTINEL")))
    elif stage == "dsn":
        monkeypatch.setattr(worker.spec, "connection_arguments",
                            lambda *_, **__: fail(spec.DeliverySpecError("STATIC_DSN_SENTINEL")))
    elif stage == "capture":
        monkeypatch.setattr(worker.direct, "capture_before",
                            lambda *_, **__: fail(spec.DeliverySpecError("STATIC_CAPTURE_SENTINEL")))
    else:
        monkeypatch.setattr(worker.direct, "capture_before",
                            lambda *_, **__: {"grant_authorized": stage == "seal"})

    with pytest.raises(spec.DeliverySpecError) as captured:
        worker.run_worker(env)
    assert worker._safe_error_label(captured.value) == "DeliverySpecError:" + stage
    assert calls.count("connect") == (0 if stage in {
        "startup", "package", "environment", "payload", "window", "dsn"} else 1)
    assert calls.count("close") == (1 if stage in {"capture", "seal"} else 0)


def test_first_existing_failure_wins_and_later_boundaries_are_not_called(monkeypatch):
    env, _ = fixture._environment()
    worker._consumed = False
    calls = []
    monkeypatch.setattr(worker.controller, "package_hashes",
                        lambda *_: fail(spec.DeliverySpecError("FIRST_PACKAGE_FAILURE")))
    monkeypatch.setattr(worker.spec, "worker_environment",
                        lambda *_, **__: calls.append("environment"))
    monkeypatch.setattr(worker.psycopg, "connect",
                        lambda **_: calls.append("connect"))
    with pytest.raises(spec.DeliverySpecError) as captured:
        worker.run_worker(env)
    assert str(captured.value) == "FIRST_PACKAGE_FAILURE"
    assert worker._safe_error_label(captured.value) == "DeliverySpecError:package"
    assert calls == []
