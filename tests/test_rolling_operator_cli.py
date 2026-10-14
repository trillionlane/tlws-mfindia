"""Hermetic actual CLI dispatch; no database connection or cloud operation."""

import importlib.util
import json
from pathlib import Path

import pytest

from mfdataindia.review.rollout import CONNECTION, seal_evidence, verify_evidence
from test_verified_review_rollout import manifest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def operator(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "rolling_cli_fixture", ROOT / "scripts/operate_rolling_analytics_dev.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args):
            calls.append(("sql", args))

    monkeypatch.setattr(module.psycopg, "connect", lambda *args, **kwargs: Connection())
    return module, calls


def environment(m, operation):
    role = "mfdata_verified_generator" if operation == "generate" else "mfdata_admin"
    return {
        "DEPLOYMENT_GIT_SHA": m.git_sha,
        "ROLLING_OPERATION": operation,
        "MFDATAINDIA_DSN": f"host='/cloudsql/{CONNECTION}' dbname=mfdataindia user={role} password=fixture-only",
    }


def test_approved_rolling_install_selector_reaches_the_rolling_installer(operator, monkeypatch):
    module, calls = operator
    m = manifest()

    def install(conn, root):
        calls.append(("install", root))
        return {"status": "INSTALLED", "source_rows_changed": 0}

    monkeypatch.setattr(module.operations, "install", install)
    evidence = verify_evidence(
        m, "rolling-install", module.execute(m, environment(m, "install"), ROOT)
    )
    assert evidence["status"] == "INSTALLED" and evidence["source_writes"] == 0
    assert calls[-1] == ("install", ROOT)


def test_generator_selector_routes_to_rolling_publication_and_exact_replay(operator, monkeypatch):
    module, calls = operator
    m = manifest()
    monkeypatch.setattr(module, "verify_runtime", lambda c, p: calls.append(("runtime", p)))
    monkeypatch.setattr(module.store, "verify_acl", lambda c: calls.append(("rolling-acl",)))
    monkeypatch.setattr(module.review, "capture", lambda c, code: {"code": code})

    def publish(conn, snapshots):
        calls.append(("publish", tuple(s["code"] for s in snapshots)))
        return [
            {
                "code": s["code"],
                "result_id": "a" * 64,
                "input_hash": "b" * 64,
                "policy_hash": "c" * 64,
            }
            for s in snapshots
        ]

    monkeypatch.setattr(module.store, "publish", publish)
    evidence = verify_evidence(
        m, "rolling-generate", module.execute(m, environment(m, "generate"), ROOT)
    )
    assert [r["code"] for r in evidence["results"]] == sorted(m.cohort)
    assert sum(c[0] == "publish" for c in calls) == 2
    assert ("runtime", "generator") in calls and ("rolling-acl",) in calls


def test_register_uses_rolling_record_key_not_calendar_key(operator, monkeypatch):
    module, calls = operator
    m = manifest()
    cert = seal_evidence(
        m,
        "rolling-http",
        {
            "level": "DEV_PRIVATE_HTTP",
            "origin": "https://fixture.a.run.app",
            "audience": "https://fixture.a.run.app",
            "source_writes": 0,
            "records_appended": 0,
        },
    )
    record = {"manifest": m.model_dump(mode="json"), "certificate": cert}

    def register(conn, value):
        calls.append(("register", value))
        return "d" * 64

    monkeypatch.setattr(module.store, "register", register)
    env = {**environment(m, "register"), "ROLLING_RECORD": json.dumps(record)}
    evidence = verify_evidence(m, "rolling-register", module.execute(m, env, ROOT))
    assert evidence["certificate_id"] == "d" * 64 and calls[-1] == ("register", record)


def test_legacy_calendar_selector_cannot_accidentally_run_rolling_work(operator):
    module, calls = operator
    m = manifest()
    env = environment(m, "install")
    del env["ROLLING_OPERATION"]
    env["CALENDAR_OPERATION"] = "install"
    with pytest.raises(KeyError, match="ROLLING_OPERATION"):
        module.execute(m, env, ROOT)
    assert calls == []


@pytest.mark.parametrize("operation", ["unsupported", "INSTALL", ""])
def test_unknown_rolling_operation_fails_before_database_work(operator, operation):
    module, calls = operator
    m = manifest()
    with pytest.raises(ValueError, match="unsupported rolling operation"):
        module.execute(m, environment(m, operation), ROOT)
    assert calls == []
