"""Current fixed metadata/preflight/HTTP adapter, fake transports only."""
from copy import deepcopy

import pytest

from mfdataindia.review import reader_execution_backend as current
from mfdataindia.review import broad_reader_direct_delivery_backend as old
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import reader_execution_contract as contract
from reader_execution_fixtures import plan, ROOT, GIT


@pytest.mark.parametrize("kind", contract.KINDS)
def test_metadata_can_only_read_current_fixed_resources_and_protected_baseline(kind):
    value, _, _ = plan()
    op = value["operation_id"]
    role = spec.role_for(kind, operation_id=op)
    assert "--filter=metadata.name=" + role.job in old.metadata_arguments("jobs", role=kind, operation_id=op)
    assert "--job=" + role.job in old.metadata_arguments("executions", role=kind, operation_id=op)
    assert old.metadata_arguments("logs", role.job + "-abcde", role=kind, operation_id=op)[0] == "logging"
    for key, target in (("job", spec.ROLES[kind].job), ("job", "other-job"),
                         ("execution", "consumed-job-abcde"), ("secret", role.secret)):
        with pytest.raises(ValueError):
            old.metadata_arguments(key, target, role=kind, operation_id=op)


def test_current_backend_requires_own_complete_preflight_before_any_http(monkeypatch):
    value, _, _ = plan()
    prepared = contract.prepare_request(value, "capture", [], source_root=ROOT, expected_git=GIT)
    adapter = current.Backend(value, "capture", ROOT, prepared)
    calls = []
    monkeypatch.setattr(adapter.session, "post", lambda *_a, **_k: pytest.fail("no HTTP before preflight"))
    with pytest.raises(ValueError, match="CURRENT_OPERATION_PREFLIGHT_REQUIRED"):
        adapter.create("capture", prepared["body"])
    assert adapter.created is True and not adapter.executed
    # Parent legacy preflight must not be a route around current mode.
    with pytest.raises(ValueError, match="CURRENT_OPERATION_PREFLIGHT_REQUIRED"):
        old.Backend.preflight(adapter, adapter.approval, prepared)
    monkeypatch.setattr(current, "verify_checkout", lambda *_: calls.append("checkout"))
    def metadata(kind, name=None, *, role=None):
        calls.append((kind, name, role))
        if kind == "auth":
            return [{"account": spec.OPERATOR}]
        if kind == "operator":
            return {"bindings": [{"role": "roles/owner", "members": ["user:" + spec.OPERATOR]}]}
        if kind == "project":
            return {"projectId": spec.PROJECT, "projectNumber": spec.PROJECT_NUMBER}
        if kind in ("jobs", "executions", "programme_jobs"):
            return []
        pytest.fail("unexpected metadata")
    monkeypatch.setattr(adapter, "metadata", metadata)
    monkeypatch.setattr(adapter, "protected", lambda: {"fixture": True})
    resolved = []
    def image(_adapter, image, sha, *, include_resolution):
        assert sha == GIT and include_resolution is True
        resolved.append(image)
        return deepcopy(next(binding for binding in value["image_binding"].values() if binding["image"] == image))
    monkeypatch.setattr(current, "verify_image", image)
    try:
        adapter.preflight(value, prepared, {})
        assert adapter.current_preflight_verified
        assert len(resolved) == 4
        assert all(sum(row[0] == "jobs" and row[2] == kind for row in calls if type(row) is tuple) == 1
                   for kind in contract.KINDS)
        assert adapter.image("app") == value["image_binding"]["app"]
    finally:
        adapter.session.close()


def test_inspector_exposes_only_read_only_operations():
    value, _, _ = plan()
    prepared = contract.prepare_request(value, "capture", [], source_root=ROOT, expected_git=GIT)
    inspector = current.Inspector(value, "capture", ROOT, prepared)
    try:
        assert all(not hasattr(inspector, name) for name in ("create", "execute", "_post", "command", "run", "rollback", "commit"))
    finally:
        inspector._adapter.session.close()


def test_new_nonce_cannot_hide_unresolved_cloud_programme_without_local_records(monkeypatch):
    value, _, _ = plan()
    prepared = contract.prepare_request(value, "capture", [], source_root=ROOT, expected_git=GIT)
    adapter = current.Backend(value, "capture", ROOT, prepared)
    monkeypatch.setattr(current, "verify_checkout", lambda *_: None)
    def metadata(kind, name=None, *, role=None):
        if kind == "auth":
            return [{"account": spec.OPERATOR}]
        if kind == "operator":
            return {"bindings": [{"role": "roles/owner", "members": ["user:" + spec.OPERATOR]}]}
        if kind == "project":
            return {"projectId": spec.PROJECT, "projectNumber": spec.PROJECT_NUMBER}
        if kind in ("jobs", "executions"):
            return []
        if kind == "programme_jobs":
            return [{"metadata": {"name": "mf-reader-internal-grant-op-fedcba9876543210"}}]
        pytest.fail("unexpected metadata")
    monkeypatch.setattr(adapter, "metadata", metadata)
    monkeypatch.setattr(adapter, "protected", lambda: {"fixture": True})
    monkeypatch.setattr(current, "verify_image", lambda _adapter, image, sha, **kw:
        deepcopy(next(b for b in value["image_binding"].values() if b["image"] == image)))
    monkeypatch.setattr(adapter.session, "post", lambda *_a, **_k: pytest.fail("no mutation"))
    try:
        with pytest.raises(ValueError, match="PROGRAMME_INVENTORY_REQUIRES_RECONCILIATION"):
            adapter.preflight(value, prepared, {})
        assert not adapter.created and not adapter.executed
    finally:
        adapter.session.close()


@pytest.mark.parametrize("fault", ["extra_job", "missing_job", "duplicate_job", "changed_uid", "extra_execution", "unstable", "truncated"])
def test_programme_inventory_is_complete_stable_and_bidirectional(fault, monkeypatch):
    from mfdataindia.review.policy import fingerprint
    value, _, _ = plan()
    prepared = contract.prepare_request(value, "capture", [], source_root=ROOT, expected_git=GIT)
    adapter = current.Backend(value, "capture", ROOT, prepared)
    name = "mf-reader-internal-capture-op-fedcba9876543210"
    original = {"metadata": {"name": name, "uid": "a" * 16}}
    adapter._expected_programme = {name: {"job_sha256": fingerprint(original), "execution_sha256": fingerprint([])}}
    calls = []
    def metadata(kind, selected=None, **kwargs):
        calls.append(kind)
        if kind == "programme_jobs":
            if fault == "missing_job" or fault == "unstable" and calls.count(kind) == 2:
                return []
            if fault == "extra_job":
                return [original, {"metadata": {"name": "mf-reader-internal-grant-op-abcdef0123456789"}}]
            if fault == "duplicate_job":
                return [original, original]
            if fault == "truncated":
                return [original] * 1001
            return [original]
        assert selected == name
        if kind == "programme_job":
            return {"metadata": {"name": name, "uid": "b" * 16}} if fault == "changed_uid" else original
        if kind == "programme_executions":
            return [{"metadata": {"name": name + "-abcde"}}] if fault == "extra_execution" else []
        pytest.fail("unexpected metadata")
    monkeypatch.setattr(adapter, "metadata", metadata)
    try:
        with pytest.raises(ValueError, match="PROGRAMME_INVENTORY_REQUIRES_RECONCILIATION"):
            adapter.recheck_programme_inventory()
        assert not adapter.created and not adapter.executed
    finally:
        adapter.session.close()
