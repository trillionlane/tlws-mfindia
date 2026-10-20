"""Actual guard plus exact generated prefix; no PostgreSQL/source/cloud request."""

import ast
from pathlib import Path
from types import SimpleNamespace
import textwrap

import pytest

from mfdataindia.review import clone_checkpoint_v2 as cp
from mfdataindia.review.clone_final_snapshot import DUMP_START, DUMP_EXPORT, create_rehearsal_output


@pytest.mark.parametrize("old_mode", [True, False])
def test_exact_old_readwrite_failure_and_repaired_guard_with_unchanged_catalogue(old_mode, tmp_path):
    calls = []

    class Connection:
        info = SimpleNamespace(host="127.0.0.1", port=55540,
                               dbname="mfdata_fs13_final_image_261020h")
        readonly = False

        def execute(self, query):
            calls.append(query)
            if query == "SET TRANSACTION READ ONLY":
                self.readonly = True
                return None
            if query == "SELECT pg_export_snapshot() AS snapshot":
                return SimpleNamespace(fetchone=lambda: {"snapshot": "owned-snapshot"})
            if isinstance(query, str) and "current_setting('transaction_isolation')" in query:
                return SimpleNamespace(fetchone=lambda: {
                    "isolation": "repeatable read", "readonly": "on" if self.readonly else "off"})
            assert self.readonly
            return SimpleNamespace(fetchall=lambda: [])

    conn = Connection()
    started = []

    def popen(argv, **kwargs):
        started.append((argv, kwargs))
        return "child-fixture"

    source = DUMP_START
    if old_mode:
        source = source.replace("conn.execute('SET TRANSACTION READ ONLY')", "pass # former G")
    environment = {"conn": conn, "time": SimpleNamespace(monotonic=lambda: 1),
                   "subprocess": SimpleNamespace(Popen=popen, PIPE=-1),
                   "CLONE_ROOT": tmp_path, "pg_env": {"fixture": "not-a-secret"}}
    exec(compile(textwrap.dedent(source), "<owned-prefix-fixture>", "exec"), environment)
    assert len(started) == 1
    assert started[0][0] == ["pg_dump", "--format=custom", "--snapshot=owned-snapshot",
                             "--file=" + str(tmp_path / "rehearsal-export/checkpoint.dump")]
    if old_mode:
        with pytest.raises(ValueError, match="checkpoint metadata requires RR READONLY"):
            cp.logical_metadata(conn)
    else:
        result = cp.logical_metadata(conn)
        assert result and all(value == [] for value in result.values())
        assert calls[0] == "SET TRANSACTION READ ONLY"


def test_prefix_has_no_caller_commit_writer_or_budget_change_and_native_uses_same_bytes():
    tree = ast.parse(textwrap.dedent(DUMP_START))
    methods = [node.func.attr for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
    assert "commit" not in methods and "rollback" not in methods
    assert len(tree.body) == 6  # mode, shared fresh workspace, snapshot/time/dump
    smoke = (Path(__file__).parents[1] / "scripts/smoke_clone_checkpoint_v2.py").read_text()
    assert "from mfdataindia.review.clone_final_snapshot import DUMP_START" in smoke
    assert 'exec(compile(textwrap.dedent(DUMP_START)' in smoke
    assert 'exec(compile(textwrap.dedent(DUMP_EXPORT)' in smoke
    assert '"checkpoint metadata requires RR READONLY"' in smoke
    assert 'child.communicate(timeout=240)' in smoke
    assert 'child.communicate(timeout=15)' in smoke


@pytest.mark.parametrize("kind", ["directory", "symlink", "broken_symlink", "file"])
def test_existing_output_never_reused_or_deleted(tmp_path, kind):
    output = tmp_path / "rehearsal-export"
    if kind == "directory":
        output.mkdir()
        (output / "evidence").write_bytes(b"retained")
    elif kind == "file":
        output.write_bytes(b"retained")
    else:
        target = tmp_path / ("missing" if kind == "broken_symlink" else "input")
        if kind == "symlink":
            target.mkdir()
        output.symlink_to(target)
    with pytest.raises(FileExistsError):
        create_rehearsal_output(tmp_path)
    assert output.exists() or output.is_symlink()


def test_fresh_private_output_preserves_incoming_files(tmp_path):
    for name in ("checkpoint.dump", "checkpoint-state.json"):
        (tmp_path / name).write_bytes(b"retained-input")
    output = create_rehearsal_output(tmp_path)
    assert output.stat().st_mode & 0o777 == 0o700
    assert not list(output.iterdir())
    assert all((tmp_path / name).read_bytes() == b"retained-input"
               for name in ("checkpoint.dump", "checkpoint-state.json"))


def test_shared_export_uses_only_fresh_output_not_input():
    tree = ast.parse(textwrap.dedent(DUMP_EXPORT))
    call = tree.body[0].value
    assert isinstance(call.args[2], ast.Name) and call.args[2].id == "checkpoint_output"
    assert "CLONE_ROOT" not in DUMP_EXPORT
