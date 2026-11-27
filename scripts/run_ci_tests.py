"""Run two disjoint, exhaustively reconciled partitions of the full test suite.

The early PostgreSQL boundary gate remains first. Its successful tests are not
executed a second time in the remainder. Collection drift or failed/missing
early evidence fails closed; no test is marked skipped by this runner.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from functools import wraps
import hashlib
import json
from pathlib import Path
import re
from threading import Lock
from time import perf_counter
from unittest.mock import patch

import pytest

FOCUSED_FILES = frozenset({
    "tests/test_certification_input_native.py",
    "tests/test_uti_v2_store.py",
    "tests/test_uti_v3_store.py",
    "tests/test_uti_v4_store.py",
    "tests/test_selected_value_preservation.py",
    "tests/test_fenced_book_activation.py",
    "tests/test_ingestion_gate_postgres.py",
    "tests/test_monthly_derivation_replay_postgres.py",
    "tests/test_hdfc_dynamic_postgres.py",
    "tests/test_multi_amc_facts.py",
    "tests/test_nse_source_native_postgres.py",
})


# Fixed labels only: no DSNs, query text/parameters, returned values or exception text.
NATIVE_PHASES = frozenset({
    "protocol", "setup", "test_call", "teardown", "verified_db", "ro01_db",
    "source_reference_db", "source_variant_db", "migrations", "source_state",
    "populate", "v4_graph", "dataset_rows", "dataset_http", "reader_chain",
    "application_http", "certification_candidate", "certification_active",
    "certification_restored", "certification_scheduled",
})


class NativeMeasurements:
    """Test-local synchronous cursor API observations, not a database profiler.

    Calls and fetched rows are counted, not rows scanned by PostgreSQL. COPY,
    async/server cursors and implicit iteration are not measured. Nested elapsed
    phases are inclusive; cursor counters belong only to the innermost phase.
    No query is added, rendered, cached, consumed or retried by this observer.
    """

    def __init__(self, nodeid, *, clock=perf_counter):
        self.test_id_sha256 = hashlib.sha256(nodeid.encode()).hexdigest()
        module = nodeid.split("::", 1)[0].removeprefix("tests/")
        self.module = module if re.fullmatch(r"test_[a-z0-9_]+\.py", module) else "other"
        self.clock = clock
        self.phases = {}
        self.stack = []
        self.lock = Lock()

    def bucket(self, phase):
        return self.phases.setdefault(phase, {
            "elapsed_seconds": 0.0, "visits": 0, "execute_calls": 0,
            "executemany_calls": 0, "literal_catalogue_calls": 0,
            "fetch_calls": 0, "fetched_rows": 0, "failed_calls": 0,
            "cursor_seconds": 0.0, "slowest_cursor_seconds": 0.0,
        })

    @contextmanager
    def phase(self, label):
        if label not in NATIVE_PHASES:
            raise ValueError("unknown native measurement phase")
        start = self.clock()
        self.stack.append(label)
        try:
            yield
        finally:
            elapsed = self.clock() - start
            assert self.stack.pop() == label
            with self.lock:
                row = self.bucket(label)
                row["elapsed_seconds"] += elapsed
                row["visits"] += 1

    def observe_call(self, method, original):
        @wraps(original)
        def observed(cursor, *args, **kwargs):
            phase = self.stack[-1] if self.stack else "protocol"
            start = self.clock()
            failed, rows = True, 0
            try:
                result = original(cursor, *args, **kwargs)
                failed = False
                if method == "fetchone":
                    rows = int(result is not None)
                elif method in {"fetchall", "fetchmany"}:
                    rows = len(result)
                return result  # Preserve identity, cursor semantics and original exceptions.
            finally:
                elapsed = self.clock() - start
                with self.lock:
                    row = self.bucket(phase)
                    if method in {"execute", "executemany"}:
                        row[method + "_calls"] += 1
                        query = args[0] if args else kwargs.get("query")
                        # Classification only; composed queries are never rendered.
                        if isinstance(query, str) and re.search(
                            r"\b(?:pg_catalog|information_schema|pg_class|pg_namespace|"
                            r"pg_attribute|pg_roles|pg_proc|pg_tables|pg_default_acl)\b", query,
                        ):
                            row["literal_catalogue_calls"] += 1
                    else:
                        row["fetch_calls"] += 1
                        row["fetched_rows"] += rows
                    row["failed_calls"] += int(failed)
                    row["cursor_seconds"] += elapsed
                    row["slowest_cursor_seconds"] = max(row["slowest_cursor_seconds"], elapsed)
        return observed

    @contextmanager
    def observe(self, cursor_type):
        # Restore exactly the original methods even when fixture/test/cleanup fails.
        with ExitStack() as stack:
            for method in ("execute", "executemany", "fetchone", "fetchall", "fetchmany"):
                original = getattr(cursor_type, method)
                stack.enter_context(patch.object(cursor_type, method, self.observe_call(method, original)))
            yield

    def report(self):
        return {"test_id_sha256": self.test_id_sha256, "module": self.module,
                "phases": {label: {
                    key: round(value, 6) if isinstance(value, float) else value
                    for key, value in values.items()
                } for label, values in sorted(self.phases.items())}}


@contextmanager
def native_phase(request, label):
    """No-op in ordinary pytest; normal CI runner supplies per-test measurements."""
    metrics = getattr(getattr(request, "config", None), "_mf_ci_native_metrics", None)
    if metrics is None:
        yield
    else:
        with metrics.phase(label):
            yield

def native_timed(label):
    """Time an existing test helper without changing its arguments or return value."""
    if label not in NATIVE_PHASES:
        raise ValueError("unknown native measurement phase")

    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            with native_phase(kwargs.get("request"), label):
                return function(*args, **kwargs)
        return measured
    return decorate


class Partition:
    def __init__(self, phase: str, previous: dict | None = None):
        self.phase = phase
        self.previous = previous
        self.inventory: dict | None = None
        self.native_measurements = []

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item):
        if item.get_closest_marker("postgres") is None:
            yield
            return
        import psycopg
        metrics = NativeMeasurements(item.nodeid)
        item.config._mf_ci_native_metrics = metrics
        try:
            with metrics.observe(psycopg.Cursor), metrics.phase("protocol"):
                yield
        finally:
            del item.config._mf_ci_native_metrics
            self.native_measurements.append(metrics.report())

    @pytest.hookimpl(hookwrapper=True)
    def pytest_fixture_setup(self, fixturedef, request):
        label = fixturedef.argname
        with native_phase(request, label if label in {
            "verified_db", "ro01_db", "source_reference_db", "source_variant_db",
        } else "setup"):
            yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        with native_phase(item, "test_call"):
            yield

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(self, item):
        with native_phase(item, "teardown"):
            yield

    def pytest_terminal_summary(self, terminalreporter):
        # Outside test capture: never contaminate worker certificate stdout.
        # All modules are aggregated; include the slowest 30 individual tests.
        modules = {}
        for report in self.native_measurements:
            target = modules.setdefault(report["module"], {"tests": 0, "phases": {}})
            target["tests"] += 1
            for phase, values in report["phases"].items():
                row = target["phases"].setdefault(phase, {})
                for key, value in values.items():
                    row[key] = max(row.get(key, 0), value) if key == "slowest_cursor_seconds" else row.get(key, 0) + value
        if modules:
            terminalreporter.write_sep("-", "Native measurements: inclusive phases; synchronous cursor APIs only")
            for module, aggregate in sorted(modules.items()):
                terminalreporter.write_line("CI_NATIVE_MODULE " + json.dumps({"module": module, **aggregate}, sort_keys=True))
            for report in sorted(self.native_measurements, key=lambda row: row["phases"]["protocol"]["elapsed_seconds"], reverse=True)[:30]:
                terminalreporter.write_line("CI_NATIVE_TEST " + json.dumps(report, sort_keys=True))

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
    result = int(pytest.main(["tests/", "-q", "--durations=50", "--durations-min=0.1"], plugins=[plugin]))
    if args.phase == "focused" and result == 0 and plugin.inventory is not None:
        with args.evidence.open("x") as stream:
            json.dump({"exit_code": result, "inventory": plugin.inventory}, stream)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
