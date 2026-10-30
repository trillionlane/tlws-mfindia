"""Exercise executable entrypoints with the actual process environment mapping."""

from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from mfdataindia.review import broad_reader_direct_delivery_controller as controller
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review.policy import fingerprint


ROOT = Path(__file__).resolve().parents[1]
KINDS = ("capture", "grant", "reader")
FIXTURES = {}
for _kind in KINDS:
    _spec = spec_from_file_location(
        "_runtime_environment_fixture_" + _kind,
        ROOT / "tests" / ("test_broad_reader_direct_" + _kind + "_worker.py"))
    assert _spec is not None and _spec.loader is not None
    _module = module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    FIXTURES[_kind] = _module


def environment(kind, monkeypatch):
    fixture = FIXTURES[kind]
    values = (fixture._environment(monkeypatch) if kind == "reader"
              else fixture._environment())
    return fixture.worker, values[0], values[1]


def validate(kind, value):
    return spec.worker_environment(
        kind, value, final_git_sha=value.get("DEPLOYMENT_GIT_SHA"),
        source_sha256=fingerprint(source.verify_packaged_source(ROOT)),
        **controller.package_hashes(ROOT, kind=kind))


@pytest.mark.parametrize("kind", KINDS)
def test_main_snapshots_real_environ_before_strict_validation(monkeypatch, capsys, kind):
    worker, configured, expected_payload = environment(kind, monkeypatch)
    seen = []

    def checked_run(snapshot):
        # A later ambient change must not change the already captured request.
        os.environ["PGHOST"] = "synthetic-later-ambient-drift"
        seen.append(snapshot)
        assert validate(kind, snapshot) == expected_payload
        return "synthetic-entrypoint-success"

    monkeypatch.setattr(worker, "run_worker", checked_run)
    monkeypatch.setattr(sys, "argv", [spec.ROLES[kind].worker])
    with patch.dict(os.environ, configured, clear=True):
        assert type(os.environ) is not dict
        worker.main()
        assert os.environ["PGHOST"] == "synthetic-later-ambient-drift"
    assert len(seen) == 1 and type(seen[0]) is dict
    assert seen[0] == configured and "PGHOST" not in seen[0]
    output = capsys.readouterr()
    assert output.out == "synthetic-entrypoint-success\n" and output.err == ""
    assert "synthetic-only" not in output.out


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mutation", ("PGHOST", "BROAD_DIRECT_EXTRA", "wrong-role"))
def test_main_keeps_rejection_of_unapproved_environment(monkeypatch, capsys, kind, mutation):
    worker, configured, _ = environment(kind, monkeypatch)
    if mutation == "wrong-role":
        configured["BROAD_DIRECT_ROLE"] = "unapproved"
    else:
        configured[mutation] = "synthetic-unapproved"
    monkeypatch.setattr(worker, "run_worker", lambda value: validate(kind, value))
    monkeypatch.setattr(sys, "argv", [spec.ROLES[kind].worker])
    with patch.dict(os.environ, configured, clear=True):
        with pytest.raises(spec.DeliverySpecError,
                           match="^EXACT_DIRECT_WORKER_ENVIRONMENT_REQUIRED$"):
            worker.main()
    output = capsys.readouterr()
    assert output.out == "" and output.err == ""


@pytest.mark.parametrize("kind", KINDS)
def test_real_main_runs_existing_lifecycle_from_real_environ(monkeypatch, capsys, kind):
    worker, configured, payload = environment(kind, monkeypatch)
    worker._consumed = False
    calls = []

    class Connection:
        def __init__(self, number):
            self.number = number

        def close(self):
            calls.append("close:" + str(self.number))

    def connect(**arguments):
        number = 1 + sum(value.startswith("connect:") for value in calls)
        assert arguments["user"] == (spec.READER_ROLE if kind == "reader"
                                     else spec.OWNER_ROLE)
        calls.append("connect:" + str(number))
        return Connection(number)

    def capture(_connection, **arguments):
        assert arguments["window"] == payload["window"]
        calls.append("capture")
        return {"format": "synthetic-read-only-capture", "grant_authorized": False,
                "permission_enabled": False}

    def grant(_target, _observer, permit, **_arguments):
        assert permit.document == payload and permit.used is False
        calls.append("grant")
        return {"stage": "COMMITTED_STATE_VERIFIED", "permission_enabled": False,
                "authenticated_reader_verified": False, "ti_verified": False}

    def read(_connection, **arguments):
        assert arguments["planned_after"] == payload["planned_after"]
        calls.append("reader")
        return {"format": "synthetic-reader-result", "representative_select_completed": True,
                "permission_enabled": False, "ti_verified": False}

    monkeypatch.setattr(worker.psycopg, "connect", connect)
    if kind == "capture":
        monkeypatch.setattr(worker.direct, "capture_before", capture)
    elif kind == "grant":
        monkeypatch.setattr(worker.direct, "run", grant)
    else:
        monkeypatch.setattr(worker.reader, "run", read)
    monkeypatch.setattr(sys, "argv", [spec.ROLES[kind].worker])
    with patch.dict(os.environ, configured, clear=True):
        worker.main()
        with pytest.raises(spec.DeliverySpecError, match="CONSUMED_DIRECT_"):
            worker.main()
    output = capsys.readouterr()
    certificate = json.loads(output.out)
    stage = {"capture": "direct-capture-readonly", "grant": "direct-grant-transaction",
             "reader": "direct-authenticated-reader"}[kind]
    spec.verify_certificate(certificate, payload_sha256=fingerprint(payload), stage=stage)
    expected = (["connect:1", "connect:2", "grant", "close:2", "close:1"]
                if kind == "grant" else ["connect:1", kind, "close:1"])
    assert calls == expected and output.err == ""
    assert "synthetic-only" not in output.out


@pytest.mark.parametrize("consumed", ("261029a", "261030b"))
def test_both_consumed_capture_identities_are_denied(monkeypatch, consumed):
    job = "mf-reader-direct-capture-" + consumed
    assert job in spec.OLD_CONSUMED_JOBS
    assert spec.ROLES["capture"].job == "mf-reader-direct-capture-261030c"
    old = spec.Role("dev-bread-direct-cap-" + consumed, job,
                    spec.ROLES["capture"].worker,
                    spec.ROLES["capture"].service_account, spec.ROLES["capture"].secret)
    monkeypatch.setitem(spec.ROLES, "capture", old)
    with pytest.raises(spec.DeliverySpecError, match="CONSUMED_OR_ALIASED_JOB_IDENTITY"):
        spec.request("capture", root="", final_git_sha="", image={}, payload={},
                     worker_sha256="", controller_sha256="", spec_sha256="",
                     source_sha256="")
