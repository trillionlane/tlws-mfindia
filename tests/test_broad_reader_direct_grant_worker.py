"""Provider-free single-commit owner-worker lifecycle and capacity tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from mfdataindia.review import broad_reader_direct_capacity as capacity
from mfdataindia.review import broad_reader_direct_delivery_controller as controller
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_source_authority as source
from scripts import apply_broad_reader_direct_dev as worker


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
    roots = [{"oid": 1, "nspname": "mf", "relname": "funds", "relkind": "r"},
             {"oid": 2, "nspname": "mf", "relname": "fund_view", "relkind": "v"}]
    leaves = [{"oid": 100 + i, "nspname": "pg_catalog",
               "relname": name, "relkind": "r"}
              for i, name in enumerate(("pg_class", "pg_inherits", "pg_namespace"))]
    payload = {
        "format": "mf-broad-reader-direct-grant-v1",
        "window": {"starts_at": (now - timedelta(seconds=5)).isoformat(),
                   "ends_at": (now + timedelta(minutes=5)).isoformat(),
                   "exclusive_administration": True,
                   "data_schema_custody": True,
                   "future_owner_provisioning": True},
        "before_snapshot": {"permission_enabled": False,
                            "plan": {"permission_acceptance": False,
                                     "relation_scope": {"grant_roots": roots,
                                                        "lock_only_leaves": leaves,
                                                        "materialized_unlocked": []},
                                     "delta": {"schemas": [], "relations": [],
                                               "columns": [], "defaults": []}}},
        "helper_attestation": {"format": "synthetic-only"},
        "packaged_source": source.verify_packaged_source(ROOT),
        "git_sha": GIT,
        "image_binding": {"app": deepcopy(APP), "tools": deepcopy(TOOLS)},
        "create_only_intent_sha256": "d" * 64,
    }
    root = str(spec.ARTIFACTS / (spec.ROOT_PREFIXES["grant"] + "abcdef"))
    payload["create_only_intent_sha256"] = spec.intent_preimage(
        "grant", root, payload)[1]
    prepared = controller.prepare_grant(
        payload=payload, root=root, final_git_sha=GIT, image=APP, source_root=ROOT)
    env = {row["name"]: row["value"] for row in prepared["task"]["containers"][0]["env"]
           if "value" in row}
    env.update({"MFDATAINDIA_DSN": DSN,
                "CLOUD_RUN_JOB": spec.ROLES["grant"].job,
                "CLOUD_RUN_EXECUTION": spec.ROLES["grant"].job + "-abcde",
                "CLOUD_RUN_TASK_INDEX": "0", "CLOUD_RUN_TASK_ATTEMPT": "0",
                "CLOUD_RUN_TASK_COUNT": "1"})
    return env, payload


def test_grant_worker_uses_two_owner_connections_and_reports_unaccepted_commit(monkeypatch):
    env, payload = _environment()
    worker._consumed = False
    calls = []

    class FakeConnection:
        def __init__(self, name):
            self.name = name

        def close(self):
            calls.append("close:" + self.name)

    def connect(**kwargs):
        assert kwargs["user"] == spec.OWNER_ROLE
        name = "target" if not calls else "observer"
        calls.append("connect:" + name)
        return FakeConnection(name)

    def apply(target, observer, permit, *, source_root):
        assert (target.name, observer.name) == ("target", "observer")
        assert permit.used is False and permit.document == payload
        assert source_root == ROOT
        calls.append("grant")
        return {"stage": "COMMITTED_STATE_VERIFIED",
                "permission_enabled": False,
                "authenticated_reader_verified": False, "ti_verified": False}

    monkeypatch.setattr(worker.psycopg, "connect", connect)
    monkeypatch.setattr(worker.direct, "run", apply)
    result = json.loads(worker.run_worker(env))
    assert result["stage"] == "direct-grant-transaction"
    assert result["evidence"]["transaction_receipt"]["permission_enabled"] is False
    assert result["evidence"]["capacity_proof"]["grant_authorized"] is False
    assert calls == ["connect:target", "connect:observer", "grant",
                     "close:observer", "close:target"]
    with pytest.raises(spec.DeliverySpecError, match="CONSUMED_DIRECT_GRANT_WORKER"):
        worker.run_worker(env)


def test_unreportable_grant_is_rejected_before_any_connection(monkeypatch):
    env, _ = _environment()
    worker._consumed = False
    monkeypatch.setattr(worker.psycopg, "connect",
                        lambda **_: pytest.fail("must not connect"))
    monkeypatch.setattr(worker.capacity, "prove_capacity",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            capacity.CapacityError("synthetic bound failure")))
    with pytest.raises(capacity.CapacityError):
        worker.run_worker(env)


def test_ambiguous_commit_closes_both_without_retry_or_reversal(monkeypatch):
    env, _ = _environment()
    worker._consumed = False
    calls = []

    class FakeConnection:
        def close(self):
            calls.append("close")

    monkeypatch.setattr(worker.psycopg, "connect",
                        lambda **_: (calls.append("connect"), FakeConnection())[1])

    def fail(*_args, **_kwargs):
        calls.append("grant")
        raise worker.direct.GrantError("COMMIT_OUTCOME_UNKNOWN")

    monkeypatch.setattr(worker.direct, "run", fail)
    with pytest.raises(worker.direct.GrantError, match="COMMIT_OUTCOME_UNKNOWN"):
        worker.run_worker(env)
    assert calls == ["connect", "connect", "grant", "close", "close"]
