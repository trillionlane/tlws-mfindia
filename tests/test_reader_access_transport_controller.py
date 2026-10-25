"""Provider-free controller lifecycle, whole seals, ambiguity and preservation."""

import ast
from copy import deepcopy
from pathlib import Path

import pytest

from mfdataindia.review import reader_access_transport_controller as core
from mfdataindia.review import reader_access_transport_backend as backend_module
from mfdataindia.review import reader_access_transport_spec as spec
from mfdataindia.review.reader_access_transport_backend import (
    Backend,
    load_approval,
    metadata_arguments,
)
from mfdataindia.review.rollout import seal_evidence
from test_reader_access_transport import approval

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "123456789012"
JOB_UID = "11111111-1111-4111-8111-111111111111"
EXEC_UID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(spec, "ARTIFACTS", tmp_path)


def binding(a):
    return {
        "packaged_program_sha256": core.WORKER_SHA,
        "packaged_spec_sha256": core.SPEC_SHA,
        "packaged_adapter_sha256": core.ADAPTER_SHA,
        "packaged_controller_sha256": a.controller_sha256,
        "packaged_engine_sha256": core.ENGINE_SHA,
        "packaged_profile_sha256": core.PROFILE_SHA,
        "manifest_image": a.manifest.image,
        "manifest_git_sha": a.manifest.git_sha,
    }


def expected_result(p, b):
    approval = core.approval_document(p)
    return {
        "engine_receipt": spec.expected_engine_receipt(p),
        "payload_sha256": core.fingerprint(p),
        "request_sha256": approval["request_sha256"],
        "approval_sha256": core.fingerprint(approval),
        "lock_witness": deepcopy(p["grant_proposal"]["lock_witness"]),
        "worker_binding": b,
        "performance_or_FS13_acceptance": False,
        "postcommit_permanence_guarantee": False,
    }


@pytest.mark.parametrize("field", ["approval_sha256", "request_sha256"])
@pytest.mark.parametrize("defect", ["missing", "wrong", "swapped"])
def test_terminal_exact_approval_and_request_hashes_are_required(field, defect):
    a = approval()
    value = expected_result(a.payload, binding(a))
    if defect == "missing":
        del value[field]
    elif defect == "wrong":
        value[field] = "0" * 64
    else:
        value[field] = value["request_sha256" if field == "approval_sha256" else "approval_sha256"]
    with pytest.raises(ValueError, match="full exact grant receipt"):
        core.validate_result(a, seal_evidence(a.manifest, core.STAGE, value))


def test_legacy_payload_only_terminal_evidence_is_rejected():
    a = approval()
    value = expected_result(a.payload, binding(a))
    del value["request_sha256"], value["approval_sha256"]
    with pytest.raises(ValueError, match="full exact grant receipt"):
        core.validate_result(a, seal_evidence(a.manifest, core.STAGE, value))


def metadata(name, *, execution=False, annotations=None):
    result = {
        "name": name,
        "namespace": NAMESPACE,
        "uid": EXEC_UID if execution else JOB_UID,
        "generation": 1,
        "creationTimestamp": "2026-10-21T20:00:01Z" if execution else "2026-10-21T20:00:00Z",
        "labels": {"cloud.googleapis.com/location": core.REGION},
        "annotations": {
            "run.googleapis.com/creator": core.ACCOUNT,
            "run.googleapis.com/lastModifier": core.ACCOUNT,
            **(annotations or {}),
        },
    }
    if execution:
        result["labels"].update(
            {
                "run.googleapis.com/job": core.NAME,
                "run.googleapis.com/jobUid": JOB_UID,
                "run.googleapis.com/jobGeneration": "1",
            }
        )
    return result


class FakeBackend:
    def __init__(self, a, *, defect=None):
        self.a = a
        self.value = core.inputs(a)
        self.events = []
        self.defect = defect
        self.protected_reads = 0

    def preflight(self, a):
        self.events.append("preflight")
        if self.defect == "preflight":
            raise ValueError("synthetic existing job")
        return NAMESPACE

    def protected(self):
        self.protected_reads += 1
        return {
            "protected": False if self.defect == "protected" and self.protected_reads > 2 else True
        }

    def image_resolution(self):
        return {
            "image": self.a.manifest.image,
            "resolved_image": self.a.manifest.image,
            "config_digest": "sha256:" + "d" * 64,
            "git_sha": self.a.manifest.git_sha,
            "platform": "linux/amd64",
            "layers_downloaded": 0,
        }

    def create(self, body):
        self.events.append("create")
        assert body == self.value["body"]
        if self.defect == "create":
            raise TimeoutError("synthetic ambiguous create")
        return self.job()

    def job(self):
        spec = deepcopy(self.value["request"]["spec"])
        if self.defect == "deployed":
            spec["template"]["spec"]["template"]["spec"]["maxRetries"] = 1
        status = {"conditions": [{"type": "Ready", "status": "True"}], "executionCount": 0}
        if "execute" in self.events:
            status.update(
                {
                    "executionCount": 1,
                    "latestCreatedExecution": {
                        "name": core.NAME + "-abcde",
                        "creationTimestamp": "2026-10-21T20:00:01Z",
                    },
                }
            )
        return {
            "apiVersion": "run.googleapis.com/v1",
            "kind": "Job",
            "metadata": metadata(core.NAME),
            "spec": spec,
            "status": status,
        }

    def execute(self):
        self.events.append("execute")
        return self.execution(core.NAME + "-abcde")

    def execution(self, name):
        task = deepcopy(self.value["task"])
        if self.defect == "immutable":
            task["containers"][0]["args"] = ["other-script"]
        return {
            "apiVersion": "run.googleapis.com/v1",
            "kind": "Execution",
            "metadata": metadata(name, execution=True, annotations=self.value["annotations"]),
            "spec": {"taskCount": 1, "parallelism": 1, "template": {"spec": task}},
            "status": {
                "succeededCount": 1,
                "conditions": [
                    {
                        "type": "Completed",
                        "status": "False" if self.defect == "terminal" else "True",
                    }
                ],
            },
        }

    def executions(self):
        return [self.execution(core.NAME + "-abcde")] if "execute" in self.events else []

    def certificate(self, name):
        result = expected_result(self.a.payload, binding(self.a))
        if self.defect == "certificate":
            result["worker_binding"]["packaged_program_sha256"] = "0" * 64
        return seal_evidence(self.a.manifest, core.STAGE, result)


def run_fixture(a, backend):
    root = Path(a.root)
    root.mkdir()
    (root / "approval.json").write_text("{}")
    records = {}

    def claim(path, value):
        if path in records:
            raise FileExistsError("synthetic consumed record")
        records[path] = deepcopy(value)

    return root, records, claim


def test_once_full_seals_final_inventory_then_LAST_protected_and_duplicate_STOP():
    a = approval()
    b = FakeBackend(a)
    root, records, claim = run_fixture(a, b)
    reads = []
    protected, inventory = b.protected, b.executions

    def read_protected():
        reads.append("protected")
        return protected()

    def read_inventory():
        reads.append("inventory")
        return inventory()

    b.protected, b.executions = read_protected, read_inventory
    result = core.run(a, b, claim=claim)
    assert b.events == ["preflight", "create", "execute"]
    assert reads[-2:] == ["inventory", "protected"]
    assert list(records)[-2:] == [root / "protected-final.json", root / "result.json"]
    assert records[root / "execute-intent.json"]["body"] == {}
    assert records[root / "intent.json"]["request"] == core.inputs(a)["request"]
    assert records[root / "result.json"]["approval_sha256"] == core.fingerprint(
        a.model_dump(mode="json")
    )
    assert records[root / "result.json"]["request_sha256"] == a.request_sha256
    assert result == expected_result(a.payload, binding(a))
    with pytest.raises(FileExistsError):
        core.run(a, b, claim=claim)
    assert b.events == ["preflight", "create", "execute"]


@pytest.mark.parametrize(
    "defect,events",
    [
        ("preflight", ["preflight"]),
        ("create", ["preflight", "create"]),
        ("deployed", ["preflight", "create"]),
        ("immutable", ["preflight", "create", "execute"]),
        ("terminal", ["preflight", "create", "execute"]),
        ("certificate", ["preflight", "create", "execute"]),
        ("protected", ["preflight", "create", "execute"]),
    ],
)
def test_every_ambiguous_failed_stage_STOP_retains_no_retry(defect, events):
    a = approval()
    b = FakeBackend(a, defect=defect)
    root, records, claim = run_fixture(a, b)
    with pytest.raises((ValueError, TimeoutError)):
        core.run(a, b, claim=claim)
    assert b.events == events
    assert root / "result.json" not in records
    assert root / "failure.json" in records or root / "protected-failure.json" in records


@pytest.mark.parametrize(
    "defect",
    [
        "image",
        "args",
        "env_value",
        "extra_env",
        "duplicate_env",
        "secret_name",
        "secret_version",
        "service_account",
        "retry",
        "task_timeout",
        "volumes",
        "cpu",
        "memory",
    ],
)
def test_whole_task_every_capability_drift_fails(defect):
    a = approval()
    prepared = core.inputs(a)
    task = deepcopy(prepared["task"])
    c = task["containers"][0]
    if defect == "image":
        c["image"] = "unapproved"
    elif defect == "args":
        c["args"] = ["other"]
    elif defect == "env_value":
        c["env"][0]["value"] = "unapproved"
    elif defect == "extra_env":
        c["env"].append({"name": "EXTRA", "value": "unapproved"})
    elif defect == "duplicate_env":
        c["env"].append(c["env"][0])
    elif defect == "secret_name":
        c["env"][-1]["valueFrom"]["secretKeyRef"]["name"] = "other"
    elif defect == "secret_version":
        c["env"][-1]["valueFrom"]["secretKeyRef"]["key"] = "latest"
    elif defect == "service_account":
        task["serviceAccountName"] = "other"
    elif defect == "retry":
        task["maxRetries"] = 1
    elif defect == "task_timeout":
        task["timeoutSeconds"] = "900"
    elif defect == "volumes":
        task["volumes"] = [{"name": "other"}]
    else:
        c["resources"]["limits"][defect] = "other"
    with pytest.raises(ValueError):
        core.validate_task(task, prepared["task"])


@pytest.mark.parametrize(
    "defect",
    [
        "uid",
        "created",
        "namespace",
        "creator",
        "network",
        "extra_job_spec",
        "task_count",
        "parent_uid",
        "execution_args",
        "execution_image",
    ],
)
def test_job_execution_uid_time_parent_and_whole_input_seals(defect):
    b = FakeBackend(approval())
    job = b.job()
    if defect in ("parent_uid", "execution_args", "execution_image"):
        seal = core.deployed(job, b.value, namespace=NAMESPACE)
        e = b.execute()
        if defect == "parent_uid":
            e["metadata"]["labels"]["run.googleapis.com/jobUid"] = "other"
        else:
            e["spec"]["template"]["spec"]["containers"][0][
                "args" if defect == "execution_args" else "image"
            ] = ["other"] if defect == "execution_args" else "other"
        with pytest.raises(ValueError):
            core.immutable_execution(e, b.value, seal, namespace=NAMESPACE)
        return
    if defect == "uid":
        job["metadata"]["uid"] = "other"
    elif defect == "created":
        job["metadata"]["creationTimestamp"] = "ambiguous"
    elif defect == "namespace":
        job["metadata"]["namespace"] = "other"
    elif defect == "creator":
        job["metadata"]["annotations"]["run.googleapis.com/creator"] = "other"
    elif defect == "network":
        job["spec"]["template"]["metadata"]["annotations"][
            "run.googleapis.com/vpc-access-connector"
        ] = "other"
    elif defect == "extra_job_spec":
        job["spec"]["runExecutionToken"] = ""
    else:
        job["spec"]["template"]["spec"]["taskCount"] = True
    with pytest.raises(ValueError):
        core.deployed(job, b.value, namespace=NAMESPACE)


@pytest.mark.parametrize("stage", ["before", "after", "final"])
def test_sibling_inventory_never_admitted(stage):
    a = approval()
    b = FakeBackend(a)
    root, records, claim = run_fixture(a, b)
    original = b.executions
    count = 0

    def inventory():
        nonlocal count
        count += 1
        value = original()
        inject = {"before": 2, "after": 3, "final": 4}[stage]
        if count == inject:
            return value + [b.execution(core.NAME + "-extra")]
        return value

    b.executions = inventory
    with pytest.raises(ValueError):
        core.run(a, b, claim=claim)
    assert root / "result.json" not in records
    assert b.events.count("execute") == (0 if stage == "before" else 1)


def test_preflight_checks_all_source_pins_before_any_provider_call(monkeypatch):
    a = approval()
    b = Backend(a, ROOT)
    monkeypatch.setattr(backend_module, "verify_checkout", lambda *_: None)
    monkeypatch.setattr(core.permission, "__file__", "/tmp/shadow.py")
    monkeypatch.setattr(b, "metadata", lambda *_: pytest.fail("provider on shadow spec"))
    with pytest.raises(ValueError):
        b.preflight(a)
    b.session.close()


def test_backend_consumes_ambiguous_POST_before_token_and_never_retry(monkeypatch):
    a = approval()
    b = Backend(a, ROOT)
    seen = []

    def token(*_):
        seen.append("token")
        raise TimeoutError("SYNTHETIC_AMBIGUITY")

    monkeypatch.setattr(b, "command", token)
    with pytest.raises(TimeoutError):
        b.create(b.prepared["body"])
    with pytest.raises(ValueError):
        b.create(b.prepared["body"])
    assert seen == ["token"] and b.created and not b.executed
    b.session.close()


def test_exclusive_durable_records_and_duplicate_json(tmp_path):
    p = tmp_path / "intent.json"
    core.exclusive(p, {"first": True})
    assert p.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        core.exclusive(p, {"second": True})
    link = tmp_path / "link"
    link.symlink_to(p)
    with pytest.raises(FileExistsError):
        core.exclusive(link, {})
    bad = tmp_path / "approval.json"
    bad.write_text('{"payload":{},"payload":{}}')
    with pytest.raises(ValueError):
        load_approval(bad, "a" * 64)


def test_preserved_guard_bodies_are_FULL_source_byte_equal():
    for old, new, changed, added in [
        (
            "core_access_metadata_controller_v2.py",
            "reader_access_transport_controller.py",
            {"exact_scope", "inputs", "validate_result", "run"},
            {
                "manifest",
                "root",
                "controller_git_sha",
                "controller_sha256",
                "protected_hash",
                "window",
                "inputs_payload",
                "approval_document",
            },
        ),
        (
            "core_access_metadata_backend_v2.py",
            "reader_access_transport_backend.py",
            {"__init__", "preflight", "_post"},
            set(),
        ),
    ]:

        def bodies(name):
            text = (ROOT / "src/mfdataindia/review" / name).read_text()
            return {
                node.name: ast.get_source_segment(text, node)
                for node in ast.walk(ast.parse(text))
                if isinstance(node, ast.FunctionDef)
            }

        first, second = bodies(old), bodies(new)
        assert first.keys() | added == second.keys()
        for name in first.keys() - changed:
            assert first[name] == second[name], name


@pytest.mark.parametrize(
    "kind,name",
    [
        ("secret", None),
        ("job", "other-job"),
        ("executions", "other-job"),
        ("logs", "old-execution"),
        ("iam-write", None),
    ],
)
def test_no_arbitrary_provider_secret_or_target_operation(kind, name):
    with pytest.raises(ValueError):
        metadata_arguments(kind, name)


def test_execution_inventory_is_provider_scoped_to_fixed_new_owner_job():
    args = metadata_arguments("executions")
    assert "--job=" + core.NAME in args
    assert "--project=trillionlane-dev" in args and "--region=asia-south1" in args
    assert "--limit=2" in args and not any(x.startswith("--filter") for x in args)


def test_post_rechecks_expiry_after_token_latency_before_HTTP(monkeypatch):
    a = approval()
    b = Backend(a, ROOT)
    checks = []

    def window(*_, **__):
        checks.append(True)
        if len(checks) == 2:
            raise ValueError("SYNTHETIC_TOKEN_LATENCY_EXPIRED_WINDOW")

    monkeypatch.setattr(spec, "live_window", window)
    monkeypatch.setattr(b, "command", lambda *_: "synthetic-token-not-real")
    monkeypatch.setattr(b.session, "post", lambda *_a, **_kw: pytest.fail("HTTP after expiry"))
    with pytest.raises(ValueError):
        b.create(b.prepared["body"])
    assert b.created and len(checks) == 2
    with pytest.raises(ValueError):
        b.create(b.prepared["body"])
    b.session.close()
