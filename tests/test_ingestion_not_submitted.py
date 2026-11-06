"""Actual runner/Store boundaries with synthetic transports; native SQL is separate."""
from contextlib import contextmanager
from types import SimpleNamespace

import json
import psycopg
from psycopg.pq import ConnStatus, TransactionStatus
import pytest

from mfdataindia.enrichment import ingestion_gate as gate, ingestion_postgres
from mfdataindia.enrichment import ingestion_runner as runner
from mfdataindia.enrichment.ingestion_discovery import Discovery, State
from mfdataindia.review.policy import fingerprint

SOURCE = 'kotak-expanded'
EXECUTION = 'tlws-mf-official-facts-refresh-dev-no-publisher'
SECRET = 'SYNTHETIC_SECRET_DO_NOT_REPORT'


class Clock:
    expired = False

    def __call__(self):
        return 521 if self.expired else 0


class Connection:
    def __init__(self, *, commit_unknown=False):
        self.info = SimpleNamespace(status=ConnStatus.OK, transaction_status=TransactionStatus.IDLE)
        self.commit_unknown = commit_unknown

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args):
        pass

    @contextmanager
    def transaction(self):
        self.info.transaction_status = TransactionStatus.INTRANS
        try:
            yield
        except BaseException:
            self.info.transaction_status = TransactionStatus.IDLE
            raise
        else:
            self.info.transaction_status = TransactionStatus.IDLE
            if self.commit_unknown:
                raise psycopg.OperationalError(SECRET)


def boundary(monkeypatch, point, *, terminal_failure=False, poll_failure=False):
    clock, calls, events = Clock(), [], []

    def claim(conn, work, execution, **kwargs):
        body = {'format': 'amc-ingestion-claim-v1', 'source_key': SOURCE,
                'execution_id': execution, 'work_id': work['work_id'],
                'attempt': 1, 'source_inputs': []}
        document = {**body, 'claim_id': fingerprint(body)}
        if point == 'claim_commit_unknown':
            raise psycopg.OperationalError(SECRET)
        if point == 'after_claim':
            clock.expired = True
        return gate.Claim(State.CLAIMED, document, work)

    def intent(*args):
        if point == 'intent_commit_unknown':
            raise psycopg.OperationalError(SECRET)
        events.append('PUBLICATION_INTENT')
        if point == 'after_intent':
            clock.expired = True

    def preparation_failed(*args):
        if terminal_failure:
            raise psycopg.OperationalError(SECRET)
        events.append('PREPARATION_FAILED')

    def outcome(*args, confirmed_rollback=False, commit_observed=False):
        events.append('ROLLBACK_CONFIRMED' if confirmed_rollback else
                      'COMMITTED_UNVERIFIED' if commit_observed else 'OUTCOME_UNKNOWN')

    monkeypatch.setattr(gate, 'claim', claim)
    monkeypatch.setattr(gate, 'publication_intent', intent)
    monkeypatch.setattr(gate, 'preparation_failed', preparation_failed, raising=False)
    monkeypatch.setattr(gate, 'outcome', outcome)
    monkeypatch.setattr(gate, 'complete', lambda *a, **k: events.append('PUBLISHED'))

    class Store(ingestion_postgres.Store):
        def latest_published(self, source):
            return '2026-09-30'

        def due(self, source):
            return True

        def published_input(self, *args):
            return None

        def poll(self, observation):
            if poll_failure:
                raise OSError(SECRET)

    class Adapter:
        def discover(self, deadline):
            return Discovery(SOURCE, State.READY), runner.Candidate(
                [{'role': 'book', 'content': b'%PDF-synthetic'}], 'a'*64, 'b'*64)

        def prepare(self, candidate, deadline):
            def publisher(conn):
                calls.append('publisher')
                return {'execution_id': EXECUTION, 'status': 'updated', 'run_id': 'f'*64}
            return runner.Prepared('2026-10-31', publisher)

    store = Store(lambda: Connection(commit_unknown=point == 'writer_commit_unknown'))
    result = runner.execute(SOURCE, EXECUTION, Adapter(), store, clock=clock)
    return result, calls, events


@pytest.mark.parametrize('point', ['after_claim', 'after_intent'])
def test_proven_no_publisher_deadline_is_durably_terminal(monkeypatch, point):
    result, calls, events = boundary(monkeypatch, point)
    assert calls == []
    assert events == (['PUBLICATION_INTENT'] if point == 'after_intent' else []) + ['PREPARATION_FAILED']
    assert result['claim_terminal_outcome'] == 'PREPARATION_FAILED'
    assert result['state'] == State.PARSE_FAILED.value
    assert result['publication_effect'] == 'NOT_SUBMITTED'
    assert result['reason'] == 'DEADLINE_EXHAUSTED'
    assert result['latest_period_published'] == '2026-09-30'
    assert result['claim_execution_id'] == EXECUTION
    assert result['disposition'] == 'NO_IN_TASK_RETRY'
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('point', ['claim_commit_unknown', 'intent_commit_unknown', 'writer_commit_unknown'])
def test_uncertain_control_or_business_commit_never_becomes_no_call_terminal(monkeypatch, point):
    result, calls, events = boundary(monkeypatch, point)
    assert 'PREPARATION_FAILED' not in events
    assert 'claim_terminal_outcome' not in result
    assert result['state'] in (State.HELD.value, State.UNKNOWN.value)
    assert result['disposition'] == 'INSPECT_ONLY_NO_REPLAY'
    assert len(calls) == (1 if point == 'writer_commit_unknown' else 0)
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('point', ['after_claim', 'after_intent'])
def test_failed_terminal_persistence_does_not_claim_release(monkeypatch, point):
    result, calls, events = boundary(monkeypatch, point, terminal_failure=True)
    assert calls == [] and 'PREPARATION_FAILED' not in events
    assert result['state'] == State.HELD.value
    assert 'claim_terminal_outcome' not in result
    assert result['reason'] == 'DEADLINE_EXHAUSTED'
    assert result['diagnostic_persistence'] == 'UNCONFIRMED'
    assert SECRET not in json.dumps(result)


def test_later_poll_failure_retains_acknowledged_terminal_outcome(monkeypatch):
    result, calls, events = boundary(monkeypatch, 'after_intent', poll_failure=True)
    assert calls == [] and events == ['PUBLICATION_INTENT', 'PREPARATION_FAILED']
    assert result['claim_terminal_outcome'] == 'PREPARATION_FAILED'
    assert result['state'] == State.PARSE_FAILED.value
    assert result['diagnostic_persistence'] == 'UNCONFIRMED'
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('context', ['entered', 'intent_unknown', 'commit_unknown', 'committed'])
def test_no_call_record_requires_owned_acknowledged_non_submission(context):
    claimed = gate.Claim(State.CLAIMED, {'claim_id': 'c'*64}, {})
    progress = runner.Progress(claim=claimed.document)
    if context == 'entered':
        progress.publisher_entered = True
    elif context == 'intent_unknown':
        progress.intent_uncertain = True
    else:
        progress.effect = 'UNKNOWN' if context == 'commit_unknown' else 'COMMITTED_UNVERIFIED'
    def forbidden_connect():
        pytest.fail('no-call persistence must reject ambiguity before connecting')
    with pytest.raises(ValueError, match='INGESTION_NO_CALL_PROOF_REQUIRED'):
        ingestion_postgres.Store(forbidden_connect).not_submitted(claimed, progress)
    assert not progress.not_submitted_recorded


def test_acknowledged_terminal_survives_control_connection_cleanup_failure(monkeypatch):
    claimed = gate.Claim(State.CLAIMED, {'claim_id': 'c'*64}, {})
    progress = runner.Progress(claim=claimed.document)
    events = []
    monkeypatch.setattr(gate, 'preparation_failed', lambda *a: events.append('PREPARATION_FAILED'))
    class CleanupFailure(Connection):
        def __exit__(self, *args):
            raise OSError(SECRET)
    with pytest.raises(OSError):
        ingestion_postgres.Store(CleanupFailure).not_submitted(claimed, progress)
    assert events == ['PREPARATION_FAILED'] and progress.not_submitted_recorded
