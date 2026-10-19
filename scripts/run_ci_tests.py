"""Run two disjoint, exhaustively reconciled partitions of the full test suite.

The early PostgreSQL boundary gate remains first. Its successful tests are not
executed a second time in the remainder. Collection drift or failed/missing
early evidence fails closed; no test is marked skipped by this runner.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

FOCUSED_FILES = frozenset({
    "tests/test_uti_v2_store.py",
    "tests/test_uti_v3_store.py",
    "tests/test_uti_v4_store.py",
    "tests/test_selected_value_preservation.py",
})


class Partition:
    def __init__(self, phase: str, previous: dict | None = None):
        self.phase = phase
        self.previous = previous
        self.inventory: dict | None = None

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config, items):
        all_ids = [item.nodeid for item in items]
        if len(set(all_ids)) != len(all_ids):
            raise pytest.UsageError("duplicate test IDs in full collection")
        focused = {
            item.nodeid for item in items
            if item.nodeid.split("::", 1)[0] in FOCUSED_FILES
            and item.get_closest_marker("postgres") is not None
        }
        if not focused or not (set(all_ids) - focused):
            raise pytest.UsageError("empty CI test partition")
        inventory = {"all": sorted(all_ids), "focused": sorted(focused)}
        if self.phase == "remainder":
            if self.previous != {"exit_code": 0, "inventory": inventory}:
                raise pytest.UsageError("missing, failed or changed focused collection evidence")
        self.inventory = inventory
        selected, deselected = [], []
        for item in items:
            include = (item.nodeid in focused) == (self.phase == "focused")
            (selected if include else deselected).append(item)
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)
        print(f"\nCI collection: {len(all_ids)} total, {len(focused)} focused, "
              f"{len(all_ids) - len(focused)} remainder; phase={self.phase}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("focused", "remainder"))
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    previous = None
    if args.phase == "focused":
        # A stale successful receipt must never survive a failed fresh run.
        if args.evidence.exists():
            parser.error("focused evidence already exists; use a fresh path")
    else:
        try:
            previous = json.loads(args.evidence.read_text())
        except (OSError, ValueError):
            parser.error("focused evidence is unavailable or invalid")
    plugin = Partition(args.phase, previous)
    result = int(pytest.main(["tests/", "-q"], plugins=[plugin]))
    if args.phase == "focused" and result == 0 and plugin.inventory is not None:
        with args.evidence.open("x") as stream:
            json.dump({"exit_code": result, "inventory": plugin.inventory}, stream)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
