"""Provider-free authenticated index/child equivalence; never runtime evidence."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from mfdataindia.review import core_access_metadata_controller as core
from mfdataindia.review import core_access_metadata_backend as adapter
from mfdataindia.review.image_provenance import verify_image
from test_core_access_metadata_controller import FakeBackend, approval, NAMESPACE
from test_verified_review_image_provenance import BASE, Cloud, setup_fixture


def resolution(a):
    return {
        "image": a.manifest.image,
        "resolved_image": BASE + "@sha256:" + "c" * 64,
        "config_digest": "sha256:" + "d" * 64,
        "git_sha": a.manifest.git_sha,
        "platform": "linux/amd64",
        "layers_downloaded": 0,
    }


@pytest.mark.parametrize("index", [False, True])
def test_verifier_opt_in_binds_root_child_config_bytes_without_changing_default(index):
    image, session = setup_fixture(index=index)
    receipt = verify_image(Cloud(), image, "a" * 40, session=session, include_resolution=True)
    assert set(receipt) == {
        "image",
        "resolved_image",
        "config_digest",
        "git_sha",
        "platform",
        "layers_downloaded",
    }
    assert receipt["image"] == image and receipt["git_sha"] == "a" * 40
    assert receipt["resolved_image"] == BASE + "@" + session.calls[-2].rsplit("/", 1)[-1]
    assert receipt["config_digest"] == session.calls[-1].rsplit("/", 1)[-1]
    assert receipt["platform"] == "linux/amd64" and receipt["layers_downloaded"] == 0
    assert len(session.calls) == (3 if index else 2)
    image, session = setup_fixture(index=index)
    assert verify_image(Cloud(), image, "a" * 40, session=session) == {
        "image": image,
        "git_sha": "a" * 40,
        "platform": "linux/amd64",
        "layers_downloaded": 0,
    }


@pytest.mark.parametrize(
    "options", [{"sha": "b" * 40}, {"platform": "arm64"}, {"status": 302}, {"corrupt": True}]
)
def test_resolution_verifier_keeps_git_platform_redirect_and_byte_guards(options):
    image, session = setup_fixture(index=True, **options)
    with pytest.raises(ValueError):
        verify_image(Cloud(), image, "a" * 40, session=session, include_resolution=True)


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate_json",
        "duplicate_child_json",
        "duplicate_config_json",
        "two_children",
        "no_child",
        "bad_child_digest",
        "child_bytes",
        "config_bytes",
    ],
)
def test_resolution_rejects_ambiguous_or_unverified_oci_metadata(defect):
    config = json.dumps(
        {
            "os": "linux",
            "architecture": "amd64",
            "config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}},
        }
    ).encode()
    if defect == "duplicate_config_json":
        config = b'{"os":"linux",' + config[1:]
    config_digest = "sha256:" + hashlib.sha256(config).hexdigest()
    child = json.dumps({"config": {"digest": config_digest}}).encode()
    if defect == "duplicate_child_json":
        child = b'{"config":{},' + child[1:]
    child_digest = "sha256:" + hashlib.sha256(child).hexdigest()
    descriptor = {"digest": child_digest, "platform": {"os": "linux", "architecture": "amd64"}}
    children = [descriptor]
    if defect == "two_children":
        children *= 2
    if defect == "no_child":
        children = []
    if defect == "bad_child_digest":
        descriptor["digest"] = "latest"
    index = json.dumps({"manifests": children}).encode()
    if defect == "duplicate_json":
        index = b'{"manifests":[],"manifests":' + json.dumps(children).encode() + b"}"
    digest = "sha256:" + hashlib.sha256(index).hexdigest()
    blobs = {digest: index, child_digest: child, config_digest: config}
    if defect == "child_bytes":
        blobs[child_digest] += b"tamper"
    if defect == "config_bytes":
        blobs[config_digest] += b"tamper"

    class Session:
        trust_env = True

        def get(self, url, **kwargs):
            return type(
                "Response", (), {"status_code": 200, "content": blobs[url.rsplit("/", 1)[-1]]}
            )()

    with pytest.raises(ValueError):
        verify_image(
            Cloud(), BASE + "@" + digest, "a" * 40, session=Session(), include_resolution=True
        )


@pytest.mark.parametrize("first_child", [False, True])
def test_full_controller_handles_authenticated_platform_resolution_and_retains_raw(
    tmp_path, first_child
):
    a = approval().model_copy(update={"root": str(tmp_path)})
    (tmp_path / "approval.json").write_text("{}")
    b = FakeBackend(a)
    proof = resolution(a)
    b.image_resolution = lambda: proof
    original = b.execution
    reads = 0

    def execution(name):
        nonlocal reads
        e = original(name)
        reads += 1
        if first_child or reads > 1:
            e["spec"]["template"]["spec"]["containers"][0]["image"] = proof["resolved_image"]
        return e

    b.execution = execution
    records = {}
    core.run(a, b, claim=lambda p, v: records.setdefault(p, deepcopy(v)))
    assert b.events == ["preflight", "create", "execute"]
    assert list(records).index(tmp_path / "image-resolution.json") < list(records).index(
        tmp_path / "intent.json"
    )
    assert records[tmp_path / "image-resolution.json"] == proof
    assert records[tmp_path / "original-execution.json"]["spec"]["template"]["spec"]["containers"][
        0
    ]["image"] == (proof["resolved_image"] if first_child else a.manifest.image)
    assert list(records)[-2:] == [tmp_path / "protected-final.json", tmp_path / "result.json"]


@pytest.mark.parametrize(
    "defect",
    ["absent", "root", "git", "repository", "platform", "config", "layers", "bool_layers", "extra"],
)
def test_invalid_resolution_stops_before_create(tmp_path, defect):
    a = approval().model_copy(update={"root": str(tmp_path)})
    (tmp_path / "approval.json").write_text("{}")
    b = FakeBackend(a)
    proof = resolution(a)
    if defect == "absent":
        proof = None
    elif defect == "root":
        proof["image"] = BASE + "@sha256:" + "f" * 64
    elif defect == "git":
        proof["git_sha"] = "0" * 40
    elif defect == "repository":
        proof["resolved_image"] = proof["resolved_image"].replace(
            "/mfdataindia@", "/mfdataindia-review-tools@"
        )
    elif defect == "platform":
        proof["platform"] = "linux/arm64"
    elif defect == "config":
        proof["config_digest"] = "latest"
    elif defect == "layers":
        proof["layers_downloaded"] = 1
    elif defect == "bool_layers":
        proof["layers_downloaded"] = False
    else:
        proof["extra"] = "unapproved"
    b.image_resolution = lambda: proof
    records = {}
    with pytest.raises(ValueError):
        core.run(a, b, claim=lambda p, v: records.setdefault(p, deepcopy(v)))
    assert b.events == ["preflight"]
    assert tmp_path / "intent.json" not in records and tmp_path / "result.json" not in records


@pytest.mark.parametrize(
    "defect", ["unknown_child", "repository", "env", "secret", "resource", "network", "parent"]
)
def test_resolved_image_never_hides_unrelated_execution_drift(defect):
    a = approval()
    b = FakeBackend(a)
    p = core.inputs(a)
    p["image_resolution"] = resolution(a)
    seal = core.deployed(b.job(), p, namespace=NAMESPACE)
    e = b.execute()
    e["spec"]["template"]["spec"]["containers"][0]["image"] = p["image_resolution"][
        "resolved_image"
    ]
    task = e["spec"]["template"]["spec"]
    container = task["containers"][0]
    if defect == "unknown_child":
        container["image"] = BASE + "@sha256:" + "f" * 64
    elif defect == "repository":
        container["image"] = container["image"].replace("/mfdataindia@", "/other@")
    elif defect == "env":
        container["env"][0]["value"] = "different"
    elif defect == "secret":
        container["env"][-1]["valueFrom"]["secretKeyRef"]["key"] = "latest"
    elif defect == "resource":
        container["resources"]["limits"]["cpu"] = "2"
    elif defect == "network":
        e["metadata"]["annotations"]["run.googleapis.com/vpc-access-egress"] = "private-ranges-only"
    else:
        e["metadata"]["labels"]["run.googleapis.com/jobUid"] = "other"
    saved = deepcopy(e)
    with pytest.raises(ValueError):
        core.immutable_execution(e, p, seal, namespace=NAMESPACE)
    assert e == saved


def test_child_without_proof_is_rejected_and_receipt_is_defensively_copied():
    a = approval()
    b = FakeBackend(a)
    p = core.inputs(a)
    e = b.execute()
    seal = core.deployed(b.job(), p, namespace=NAMESPACE)
    e["spec"]["template"]["spec"]["containers"][0]["image"] = resolution(a)["resolved_image"]
    with pytest.raises(ValueError):
        core.immutable_execution(e, p, seal, namespace=NAMESPACE)
    backend = adapter.Backend(a, ".")
    try:
        with pytest.raises(ValueError):
            backend.image_resolution()
        backend._image_resolution = resolution(a)
        copied = backend.image_resolution()
        copied["resolved_image"] = "tamper"
        assert backend.image_resolution() == resolution(a)
    finally:
        backend.session.close()


@pytest.mark.parametrize("defect", ["different_config", "different_child", "missing", "read_failure"])
def test_backend_requires_two_equal_fresh_receipts_without_retry(monkeypatch, defect):
    a = approval(controller_sha256=hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest())
    backend = adapter.Backend(a, Path.cwd())
    monkeypatch.setattr(adapter, "verify_checkout", lambda *_: None)
    values = {"auth": [{"account": core.ACCOUNT}],
              "operator": {"bindings": [{"role": "roles/owner", "members": ["user:" + core.ACCOUNT]}]},
              "jobs": [], "executions": [], "project": {"projectId": core.PROJECT, "projectNumber": NAMESPACE}}
    monkeypatch.setattr(backend, "metadata", lambda kind, *_: deepcopy(values[kind]))
    calls = []
    def verify(*args, **kwargs):
        calls.append(kwargs)
        p = resolution(a)
        if len(calls) == 2:
            if defect == "different_config":
                p["config_digest"] = "sha256:" + "f" * 64
            elif defect == "different_child":
                p["resolved_image"] = BASE + "@sha256:" + "f" * 64
            elif defect == "missing":
                return None
            else:
                raise TimeoutError("synthetic metadata timeout")
        return p
    monkeypatch.setattr(adapter, "verify_image", verify)
    try:
        with pytest.raises((ValueError, TimeoutError)):
            backend.preflight(a)
        assert calls == [{"include_resolution": True}] * 2
        with pytest.raises(ValueError):
            backend.image_resolution()
        assert not backend.created and not backend.executed
    finally:
        backend.session.close()


def test_controller_freezes_receipt_before_backend_alias_mutation(tmp_path):
    a = approval().model_copy(update={"root": str(tmp_path)})
    (tmp_path / "approval.json").write_text("{}")
    b = FakeBackend(a)
    proof = resolution(a)
    wanted = deepcopy(proof)
    b.image_resolution = lambda: proof
    original_create, original_execution = b.create, b.execution
    def create(body):
        proof["resolved_image"] = BASE + "@sha256:" + "f" * 64
        return original_create(body)
    def execution(name):
        value = original_execution(name)
        value["spec"]["template"]["spec"]["containers"][0]["image"] = wanted["resolved_image"]
        return value
    b.create, b.execution = create, execution
    records = {}
    core.run(a, b, claim=lambda path, value: records.setdefault(path, deepcopy(value)))
    assert records[tmp_path / "image-resolution.json"] == wanted
    assert tmp_path / "result.json" in records
