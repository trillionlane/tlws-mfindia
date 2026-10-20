"""Actual guard plus exact generated prefix; no PostgreSQL/source/cloud request."""

import ast
from pathlib import Path
from types import SimpleNamespace
import textwrap

import pytest

from mfdataindia.review import clone_checkpoint_v2 as cp
from mfdataindia.review.clone_final_snapshot import DUMP_START


@pytest.mark.parametrize("old_mode", [True, False])
def test_exact_old_readwrite_failure_and_repaired_guard_with_unchanged_catalogue(old_mode):
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
                   "CLONE_ROOT": Path("/fixture-only"), "pg_env": {"fixture": "not-a-secret"}}
    exec(compile(textwrap.dedent(source), "<owned-prefix-fixture>", "exec"), environment)
    assert len(started) == 1
    assert started[0][0] == ["pg_dump", "--format=custom", "--snapshot=owned-snapshot",
                             "--file=/fixture-only/checkpoint.dump"]
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
    assert len(tree.body) == 4  # explicit mode, export, timestamp, bounded-job dump
    smoke = (Path(__file__).parents[1] / "scripts/smoke_clone_checkpoint_v2.py").read_text()
    assert "from mfdataindia.review.clone_final_snapshot import DUMP_START" in smoke
    assert 'exec(compile(textwrap.dedent(DUMP_START)' in smoke
    assert '"checkpoint metadata requires RR READONLY"' in smoke
    assert 'child.communicate(timeout=240)' in smoke
    assert 'child.communicate(timeout=15)' in smoke
