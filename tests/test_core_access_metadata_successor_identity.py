"""Finite successor identity and complete byte preservation; provider-free."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from ro01_historical_source import historical_parent

from mfdataindia.review import core_access_metadata_controller as old
from mfdataindia.review import core_access_metadata_controller_v2 as core
from mfdataindia.review.core_access_metadata_backend_v2 import metadata_arguments
from test_core_access_metadata_controller import approval as old_approval
from test_core_access_metadata_controller_v2 import approval, FakeBackend, NAMESPACE
from test_core_access_metadata_collector_v2 import binding_fixture, worker

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "docs/CORE_ACCESS_METADATA_SUCCESSOR_LITERAL_PLAN_20261024.json"
AUTH_PATH = ROOT / "docs/CORE_ACCESS_METADATA_SUCCESSOR_F2CA_AUTHORITY_20261024.json"


def test_preimplementation_literal_plan_and_authority_are_exact():
    assert hashlib.sha256(PLAN_PATH.read_bytes()).hexdigest() == "031fee5874f2b8b962ed3559b3e27c6d255a1b15323476d4dd2ec9d2195b247b"
    assert hashlib.sha256(AUTH_PATH.read_bytes()).hexdigest() == "b31771005bfe48ce40eb9e31cbb887dee341230df1e4172efe1629da163a6734"


@pytest.mark.parametrize("section", ["production", "mechanically_replayed_tests"])
def test_entire_successor_bytes_normalize_only_counted_literal_substitutions(section):
    plan = json.loads(PLAN_PATH.read_bytes())
    for predecessor, entry in plan[section].items():
        before = historical_parent(ROOT / predecessor, (ROOT / predecessor).read_bytes())
        raw = historical_parent(ROOT / entry["successor_path"], (ROOT / entry["successor_path"]).read_bytes())
        assert hashlib.sha256(before).hexdigest() == entry["predecessor_sha256"]
        if "successor_sha256" in entry:
            assert hashlib.sha256(raw).hexdigest() == entry["successor_sha256"]
        for rule in entry["normalization"]:
            first, second = rule["successor_literal"].encode(), rule["predecessor_literal"].encode()
            assert raw.count(first) == rule["occurrences"]
            raw = raw.replace(first, second)
        assert raw == before


def test_all_frozen_predecessor_inventory_and_tests_unchanged_except_explicit_ci_append():
    authority = json.loads(AUTH_PATH.read_bytes())
    excluded = {".github/workflows/verify.yml", "tests/test_ci_verify_sequence.py"}
    for name, digest in authority["sha256"].items():
        if name not in excluded:
            assert hashlib.sha256(historical_parent(ROOT / name, (ROOT / name).read_bytes())).hexdigest() == digest


def test_new_identity_job_root_and_global_start_are_disjoint_from_consumed_attempt():
    assert core.IDENTITY == "dev-core-aclmeta-261024b"
    assert core.NAME == "mf-core-access-metadata-261024b"
    assert core.START.name == core.NAME + "-controller-start.json"
    assert core.START != old.START
    assert core.WORKER == "scripts/collect_core_access_metadata_dev_v2.py"
    assert core.WORKER_SHA == hashlib.sha256((ROOT / core.WORKER).read_bytes()).hexdigest()
    assert core.SPEC_SHA == hashlib.sha256(Path(core.permission.__file__).read_bytes()).hexdigest()
    assert approval().root.endswith("mf-core-access-metadata-20261024-aBc123")


def test_old_and_new_approval_profiles_cannot_authorize_each_other():
    with pytest.raises(ValueError):
        core.Approval.model_validate(old_approval().model_dump(mode="json"))
    with pytest.raises(ValueError):
        old.Approval.model_validate(approval().model_dump(mode="json"))
    with pytest.raises(ValueError):
        approval(root=str(old.ARTIFACTS / "mf-core-access-metadata-20261022-aBc123"))


@pytest.mark.parametrize("kind,name", [("job", old.NAME), ("jobIAM", old.NAME),
                                      ("execution", old.NAME + "-abcde"), ("logs", old.NAME + "-abcde")])
def test_successor_adapter_cannot_target_consumed_job_or_execution(kind, name):
    with pytest.raises(ValueError):
        metadata_arguments(kind, name)
    assert "--job=" + core.NAME in metadata_arguments("executions")


def test_successor_worker_rejects_old_identity_before_dsn(monkeypatch):
    manifest, env = binding_fixture()
    manifest.rollout_id = old.IDENTITY
    monkeypatch.setattr(worker.Manifest, "model_validate_json", lambda _: manifest)
    monkeypatch.setattr(worker, "pinned_dsn", lambda *_: pytest.fail("DSN reached"))
    monkeypatch.setattr(worker.sys, "argv", ["worker"])
    monkeypatch.setenv("REVIEW_MANIFEST", "{}")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(worker.ProbeFailure, match="binding:ValueError"):
        worker.main()


def test_successor_created_job_cannot_accept_old_parent_metadata():
    a = approval()
    b = FakeBackend(a)
    job = b.job()
    job["metadata"]["name"] = old.NAME
    with pytest.raises(ValueError):
        core.deployed(job, core.inputs(a), namespace=NAMESPACE)


def test_relabelled_old_certificate_cannot_pass_successor_binding():
    a = approval()
    b = FakeBackend(a)
    certificate = b.certificate(core.NAME + "-abcde")
    modified = deepcopy(certificate)
    modified["manifest_hash"] = old_approval().manifest.seal
    with pytest.raises(ValueError):
        core.validate_result(a, modified)
