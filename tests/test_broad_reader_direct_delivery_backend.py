"""Provider-free exact-command and no-retry adapter regression tests."""

from copy import deepcopy

import pytest

from mfdataindia.review import broad_reader_direct_delivery_backend as backend
from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_direct_rollout_controller as rollout


GIT = "a" * 40
APP = {"image": spec.IMAGE_PREFIX + "b" * 64,
       "resolved_image": spec.IMAGE_PREFIX + "b" * 64,
       "config_digest": "sha256:" + "c" * 64,
       "git_sha": GIT, "platform": "linux/amd64", "layers_downloaded": 0}
TOOLS = {**APP, "image": APP["image"].replace(
    "/mfdataindia@", "/mfdataindia-review-tools@"),
    "resolved_image": APP["resolved_image"].replace(
        "/mfdataindia@", "/mfdataindia-review-tools@")}


def _backend(kind="capture"):
    approval = {"kind": kind, "final_git_sha": GIT,
                "image_binding": {"app": deepcopy(APP), "tools": deepcopy(TOOLS)},
                "predecessor": None}
    prepared = {"body": b"{\"fixed\":true}"}
    return backend.Backend(approval, "/tmp/synthetic-not-used", prepared)


@pytest.mark.parametrize("kind", ("capture", "grant", "reader"))
def test_only_three_fixed_target_metadata_names_and_no_secret_operations(kind):
    name = spec.ROLES[kind].job
    assert "--filter=metadata.name=" + name in backend.metadata_arguments(
        "jobs", role=kind)
    assert "--job=" + name in backend.metadata_arguments(
        "executions", role=kind)
    assert "--region=" + spec.REGION in backend.metadata_arguments("job", name)
    assert backend.metadata_arguments("logs", name + "-abcde", role=kind)[0] == "logging"
    for operation, target in (("secret", name), ("job", "other-job"),
                              ("execution", "other-execution")):
        with pytest.raises(ValueError):
            backend.metadata_arguments(operation, target, role=kind)


def test_create_and_execute_are_exact_single_use_before_http_ambiguity(monkeypatch):
    value = _backend("grant")
    posts = []

    class Response:
        status_code = 201

        def json(self):
            return {"fixed": True}

    def post(url, *, data, headers, timeout, allow_redirects):
        posts.append((url, data))
        assert timeout == 30 and allow_redirects is False
        assert headers["Content-Type"] == "application/json"
        return Response()

    monkeypatch.setattr(value, "command", lambda *args: "synthetic-token")
    monkeypatch.setattr(value.session, "post", post)
    try:
        assert value.create("grant", value.prepared["body"]) == {"fixed": True}
        with pytest.raises(ValueError):
            value.create("grant", value.prepared["body"])
        assert value.execute("grant") == {"fixed": True}
        with pytest.raises(ValueError):
            value.execute("grant")
        assert len(posts) == 2
        assert posts[0][0] == backend.BASE_URL + "/jobs"
        assert posts[1][0].endswith("/" + spec.ROLES["grant"].job + ":run")
    finally:
        value.session.close()


def test_failed_http_still_consumes_create_url_and_never_retries(monkeypatch):
    value = _backend()

    class Response:
        status_code = 503

    attempts = []
    monkeypatch.setattr(value, "command", lambda *args: "synthetic-token")
    monkeypatch.setattr(value.session, "post",
                        lambda *_args, **_kwargs: (attempts.append(1), Response())[1])
    try:
        with pytest.raises(RuntimeError, match="NO_RETRY"):
            value.create("capture", value.prepared["body"])
        with pytest.raises(ValueError):
            value.create("capture", value.prepared["body"])
        assert len(attempts) == 1
    finally:
        value.session.close()


def test_capture_preflight_requires_all_three_absent_and_both_images_twice(monkeypatch):
    value = _backend("capture")
    checked = []
    monkeypatch.setattr(backend, "verify_checkout",
                        lambda repo, sha: checked.append((str(repo), sha)))

    def metadata(kind, name=None, *, role=None):
        if kind == "auth":
            return [{"account": spec.OPERATOR}]
        if kind == "operator":
            return {"bindings": [{"role": "roles/owner", "condition": None,
                                  "members": ["user:" + spec.OPERATOR]}]}
        if kind == "project":
            return {"projectId": spec.PROJECT,
                    "projectNumber": spec.PROJECT_NUMBER}
        if kind in ("jobs", "executions"):
            return []
        pytest.fail("unexpected metadata")

    monkeypatch.setattr(value, "metadata", metadata)

    def image(_cloud, image, git, *, include_resolution):
        checked.append((image, git, include_resolution))
        return deepcopy(APP if image == APP["image"] else TOOLS)

    monkeypatch.setattr(backend, "verify_image", image)
    try:
        value.preflight(value.approval, value.prepared)
        assert value.image("app") == APP and value.image("tools") == TOOLS
        assert len(checked) == 5  # Exact checkout, two APP and two TOOLS.
    finally:
        value.session.close()


@pytest.mark.parametrize("kind,completed", (
    ("grant", ("capture",)),
    ("reader", ("capture", "grant")),
))
def test_later_preflight_requires_exact_prior_job_and_sole_execution(
        monkeypatch, kind, completed):
    value = _backend(kind)
    previous = {}
    for role in completed:
        job = spec.ROLES[role].job
        previous[role] = {
            "kind": role, "approval": {"kind": role},
            "prepared": {"exact": role},
            "completion": {
                "job": {"metadata": {"name": job}},
                "execution": {"metadata": {"name": job + "-abcde"}}}}
    def predecessor(document, *, source_root):
        return previous[completed[-1]] if document is value.approval else previous["capture"]

    monkeypatch.setattr(rollout, "predecessor", predecessor)
    monkeypatch.setattr(backend, "verify_checkout", lambda *_: None)
    monkeypatch.setattr(spec, "validate_job_export", lambda role, *_a, **_k: {
        "job_uid": role + "-uid"})
    monkeypatch.setattr(spec, "validate_execution_export", lambda role, *_a, **_k: {
        "name": spec.ROLES[role].job + "-abcde"})
    monkeypatch.setattr(backend, "verify_image",
                        lambda _cloud, image, _git, **_kw: deepcopy(
                            APP if image == APP["image"] else TOOLS))
    def metadata(operation, name=None, *, role=None):
        if operation == "auth":
            return [{"account": spec.OPERATOR}]
        if operation == "operator":
            return {"bindings": [{"role": "roles/owner", "condition": None,
                                  "members": ["user:" + spec.OPERATOR]}]}
        if operation == "project":
            return {"projectId": spec.PROJECT, "projectNumber": spec.PROJECT_NUMBER}
        if operation == "jobs":
            return [deepcopy(previous[role]["completion"]["job"])] if role in completed else []
        if operation == "executions":
            return [deepcopy(previous[role]["completion"]["execution"])] if role in completed else []
        if operation == "job":
            return deepcopy(previous[next(r for r in completed if spec.ROLES[r].job == name)]
                            ["completion"]["job"])
        if operation == "execution":
            return deepcopy(previous[role]["completion"]["execution"])
        pytest.fail("unexpected metadata operation")

    monkeypatch.setattr(value, "metadata", metadata)
    try:
        value.preflight(value.approval, value.prepared)
        assert value.image("app") == APP
        # The old all-three-absent rule must reject this valid later stage.
        original = value.metadata
        monkeypatch.setattr(value, "metadata", lambda op, name=None, *, role=None:
                            [] if op == "executions" and role == "capture"
                            else original(op, name, role=role))
        with pytest.raises(ValueError, match="EXACT_SOLE_DIRECT_PREDECESSOR_REQUIRED"):
            value.preflight(value.approval, value.prepared)
    finally:
        value.session.close()
