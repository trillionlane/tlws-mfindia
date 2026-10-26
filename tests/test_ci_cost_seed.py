"""Provider-free exact old-loop equivalence, batching and no retry/txn changes."""

from datetime import date, timedelta
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import pytest

from verified_fixture_seed import NAV_INSERT_SQL, fixture_nav_rows, seed_fixture_nav

ROOT = Path(__file__).resolve().parents[1]


def original_seed(conn, code):
    # Literal original loop and SQL, independent of the new helper/constants.
    day = date(2024,1,1)
    for i in range(270):
        while day.weekday()>=5:
            day += timedelta(days=1)
        conn.execute("INSERT INTO mf.nav_history(amfi_scheme_code,nav_date,nav) VALUES(%s,%s,%s)", (code,day,'100' if i%2==0 else '110'))
        day += timedelta(days=1)


class OriginalConnection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((statement, params))


class BatchConnection:
    def __init__(self, fail_batch=None):
        self.batches = []
        self.events = []
        self.fail_batch = fail_batch

    def cursor(self):
        self.events.append('cursor')
        return self

    def __enter__(self):
        self.events.append('enter')
        return self

    def executemany(self, statement, rows):
        self.batches.append((statement, list(rows)))
        self.events.append('executemany')
        if len(self.batches) == self.fail_batch:
            raise RuntimeError('exact batch failure')

    def __exit__(self, kind, value, traceback):
        self.events.append(('exit', kind))
        return False

    def execute(self, *args):
        pytest.fail('no per-row connection.execute')

    def commit(self):
        pytest.fail('no helper commit')

    def rollback(self):
        pytest.fail('no helper rollback')


@pytest.mark.parametrize('code', [1, 2, 123456])
def test_exact_original_sql_and_ordered_rows(code):
    original = OriginalConnection()
    original_seed(original, code)
    batch = BatchConnection()
    assert seed_fixture_nav(batch, code) is None
    assert len(original.calls) == 270 and len(batch.batches) == 1
    statement, rows = batch.batches[0]
    assert [(statement, row) for row in rows] == original.calls
    assert statement == NAV_INSERT_SQL
    assert rows == fixture_nav_rows(code)
    assert rows[0] == (code, date(2024, 1, 1), '100')
    assert rows[-1] == (code, date(2025, 1, 10), '110')
    assert all(day.weekday() < 5 for _, day, _ in rows)
    assert [nav for _, _, nav in rows] == ['100', '110'] * 135
    assert batch.events == ['cursor', 'enter', 'executemany', ('exit', None)]


def test_two_funds_keep_all_540_rows_and_intervening_setup_order():
    old, batch = OriginalConnection(), BatchConnection()
    for code, option in ((1, 'GROWTH'), (2, 'IDCW')):
        batch.events.append(('fund-variant-facts', code, option))
        original_seed(old, code)
        seed_fixture_nav(batch, code)
    flattened = [(statement, row) for statement, rows in batch.batches for row in rows]
    assert flattened == old.calls and len(flattened) == 540
    assert len({(row[0], row[1]) for _, row in flattened}) == 540
    assert len(batch.batches) == 2 and all(len(rows) == 270 for _, rows in batch.batches)
    assert batch.events == [
        ('fund-variant-facts', 1, 'GROWTH'), 'cursor', 'enter', 'executemany', ('exit', None),
        ('fund-variant-facts', 2, 'IDCW'), 'cursor', 'enter', 'executemany', ('exit', None),
    ]


@pytest.mark.parametrize('failed_batch', [1, 2])
def test_batch_failure_propagates_without_retry_fallback_or_transaction_io(failed_batch):
    batch = BatchConnection(fail_batch=failed_batch)
    with pytest.raises(RuntimeError, match='exact batch failure'):
        for code in (1, 2):
            seed_fixture_nav(batch, code)
    assert len(batch.batches) == failed_batch
    assert batch.events[-1] == ('exit', RuntimeError)


@pytest.mark.parametrize('phase', ['focused', 'remainder'])
@pytest.mark.parametrize('exit_code', [0, 1, 5])
def test_duration_flags_both_phases_preserve_result_and_receipt(tmp_path, monkeypatch, phase, exit_code):
    spec = spec_from_file_location('ci_cost_runner', ROOT / 'scripts/run_ci_tests.py')
    runner = module_from_spec(spec)
    spec.loader.exec_module(runner)
    evidence = tmp_path / 'receipt.json'
    inventory = {'all': ['focused', 'remainder'], 'focused': ['focused']}
    prior = {'exit_code': 0, 'inventory': inventory}
    if phase == 'remainder':
        evidence.write_text(json.dumps(prior))
    monkeypatch.setattr('sys.argv', ['run_ci_tests.py', phase, '--evidence', str(evidence)])

    def run(args, *, plugins):
        assert args == ['tests/', '-q', '--durations=50', '--durations-min=0.1']
        assert len(plugins) == 1 and plugins[0].phase == phase
        assert plugins[0].previous == (prior if phase == 'remainder' else None)
        plugins[0].inventory = inventory
        return exit_code

    monkeypatch.setattr(runner.pytest, 'main', run)
    assert runner.main() == exit_code
    assert evidence.exists() == (phase == 'remainder' or exit_code == 0)
    if evidence.exists():
        assert json.loads(evidence.read_text()) == prior
