"""Provider-free role separation and one-shot reader-check transport tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import pytest

from mfdataindia.review import broad_reader_direct_delivery_controller as controller
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_direct_grant as direct
from mfdataindia.review import broad_reader_source_authority as source
_SCRIPT_SPEC = spec_from_file_location(
    "_phasec_check_broad_reader_direct_dev",
    Path(__file__).resolve().parents[1] / "scripts" / "check_broad_reader_direct_dev.py")
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
worker = module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(worker)


ROOT = Path(__file__).resolve().parents[1]
GIT = "a" * 40
APP = {"image": spec.IMAGE_PREFIX + "b" * 64,
       "resolved_image": spec.IMAGE_PREFIX + "b" * 64,
       "config_digest": "sha256:" + "c" * 64,
       "git_sha": GIT, "platform": "linux/amd64", "layers_downloaded": 0}
TOOLS = {**APP, "image": APP["image"].replace(
    "/mfdataindia@", "/mfdataindia-review-tools@"),
    "resolved_image": APP["resolved_image"].replace(
        "/mfdataindia@", "/mfdataindia-review-tools@")}
READER_DSN = ("host=/cloudsql/" + spec.CONNECTION + " dbname=" + spec.DATABASE
              + " user=" + spec.READER_ROLE + " password=synthetic-only")


def _environment(monkeypatch, *, no_freeze=False):
    # The real current-policy validator is tested by the existing native suite;
    # this transport test supplies only a synthetic state, never a live claim.
    monkeypatch.setattr(spec.broad_policy, "require_current_policy",
                        lambda state: state["synthetic"] is True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {
        "format": "mf-broad-reader-direct-reader-v1",
        "planned_after": {"synthetic": True},
        "grant_receipt_sha256": "d" * 64,
        "packaged_source": source.verify_packaged_source(ROOT),
        "git_sha": GIT,
        "image_binding": {"app": deepcopy(APP), "tools": deepcopy(TOOLS)},
        "create_only_intent_sha256": "e" * 64,
        "window": {"starts_at": (now - timedelta(seconds=5)).isoformat(),
                   "ends_at": (now + timedelta(minutes=5)).isoformat(),
                   "exclusive_administration": True,
                   "data_schema_custody": True,
                   "future_owner_provisioning": True},
    }
    if no_freeze:
        payload["window"].update(exclusive_administration=False,
                                 data_schema_custody=False,
                                 future_owner_provisioning=False,
                                 dev_no_freeze_risk_accepted=True)
        payload["grant_custody"] = direct.custody_marker(payload["window"])
    root = str(spec.ARTIFACTS / (spec.ROOT_PREFIXES["reader"] + "abcdef"))
    payload["create_only_intent_sha256"] = spec.intent_preimage(
        "reader", root, payload)[1]
    prepared = controller.prepare_reader(
        payload=payload, root=root, final_git_sha=GIT, image=APP, source_root=ROOT)
    env = {row["name"]: row["value"] for row in prepared["task"]["containers"][0]["env"]
           if "value" in row}
    env.update({"MFDATAINDIA_DSN": READER_DSN,
                "CLOUD_RUN_JOB": spec.ROLES["reader"].job,
                "CLOUD_RUN_EXECUTION": spec.ROLES["reader"].job + "-abcde",
                "CLOUD_RUN_TASK_INDEX": "0", "CLOUD_RUN_TASK_ATTEMPT": "0",
                "CLOUD_RUN_TASK_COUNT": "1"})
    return env, payload, prepared


def test_no_freeze_reader_worker_result_and_payload_marker(monkeypatch):
    env, payload, _ = _environment(monkeypatch, no_freeze=True)
    marker = direct.custody_marker(payload["window"])
    worker._consumed = False
    calls = []

    class FakeConnection:
        def close(self):
            calls.append("close")

    def read(_conn, *, planned_after):
        assert planned_after == payload["planned_after"]
        calls.append("read")
        return {"format": "synthetic-actual-reader-check",
                "representative_select_completed": True,
                "permission_enabled": False, "ti_verified": False}

    monkeypatch.setattr(worker.psycopg, "connect",
                        lambda **_: (calls.append("connect"), FakeConnection())[1])
    monkeypatch.setattr(worker.reader, "run", read)
    certificate = json.loads(worker.run_worker(env))
    assert spec.verify_certificate(
        certificate, payload_sha256=spec.broad_policy.fingerprint(payload),
        stage="direct-authenticated-reader")["reader_result"]["custody"] == marker
    assert calls == ["connect", "read", "close"]
    for variant in ("missing", "forged"):
        bad = deepcopy(payload)
        if variant == "missing":
            bad.pop("grant_custody")
        else:
            bad["grant_custody"] = {**marker, "mode": "CUSTODY_V1"}
        with pytest.raises(spec.DeliverySpecError):
            spec.validate_reader_payload(bad, final_git_sha=GIT, source_root=ROOT)


def test_reader_worker_uses_only_reader_secret_and_one_connection(monkeypatch):
    env, payload, prepared = _environment(monkeypatch)
    worker._consumed = False
    calls = []

    class FakeConnection:
        def close(self):
            calls.append("close")

    def connect(**kwargs):
        assert kwargs["user"] == spec.READER_ROLE
        assert kwargs["password"] == "synthetic-only"
        calls.append("connect")
        return FakeConnection()

    def read(conn, *, planned_after):
        assert isinstance(conn, FakeConnection)
        assert planned_after == payload["planned_after"]
        calls.append("read")
        return {"format": "synthetic-actual-reader-check",
                "representative_select_completed": True,
                "permission_enabled": False, "ti_verified": False}

    monkeypatch.setattr(worker.psycopg, "connect", connect)
    monkeypatch.setattr(worker.reader, "run", read)
    result = json.loads(worker.run_worker(env))
    assert result["stage"] == "direct-authenticated-reader"
    assert result["evidence"]["grant_receipt_sha256"] == "d" * 64
    assert calls == ["connect", "read", "close"]
    assert prepared["task"]["containers"][0]["env"][-1] == {
        "name": "MFDATAINDIA_DSN", "valueFrom": {
            "secretKeyRef": {"name": spec.ROLES["reader"].secret, "key": "1"}}}
    with pytest.raises(spec.DeliverySpecError, match="CONSUMED_DIRECT_READER_WORKER"):
        worker.run_worker(env)


def test_reader_worker_rejects_owner_dsn_before_any_connection(monkeypatch):
    env, _, _ = _environment(monkeypatch)
    worker._consumed = False
    env["MFDATAINDIA_DSN"] = READER_DSN.replace(
        "user=" + spec.READER_ROLE, "user=" + spec.OWNER_ROLE)
    monkeypatch.setattr(worker.psycopg, "connect",
                        lambda **_: pytest.fail("must not connect"))
    with pytest.raises(spec.DeliverySpecError):
        worker.run_worker(env)


def test_reader_failure_closes_without_retry(monkeypatch):
    env, _, _ = _environment(monkeypatch)
    worker._consumed = False
    calls = []

    class FakeConnection:
        def close(self):
            calls.append("close")

    monkeypatch.setattr(worker.psycopg, "connect",
                        lambda **_: (calls.append("connect"), FakeConnection())[1])

    def fail(*_args, **_kwargs):
        calls.append("read")
        raise RuntimeError("synthetic reader failure")

    monkeypatch.setattr(worker.reader, "run", fail)
    with pytest.raises(RuntimeError, match="synthetic reader failure"):
        worker.run_worker(env)
    assert calls == ["connect", "read", "close"]
