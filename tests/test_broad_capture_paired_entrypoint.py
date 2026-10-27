"""Actual capture entrypoint, not grant-worker coverage; no provider/database IO."""

from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import runpy

import pytest

from broad_reader_fixtures_v2 import capture_approval
from test_broad_reader_transport_v2 import CaptureConnection
from mfdataindia.review.evidence_transport import decode_evidence
from mfdataindia.review.policy import canonical, fingerprint
from mfdataindia.review import reader_access_transport_spec as strict
from mfdataindia.review.rollout import verify_evidence

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_DSN = (
    "host=/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev "
    "dbname=mfdataindia user=mfdata_admin password=SYNTHETIC_PRIVATE_DSN"
)


def entrypoint(version="_v3"):
    core = importlib.import_module("mfdataindia.review.broad_reader_capture_controller" + version)
    w = runpy.run_path(str(ROOT / ("scripts/collect_broad_reader_capture_dev" + version + ".py")))
    doc = capture_approval().model_dump(mode="json")
    doc["manifest"].update(rollout_id=core.IDENTITY, actor="rollout-certification:" + core.IDENTITY)
    doc["controller_sha256"] = hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest()
    doc["root"] = str(
        core.ARTIFACTS
        / (
            "mf-reader-broad-capture-20261027-aBc123"
            if version
            else "mf-reader-broad-capture-20261026-aBc123"
        )
    )
    a = core.Approval.model_validate(doc)
    env = core.inputs(a)["environment"] | dict(
        CLOUD_RUN_JOB=core.NAME,
        CLOUD_RUN_EXECUTION=core.NAME + "-abcde",
        CLOUD_RUN_TASK_INDEX="0",
        CLOUD_RUN_TASK_ATTEMPT="0",
        CLOUD_RUN_TASK_COUNT="1",
        MFDATAINDIA_DSN=SYNTHETIC_DSN,
    )
    return w, core, a, env


def exercise_entrypoint(monkeypatch, version="_v3"):
    """Also executed against exact immutable old code before successor creation."""
    w, core, a, env = entrypoint(version)
    conn = CaptureConnection()
    calls = []
    conn.close = lambda: calls.append("close")

    def connect(**kw):
        calls.append("connect")
        assert kw == w["connection_arguments"](SYNTHETIC_DSN)
        assert kw["connect_timeout"] == 5 and kw["autocommit"] is True
        return conn

    monkeypatch.setattr(w["psycopg"], "connect", connect)
    text = w["run_worker"](env)
    cert = decode_evidence(json.loads(text))
    value = cert["evidence"]
    assert verify_evidence(a.manifest, core.STAGE, cert) == value
    core.permission.validate_result(value)
    assert value["input_binding"] == dict(
        root=a.root,
        approval_sha256=fingerprint(a.model_dump(mode="json")),
        request_sha256=hashlib.sha256(core.inputs(a)["body"]).hexdigest(),
        controller_git_sha=a.controller_git_sha,
        controller_sha256=a.controller_sha256,
    )
    assert value["worker_binding"]["packaged_program_sha256"] == core.WORKER_SHA
    assert value["worker_binding"]["packaged_spec_sha256"] == core.SPEC_SHA
    assert value["worker_binding"]["packaged_controller_sha256"] == a.controller_sha256
    assert value["state"] == conn.data["state"] and value["candidate"] == conn.data["candidate"]
    assert all(value[k] is False for k in core.permission.DENIALS)
    inv = core.permission.frozen.inventory()
    definitions = {q["id"]: q["sql"] for q in inv["queries"]}
    assert conn.calls == [
        *inv["controls"],
        *(definitions[n] for n in inv["sequence"]),
        inv["terminal"],
    ]
    assert calls == ["connect", "close"]
    assert "SYNTHETIC_PRIVATE_DSN" not in text
    with pytest.raises(ValueError):
        w["run_worker"](env)
    assert calls == ["connect", "close"]


def test_real_capture_worker_complete_roundtrip_no_parser_or_capture_mock(monkeypatch):
    exercise_entrypoint(monkeypatch)


class NoSecret(dict):
    def __getitem__(self, key):
        assert key != "MFDATAINDIA_DSN", "invalid input reached secret lookup"
        return super().__getitem__(key)


@pytest.mark.parametrize(
    "defect",
    [
        "malformed",
        "duplicate",
        "nested_duplicate",
        "nan",
        "infinity",
        "array",
        "null",
        "unknown",
        "coerced",
        "missing",
        "wrong_root",
        "old_identity",
    ],
)
def test_real_parser_rejects_before_secret_and_connection(monkeypatch, defect):
    w, core, a, env = entrypoint()
    doc = a.model_dump(mode="json")
    raw = canonical(doc)
    if defect == "malformed":
        raw = raw[:-1]
    elif defect == "duplicate":
        raw = '{"root":"ignored",' + raw[1:]
    elif defect == "nested_duplicate":
        raw = raw.replace('"manifest":{', '"manifest":{"cohort":[],')
    elif defect == "nan":
        raw = raw.replace('"cohort":[154117]', '"cohort":[NaN]')
    elif defect == "infinity":
        raw = raw.replace('"cohort":[154117]', '"cohort":[Infinity]')
    elif defect == "array":
        raw = "[]"
    elif defect == "null":
        raw = "null"
    elif defect == "unknown":
        raw = '{"unknown":true,' + raw[1:]
    elif defect == "coerced":
        doc["manifest"]["allow_canary_events"] = 1
        raw = canonical(doc)
    elif defect == "missing":
        env.pop("BROAD_READER_CAPTURE_APPROVAL")
    elif defect == "wrong_root":
        doc["root"] = "/tmp/not-approved"
        raw = canonical(doc)
    else:
        doc["manifest"].update(
            rollout_id="dev-bread-cap-261026r", actor="rollout-certification:dev-bread-cap-261026r"
        )
        raw = canonical(doc)
    if defect != "missing":
        env["BROAD_READER_CAPTURE_APPROVAL"] = raw
    monkeypatch.setattr(w["psycopg"], "connect", lambda **_: pytest.fail("unexpected connect"))
    with pytest.raises((ValueError, KeyError)):
        w["run_worker"](NoSecret(env))
    with pytest.raises(ValueError):
        w["run_worker"](NoSecret(env))


@pytest.mark.parametrize(
    "key",
    [
        "DEPLOYMENT_GIT_SHA",
        "CLOUD_RUN_JOB",
        "CLOUD_RUN_EXECUTION",
        "CLOUD_RUN_TASK_INDEX",
        "CLOUD_RUN_TASK_ATTEMPT",
        "CLOUD_RUN_TASK_COUNT",
        "BROAD_READER_CAPTURE_PROGRAM_SHA256",
        "BROAD_READER_CAPTURE_SPEC_SHA256",
        "BROAD_READER_CAPTURE_INVENTORY_SHA256",
        "BROAD_READER_CAPTURE_CONTROLLER_SHA256",
    ],
)
def test_actual_packaged_and_platform_bindings_before_secret(monkeypatch, key):
    w, _, _, env = entrypoint()
    env[key] = "unexpected"
    monkeypatch.setattr(w["psycopg"], "connect", lambda **_: pytest.fail("unexpected connect"))
    with pytest.raises(ValueError):
        w["run_worker"](NoSecret(env))


@pytest.mark.parametrize("failure", ["connect", "capture", "rollback", "close"])
def test_actual_capture_transport_failure_is_consumed_never_reconnected(monkeypatch, failure):
    w, core, _, env = entrypoint()
    calls = []
    conn = CaptureConnection()
    original = conn.execute
    inv = core.permission.frozen

    def execute(sql, **kw):
        if (failure == "capture" and sql == inv.CONTEXT_SQL) or (
            failure == "rollback" and sql == inv.TERMINAL
        ):
            conn.calls.append(sql)
            raise RuntimeError("SYNTHETIC_PRIVATE_DRIVER_ERROR")
        return original(sql, **kw)

    def close():
        calls.append("close")
        if failure == "close":
            raise RuntimeError("SYNTHETIC_PRIVATE_CLOSE_ERROR")

    def connect(**_):
        calls.append("connect")
        if failure == "connect":
            raise RuntimeError("SYNTHETIC_PRIVATE_CONNECT_ERROR")
        return conn

    conn.execute, conn.close = execute, close
    monkeypatch.setattr(w["psycopg"], "connect", connect)
    with pytest.raises(RuntimeError) as error:
        w["run_worker"](env)
    if failure == "rollback":
        assert type(error.value).__name__ == "AbortUnconfirmed"
        assert conn.calls.count(inv.TERMINAL) == 1
    elif failure == "capture":
        assert conn.calls[-1] == inv.TERMINAL and conn.calls.count(inv.TERMINAL) == 1
    with pytest.raises(ValueError):
        w["run_worker"](env)
    assert calls == (["connect"] if failure == "connect" else ["connect", "close"])


def test_exact_strict_parser_reexport_and_old_consumed_version_untouched():
    from mfdataindia.review import broad_reader_capture_spec_v3 as spec
    from mfdataindia.review import broad_reader_capture_spec as old

    assert spec.parse is strict.parse and not hasattr(old, "parse")
    assert spec.IDENTITY != old.IDENTITY and spec.JOB != old.JOB
    assert spec.SOURCE_HASHES == old.SOURCE_HASHES
    assert spec.frozen is old.frozen
    doc = {"nested": {"value": "normal"}}
    assert spec.parse(canonical(doc)) == deepcopy(doc)
