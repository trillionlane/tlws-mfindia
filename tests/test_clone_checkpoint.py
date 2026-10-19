"""Synthetic checkpoint transport/tamper controls; never live coverage evidence."""

import base64
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from mfdataindia.review import clone_checkpoint as cp


def meta(name, raw):
    return {
        "uri": "gs://" + cp.BUCKET + "/" + name,
        "generation": "123456789",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "md5": base64.b64encode(hashlib.md5(raw, usedforsecurity=False).digest()).decode(),
    }


def context():
    return {
        "format": "mfdata-disposable-clone-checkpoint-v1",
        "manifest_sha256": "a" * 64,
        "backup": meta("verified-review-rollouts/dev-uti-gap-pre-261019f/snapshot.dump", b"backup"),
        "app_image": "asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia@sha256:"
        + "b" * 64,
        "tools_image": "asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia-review-tools@sha256:"
        + "c" * 64,
        "git_sha": "d" * 40,
        "helper_hashes": {"source_rehearsal.py": "e" * 64},
        "reference_sha256": "f" * 64,
        "checkpoint_prefix": "verified-review-rollouts/dev-uti-gap-261019f-clone-checkpoint/",
    }


class Response:
    def __init__(self, raw, document=None, status=200):
        self.raw, self.document, self.status_code = raw, document, status
        self.closed = False

    def json(self):
        return self.document

    def iter_content(self, size):
        for start in range(0, len(self.raw), size):
            yield self.raw[start : start + size]

    def close(self):
        self.closed = True


class Storage:
    def __init__(self):
        self.objects, self.calls, self.responses = {}, [], []

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        assert kwargs["allow_redirects"] is False and kwargs["timeout"] == 180
        assert kwargs["params"]["ifGenerationMatch"] == "0"
        name = kwargs["params"]["name"]
        if name in self.objects:
            return Response(b"", status=412)
        raw = kwargs["data"].read()
        metadata = meta(name, raw)
        assert kwargs["headers"]["Content-MD5"] == metadata["md5"]
        self.objects[name] = raw
        return Response(
            b"",
            {
                "bucket": cp.BUCKET,
                "name": name,
                "size": str(len(raw)),
                "generation": metadata["generation"],
                "md5Hash": metadata["md5"],
            },
        )

    def get(self, url, **kwargs):
        from urllib.parse import unquote

        self.calls.append(("get", url, kwargs))
        assert kwargs["params"] == {"alt": "media", "generation": "123456789"}
        assert kwargs["allow_redirects"] is False
        name = unquote(url.split("/o/", 1)[1])
        response = Response(self.objects[name])
        self.responses.append(response)
        return response


def prepare(tmp_path):
    source = tmp_path / "phase1"
    source.mkdir()
    (source / "checkpoint.dump").write_bytes(b"synthetic-not-native-pgdump")
    storage = Storage()
    state = {
        "preserved": {"codes": [1, 2], "sha256": "a" * 64},
        "references": [{"code": 1, "values": {"aum": "4094.35"}}],
        "duration": 1.25,
        "none": None,
        "unicode": "₹",
        "integer": 2**63,
    }
    seals = {"source": {"rows": 2, "sha256": "b" * 64}, "roles": ["admin"]}
    certificate = cp.export_checkpoint(storage, "synthetic-token", source, context(), state, seals)
    return storage, certificate, state, seals


def test_create_only_and_pinned_generation_roundtrip(tmp_path):
    storage, certificate, state, seals = prepare(tmp_path)
    destination = tmp_path / "phase2"
    destination.mkdir()
    envelope = cp.import_checkpoint(storage, "synthetic-token", destination, context(), certificate)
    assert envelope["state"] == state and envelope["logical_seals"] == seals
    assert (destination / "checkpoint.dump").read_bytes() == b"synthetic-not-native-pgdump"
    assert [call[0] for call in storage.calls] == ["post", "post", "get", "get"]
    assert all(response.closed for response in storage.responses)
    assert cp.require_restored_seals(seals, deepcopy(seals))[
        "complete_checkpoint_logical_seals_matched"
    ]


@pytest.mark.parametrize(
    "change", ["manifest", "backup", "git", "image", "reference", "helper", "prefix"]
)
def test_changed_context_cannot_admit_old_certificate(tmp_path, change):
    storage, certificate, _, _ = prepare(tmp_path)
    changed = context()
    if change == "manifest":
        changed["manifest_sha256"] = "0" * 64
    elif change == "backup":
        changed["backup"]["sha256"] = "0" * 64
    elif change == "git":
        changed["git_sha"] = "0" * 40
    elif change == "image":
        changed["tools_image"] = changed["tools_image"][:-64] + "0" * 64
    elif change == "reference":
        changed["reference_sha256"] = "0" * 64
    elif change == "helper":
        changed["helper_hashes"]["source_rehearsal.py"] = "0" * 64
    else:
        changed["checkpoint_prefix"] = changed["checkpoint_prefix"].replace("261019", "261020")
    with pytest.raises(ValueError):
        cp.import_checkpoint(storage, "token", tmp_path, changed, certificate)
    assert len(storage.calls) == 2


@pytest.mark.parametrize(
    "key,value",
    [
        ("generation", "0"),
        ("generation", 123),
        ("size", True),
        ("size", 0),
        ("size", cp.MAX_DUMP + 1),
        ("md5", "bad"),
        ("sha256", "bad"),
        ("uri", "gs://another/private/snapshot.dump"),
    ],
)
def test_bad_object_metadata_rejected_without_read(tmp_path, key, value):
    storage, certificate, _, _ = prepare(tmp_path)
    certificate["dump"][key] = value
    with pytest.raises(ValueError):
        cp.import_checkpoint(storage, "token", tmp_path, context(), certificate)
    assert len(storage.calls) == 2


@pytest.mark.parametrize("filename", ["state.json", "snapshot.dump"])
@pytest.mark.parametrize("mode", ["changed", "short", "long"])
def test_tampered_transport_bytes_are_never_restored(tmp_path, filename, mode):
    storage, certificate, _, _ = prepare(tmp_path)
    name = context()["checkpoint_prefix"] + filename
    old = storage.objects[name]
    storage.objects[name] = (
        b"X" + old[1:] if mode == "changed" else old[:-1] if mode == "short" else old + b"X"
    )
    directory = tmp_path / "phase2"
    directory.mkdir()
    with pytest.raises(ValueError):
        cp.import_checkpoint(storage, "token", directory, context(), certificate)
    assert storage.responses[-1].closed


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'])
def test_ambiguous_or_nonfinite_json_rejected(raw):
    with pytest.raises(ValueError):
        cp.strict_json(raw)


@pytest.mark.parametrize(
    "value",
    [Decimal("1.23"), {1: "lost-key-type"}, {"x": b"bytes"}, {"x": float("inf")}, ("tuple",)],
)
def test_no_default_string_numeric_or_key_coercion(value):
    with pytest.raises(ValueError):
        cp.json_bytes(value)


@pytest.mark.parametrize("name", ["source", "roles", "views", "privileges", "extra"])
def test_no_subset_seal_or_metadata_drift_admitted(name):
    original = {
        "source": {"rows": 2, "sha256": "a" * 64},
        "roles": ["admin"],
        "views": "original",
        "privileges": ["SELECT"],
    }
    changed = deepcopy(original)
    if name == "extra":
        changed["extra"] = "unmodelled"
    else:
        changed[name] = "changed"
    with pytest.raises(ValueError):
        cp.require_restored_seals(original, changed)


def test_ambiguous_upload_is_attempted_once(tmp_path):
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "checkpoint.dump").write_bytes(b"synthetic")

    class Ambiguous(Storage):
        def post(self, *args, **kwargs):
            super().post(*args, **kwargs)
            raise TimeoutError("unknown outcome")

    storage = Ambiguous()
    with pytest.raises(TimeoutError):
        cp.export_checkpoint(
            storage, "token", directory, context(), {"state": True}, {"seal": True}
        )
    assert len(storage.calls) == 1 and len(storage.objects) == 1


def test_existing_checkpoint_never_overwritten(tmp_path):
    storage, _, _, _ = prepare(tmp_path)
    directory = tmp_path / "other-attempt"
    directory.mkdir()
    (directory / "checkpoint.dump").write_bytes(b"replacement")
    original = dict(storage.objects)
    with pytest.raises(RuntimeError):
        cp.export_checkpoint(
            storage, "token", directory, context(), {"state": True}, {"seal": True}
        )
    assert storage.objects == original and len(storage.calls) == 3


def test_download_failure_closes_response(tmp_path):
    storage, certificate, _, _ = prepare(tmp_path)
    original = storage.get

    def denied(*args, **kwargs):
        response = original(*args, **kwargs)
        response.status_code = 403
        return response

    storage.get = denied
    with pytest.raises(ValueError):
        cp.import_checkpoint(storage, "token", tmp_path, context(), certificate)
    assert len(storage.calls) == 3 and storage.responses[0].closed


def test_design_and_kernel_have_no_runtime_grant_or_automatic_activation():
    source = Path(cp.__file__).read_text()
    assert "add-iam-policy-binding" not in source and "book_projection.activate(" not in source
    design = json.loads(
        (
            Path(__file__).resolve().parents[1] / "docs/FS13_CHECKPOINTED_REHEARSAL_DESIGN.json"
        ).read_text()
    )
    assert "900seconds,2CPU4Gi" in design["profiles"]


@pytest.mark.parametrize(
    "change", ["state_shape", "missing_seal", "wrong_context", "wrong_dump", "extra"]
)
def test_even_authenticated_malformed_envelope_is_not_a_continuation(tmp_path, change):
    storage, certificate, _, _ = prepare(tmp_path)
    key = context()["checkpoint_prefix"] + "state.json"
    envelope = json.loads(storage.objects[key])
    if change == "state_shape":
        envelope["state"] = []
    elif change == "missing_seal":
        envelope["logical_seals"] = {}
    elif change == "wrong_context":
        envelope["context"]["helper_hashes"]["source_rehearsal.py"] = "0" * 64
    elif change == "wrong_dump":
        envelope["dump"]["sha256"] = "0" * 64
    else:
        envelope["extra"] = "unmodelled"
    raw = cp.json_bytes(envelope)
    storage.objects[key] = raw
    certificate["state"] = meta(key, raw)
    with pytest.raises(ValueError, match="complete state or source bindings"):
        cp.import_checkpoint(storage, "token", tmp_path, context(), certificate)
    assert [call[0] for call in storage.calls] == ["post", "post", "get"]


@pytest.mark.parametrize(
    "host,port,name",
    [
        ("/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev", 55540, "mfdataindia"),
        ("remote", 55540, "mfdata_fs13_final_image_261019f"),
        ("127.0.0.1", 5432, "mfdata_fs13_final_image_261019f"),
        ("127.0.0.1", 55540, "mfdata_fs13_final_image_261019e"),
    ],
)
def test_metadata_guard_rejects_live_or_failed_target_before_sql(host, port, name):
    from types import SimpleNamespace

    conn = SimpleNamespace(info=SimpleNamespace(host=host, port=port, dbname=name))
    with pytest.raises(ValueError, match="never live"):
        cp.logical_metadata(conn)


def test_metadata_requires_independent_readonly_snapshot_and_captures_complete_catalogue():
    from types import SimpleNamespace

    class Conn:
        info = SimpleNamespace(
            host="127.0.0.1", port=55540, dbname="mfdata_fs13_final_image_261019f"
        )

        def __init__(self, readonly):
            self.queries, self.readonly = [], readonly

        def execute(self, query):
            self.queries.append(query)
            return self

        def fetchone(self):
            return {"isolation": "repeatable read", "readonly": self.readonly}

        def fetchall(self):
            return []

    denied = Conn("off")
    with pytest.raises(ValueError, match="RR READONLY"):
        cp.logical_metadata(denied)
    assert len(denied.queries) == 1
    conn = Conn("on")
    evidence = cp.logical_metadata(conn)
    assert set(evidence) == {
        "roles",
        "memberships",
        "database",
        "defaults",
        "schemas",
        "relations",
        "columns",
        "views",
        "functions",
        "triggers",
        "policies",
        "settings",
        "indexes",
        "constraints",
    }
    assert all(query.lstrip().startswith("SELECT") for query in conn.queries)
    assert not any("LIMIT" in query for query in conn.queries)
