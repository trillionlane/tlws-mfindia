"""CI partitions execute the exact full collection once, without skip injection."""

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = spec_from_file_location("ci_partition", Path(__file__).parents[1] / "scripts/run_ci_tests.py")
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
Partition = _module.Partition


class Item:
    def __init__(self, nodeid, postgres=False):
        self.nodeid = nodeid
        self.postgres = postgres

    def get_closest_marker(self, name):
        return object() if name == "postgres" and self.postgres else None


def collect(plugin, items):
    dropped = []
    config = SimpleNamespace(hook=SimpleNamespace(
        pytest_deselected=lambda *, items: dropped.extend(items),
    ))
    selected = list(items)
    plugin.pytest_collection_modifyitems(config, selected)
    return [i.nodeid for i in selected], [i.nodeid for i in dropped]


def population():
    return [
        Item("tests/test_uti_v2_store.py::test_old", True),
        Item("tests/test_selected_value_preservation.py::test_new", True),
        Item("tests/test_selected_value_preservation.py::test_unit"),
        Item("tests/test_other.py::test_pg[param]", True),
        Item("tests/test_other.py::test_unit"),
    ]


def test_partitions_are_disjoint_and_exhaustive_and_preserve_order():
    items = population()
    focused = Partition("focused")
    early, later = collect(focused, items)
    remainder = Partition("remainder", {"exit_code": 0, "inventory": focused.inventory})
    actual_later, actual_early = collect(remainder, items)
    assert early == actual_early
    assert later == actual_later
    assert not (set(early) & set(later))
    assert set(early + later) == {i.nodeid for i in items}
    assert len(early) == 2
    assert len(later) == 3


@pytest.mark.parametrize("change", ["add", "remove", "marker", "failed", "missing", "extra"])
def test_remainder_rejects_collection_or_evidence_drift(change):
    items = population()
    focused = Partition("focused")
    collect(focused, items)
    previous = {"exit_code": 0, "inventory": focused.inventory}
    if change == "add":
        items.append(Item("tests/test_new.py::test_new"))
    elif change == "remove":
        items.pop()
    elif change == "marker":
        items[0].postgres = False
    elif change == "failed":
        previous["exit_code"] = 1
    elif change == "missing":
        previous = None
    else:
        previous["unexpected"] = True
    with pytest.raises(pytest.UsageError, match="collection evidence"):
        collect(Partition("remainder", previous), items)


@pytest.mark.parametrize("kind", ["duplicate", "empty_focus", "empty_remainder"])
def test_invalid_collection_is_rejected(kind):
    items = population()
    if kind == "duplicate":
        items.append(items[0])
    elif kind == "empty_focus":
        items = items[2:]
    else:
        items = items[:2]
    with pytest.raises(pytest.UsageError):
        collect(Partition("focused"), items)


@pytest.mark.parametrize("kind", ["stale", "missing", "malformed"])
def test_cli_rejects_invalid_evidence_before_running_tests(tmp_path, monkeypatch, kind):
    evidence = tmp_path / "evidence.json"
    phase = "focused" if kind == "stale" else "remainder"
    if kind != "missing":
        evidence.write_text("invalid" if kind == "malformed" else "{}")
    monkeypatch.setattr("sys.argv", ["run_ci_tests.py", phase, "--evidence", str(evidence)])
    monkeypatch.setattr(_module.pytest, "main", lambda *a, **kw: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as exc:
        _module.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("exit_code", [0, 1, 5])
def test_cli_only_records_successful_focused_run(tmp_path, monkeypatch, exit_code):
    evidence = tmp_path / "evidence.json"
    monkeypatch.setattr("sys.argv", ["run_ci_tests.py", "focused", "--evidence", str(evidence)])

    def run(args, *, plugins):
        assert args == ["tests/", "-q"]
        collect(plugins[0], population())
        return exit_code

    monkeypatch.setattr(_module.pytest, "main", run)
    assert _module.main() == exit_code
    assert evidence.exists() == (exit_code == 0)
    if exit_code == 0:
        receipt = json.loads(evidence.read_text())
        assert receipt["exit_code"] == 0
        assert len(receipt["inventory"]["all"]) == 5
