"""Complete local transport lifecycle and H collision gates; no provider/PG/source."""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import textwrap
from types import SimpleNamespace

import pytest

from mfdataindia.review import clone_checkpoint_v2 as cp
from mfdataindia.review.clone_final_snapshot import DUMP_START, DUMP_EXPORT
from test_clone_checkpoint import Storage
from test_clone_checkpoint_v2 import context


def prepared(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "checkpoint.dump").write_bytes(b"synthetic-acquisition")
    storage = Storage()
    state = {"original": {"aum": "4094.35", "integer": 2**70}}
    seals = {"complete_source": "synthetic", "catalogue": ["all"]}
    certificate = cp.export_checkpoint(storage, "fixture", source, context(), state, seals)
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    cp.import_checkpoint(storage, "fixture", incoming, context(), certificate)
    return storage, state, seals, certificate, incoming


def hashes(directory):
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ("checkpoint.dump", "checkpoint-state.json")}


def test_H_import_export_same_directory_fails_before_any_upload(tmp_path):
    storage, state, seals, first, incoming = prepared(tmp_path)
    before, calls = hashes(incoming), len(storage.calls)
    with pytest.raises(FileExistsError):
        cp.export_checkpoint(storage, "fixture", incoming, context("rehearsal"), state, seals)
    assert hashes(incoming) == before and len(storage.calls) == calls
    assert not any("-rehearsal-" in name for name in storage.objects)


def test_shared_dump_and_export_real_transport_chain_preserves_inputs(tmp_path):
    storage, state, seals, first, incoming = prepared(tmp_path)
    before = hashes(incoming)
    bound = {**context("rehearsal"), "previous_checkpoint_sha256": cp.digest(first),
             "previous_execution_sha256": "9" * 64}
    calls = []

    def execute(query):
        calls.append(query)
        return SimpleNamespace(fetchone=lambda: {"snapshot": "synthetic-snapshot"})

    def popen(argv, **kwargs):
        assert argv[:3] == ["pg_dump", "--format=custom", "--snapshot=synthetic-snapshot"]
        path = Path(argv[3].removeprefix("--file="))
        assert path == incoming / "rehearsal-export/checkpoint.dump"
        with path.open("xb") as handle:
            handle.write(b"synthetic-rehearsal-same-snapshot")
        return SimpleNamespace(returncode=0)

    owned = {"conn": SimpleNamespace(execute=execute),
             "time": SimpleNamespace(monotonic=lambda: 1),
             "subprocess": SimpleNamespace(Popen=popen, PIPE=-1),
             "CLONE_ROOT": incoming, "pg_env": {"fixture": "only"},
             "cp": cp, "session": storage,
             "identity": SimpleNamespace(json=lambda: {"access_token": "fixture"}),
             "NEXT_CHECKPOINT_CONTEXT": bound, "checkpoint_state": state,
             "checkpoint_logical": seals}
    exec(compile(textwrap.dedent(DUMP_START), "<actual-shared-dump>", "exec"), owned)
    assert calls[0] == "SET TRANSACTION READ ONLY"
    exec(compile(textwrap.dedent(DUMP_EXPORT), "<actual-shared-export>", "exec"), owned)
    second = owned["checkpoint"]
    third = tmp_path / "third"
    third.mkdir()
    observed = cp.import_checkpoint(storage, "fixture", third, bound, second)
    assert observed["state"] == state and observed["logical_seals"] == seals
    assert (third / "checkpoint.dump").read_bytes() == b"synthetic-rehearsal-same-snapshot"
    assert hashes(incoming) == before
    assert second["context_sha256"] == cp.digest(bound)
    assert [row[0] for row in storage.calls] == ["post", "post", "get", "get"] * 2
    assert all(row[2]["params"]["ifGenerationMatch"] == "0"
               for row in storage.calls if row[0] == "post")
    for defect in ("previous_checkpoint_sha256", "previous_execution_sha256"):
        wrong = deepcopy(bound)
        wrong[defect] = "8" * 64
        with pytest.raises(ValueError):
            cp.verify_certificate(wrong, second)


@pytest.mark.parametrize("defect", ["existing_state", "state_symlink", "broken_state_symlink",
                                  "dump_symlink", "directory_symlink", "missing_dump", "oversized",
                                  "nonfinite_state", "invalid_seals"])
def test_known_local_defects_fail_before_upload(tmp_path, monkeypatch, defect):
    output = tmp_path / "output"
    output.mkdir()
    dump = output / "checkpoint.dump"
    if defect != "missing_dump":
        dump.write_bytes(b"synthetic")
    state_path = output / "checkpoint-state.json"
    state, seals = {"original": "retained"}, {"all": "retained"}
    if defect == "existing_state":
        state_path.write_bytes(b"retained-input")
    elif defect in ("state_symlink", "broken_state_symlink"):
        target = tmp_path / "input"
        if defect == "state_symlink":
            target.write_bytes(b"retained-input")
        state_path.symlink_to(target)
    elif defect == "dump_symlink":
        # A distinct fixture, never deleting or rewriting a real checkpoint.
        other = tmp_path / "dump-link-output"
        other.mkdir()
        (other / "checkpoint.dump").symlink_to(dump)
        output = other
    elif defect == "directory_symlink":
        link = tmp_path / "output-link"
        link.symlink_to(output)
        output = link
    elif defect == "oversized":
        monkeypatch.setattr(cp, "MAX_STATE", 32)
    elif defect == "nonfinite_state":
        state = {"invalid": float("inf")}
    elif defect == "invalid_seals":
        seals = {}
    storage = Storage()
    with pytest.raises((FileExistsError, ValueError)):
        cp.export_checkpoint(storage, "fixture", output, context(), state, seals)
    assert storage.calls == []
    if defect == "existing_state":
        assert state_path.read_bytes() == b"retained-input"
    if defect == "state_symlink":
        assert state_path.read_bytes() == b"retained-input"


@pytest.mark.parametrize("failed_post", [1, 2])
def test_transport_failure_never_retries_or_reuses_local_partial(tmp_path, failed_post):
    source = tmp_path / "source"
    source.mkdir()
    (source / "checkpoint.dump").write_bytes(b"synthetic")
    storage = Storage()
    original_post = storage.post
    attempts = []

    def post(url, **kwargs):
        attempts.append(url)
        if len(attempts) == failed_post:
            raise RuntimeError("synthetic uncertain upload")
        return original_post(url, **kwargs)

    storage.post = post
    with pytest.raises(RuntimeError, match="synthetic uncertain upload"):
        cp.export_checkpoint(storage, "fixture", source, context(), {"all": 1}, {"all": 1})
    assert len(attempts) == failed_post
    assert (source / "checkpoint-state.json").exists()
    with pytest.raises(FileExistsError):
        cp.export_checkpoint(storage, "fixture", source, context(), {"all": 1}, {"all": 1})
    assert len(attempts) == failed_post


def test_native_smoke_fixture_runs_real_transport_without_network(tmp_path):
    path = Path(__file__).parents[1] / "scripts/smoke_clone_checkpoint_v2.py"
    spec = importlib.util.spec_from_file_location("checkpoint_native_fixture", path)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    storage = smoke.FixtureTransport()
    output = tmp_path / "output"
    output.mkdir()
    (output / "checkpoint.dump").write_bytes(b"synthetic-native-transport")
    bound = smoke.fixture_context()
    cert = cp.export_checkpoint(storage, "fixture", output, bound, {"all": 1}, {"all": 1})
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    observed = cp.import_checkpoint(storage, "fixture", incoming, bound, cert)
    assert observed["state"] == {"all": 1} and storage.posts == storage.gets == 2
    source = path.read_text()
    assert source.count("cp.import_checkpoint(") == 2
    assert 'exec(compile(textwrap.dedent(DUMP_EXPORT)' in source
    assert '"CLONE_ROOT": second_root' in source and "second_root = incoming" in source
    assert 'input_hashes != {name: hashlib_file(incoming / name)' in source
    assert 'transport.posts != 4 or transport.gets != 4' in source
