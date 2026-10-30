"""Provider-free capture-worker ordering and single-use regression tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import pytest

from mfdataindia.review import broad_reader_direct_delivery_controller as controller
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_source_authority as source
_SCRIPT_SPEC = spec_from_file_location(
    "_phasec_collect_broad_reader_direct_dev",
    Path(__file__).resolve().parents[1] / "scripts" / "collect_broad_reader_direct_dev.py")
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
DSN = ("host=/cloudsql/" + spec.CONNECTION + " dbname=" + spec.DATABASE
       + " user=" + spec.OWNER_ROLE + " password=synthetic-only")


def _environment():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {
        "format": "mf-broad-reader-direct-capture-v1",
        "packaged_source": source.verify_packaged_source(ROOT),
        "git_sha": GIT,
        "image_binding": {"app": deepcopy(APP), "tools": deepcopy(TOOLS)},
        "create_only_intent_sha256": "d" * 64,
        "window": {"starts_at": (now - timedelta(seconds=5)).isoformat(),
                   "ends_at": (now + timedelta(minutes=5)).isoformat(),
                   "exclusive_administration": True,
                   "data_schema_custody": True,
                   "future_owner_provisioning": True},
    }
    root = str(spec.ARTIFACTS / (spec.ROOT_PREFIXES["capture"] + "abcdef"))
    payload["create_only_intent_sha256"] = spec.intent_preimage(
        "capture", root, payload)[1]
    prepared = controller.prepare_capture(
        payload=payload, root=root, final_git_sha=GIT, image=APP, source_root=ROOT)
    env = {row["name"]: row["value"] for row in prepared["task"]["containers"][0]["env"]
           if "value" in row}
    env.update({"MFDATAINDIA_DSN": DSN,
                "CLOUD_RUN_JOB": spec.ROLES["capture"].job,
                "CLOUD_RUN_EXECUTION": spec.ROLES["capture"].job + "-abcde",
                "CLOUD_RUN_TASK_INDEX": "0", "CLOUD_RUN_TASK_ATTEMPT": "0",
                "CLOUD_RUN_TASK_COUNT": "1"})
    return env, payload


def test_read_only_worker_attests_before_single_owner_connection(monkeypatch):
    env, payload = _environment()
    worker._consumed = False
    calls = []

    class FakeConnection:
        def close(self):
            calls.append("close")

    def connect(**kwargs):
        assert kwargs["user"] == spec.OWNER_ROLE
        assert kwargs["password"] == "synthetic-only"
        calls.append("connect")
        return FakeConnection()

    def capture(conn, *, packaged_source, source_root, window):
        assert isinstance(conn, FakeConnection)
        assert packaged_source == payload["packaged_source"]
        assert source_root == ROOT
        assert window == payload["window"]
        calls.append("capture")
        return {"format": "synthetic-read-only-capture",
                "grant_authorized": False, "permission_enabled": False}

    monkeypatch.setattr(worker.psycopg, "connect", connect)
    monkeypatch.setattr(worker.direct, "capture_before", capture)
    result = json.loads(worker.run_worker(env))
    assert result["stage"] == "direct-capture-readonly"
    assert result["evidence"]["permission_enabled"] is False
    assert calls == ["connect", "capture", "close"]
    with pytest.raises(spec.DeliverySpecError, match="CONSUMED_DIRECT_CAPTURE_WORKER"):
        worker.run_worker(env)


@pytest.mark.parametrize("mutation", (
    lambda env: env.update(BROAD_DIRECT_PAYLOAD_0="corrupt"),
    lambda env: env.update(BROAD_DIRECT_SOURCE_SHA256="0" * 64),
    lambda env: env.update(CLOUD_RUN_JOB=spec.ROLES["grant"].job),
    lambda env: env.update(MFDATAINDIA_DSN=DSN.replace(
        "user=" + spec.OWNER_ROLE, "user=" + spec.READER_ROLE)),
))
def test_worker_rejects_transport_or_role_drift_without_connection(monkeypatch, mutation):
    env, _ = _environment()
    worker._consumed = False
    mutation(env)
    monkeypatch.setattr(worker.psycopg, "connect",
                        lambda **_: pytest.fail("must not connect"))
    with pytest.raises((spec.DeliverySpecError,
                        source.SourceAuthorityError)):
        worker.run_worker(env)


def test_worker_failure_closes_connection_and_never_retries(monkeypatch):
    env, _ = _environment()
    worker._consumed = False
    calls = []

    class FakeConnection:
        def close(self):
            calls.append("close")

    monkeypatch.setattr(worker.psycopg, "connect",
                        lambda **_: (calls.append("connect"), FakeConnection())[1])

    def fail(*_, **__):
        calls.append("capture")
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(worker.direct, "capture_before", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        worker.run_worker(env)
    assert calls == ["connect", "capture", "close"]
