"""Provider-free orchestration faults; real SQL isolation has native cases."""
from dataclasses import replace
import json
import pytest

from mfdataindia.enrichment import ingestion_runner as runner, ingestion_gate as gate
from mfdataindia.enrichment.ingestion_discovery import Discovery, State

SOURCE = 'uti-expanded'
EXECUTION = 'tlws-mf-official-facts-refresh-dev-synthetic'
SECRET = 'SYNTHETIC-NEVER-PRINT-THIS-DSN'


class Adapter:
    def __init__(self, state=State.READY, raw=b'%PDF-new-pair', error=False):
        self.state, self.error, self.preparations = state, error, 0
        self.candidate = runner.Candidate([{'role': 'active', 'content': raw}], 'a'*64, 'b'*64)

    def discover(self, deadline):
        deadline()
        if self.error:
            raise OSError(SECRET)
        reason = {State.WAITING: 'CHECKED_ISSUER_CATALOGUE_EMPTY',
                  State.INCOMPLETE: 'COMPLETE_ISSUER_LINKED_ENGLISH_PAIR_REQUIRED'}.get(self.state, '')
        return Discovery(SOURCE, self.state, reason=reason), self.candidate if self.state == State.READY else None

    def prepare(self, candidate, deadline):
        self.preparations += 1
        deadline()
        return runner.Prepared('2026-10-31', lambda conn: {'actual': 'publisher'})


class Store:
    def __init__(self, effect=None, poll_error=False, claim_error=False):
        self.effect, self.poll_error, self.claim_error = effect, poll_error, claim_error
        self.known, self.claims, self.submissions, self.polls = {}, [], 0, []

    def latest_published(self, source):
        return '2026-09-30'

    def due(self, source):
        return True

    def published_input(self, source, docs, derivation, population):
        return self.known.get(json.dumps([source, docs, derivation, population], sort_keys=True))

    def claim(self, candidate, execution, *, source_inputs=()):
        if self.claim_error:
            raise OSError(SECRET)
        self.claims.append(candidate)
        return gate.Claim(State.CLAIMED, {'claim_id': 'c'*64, 'execution_id': execution}, candidate)

    def publish(self, claimed, publisher, progress, deadline):
        self.submissions += 1
        progress.effect = self.effect or 'PUBLICATION_VERIFIED'
        if self.effect:
            raise OSError(SECRET)
        candidate = claimed.work
        self.known[json.dumps([candidate['source_key'], candidate['documents'],
                              candidate['derivation_version'], candidate['population_hash']], sort_keys=True)] = candidate

    def poll(self, observation):
        if self.poll_error:
            raise OSError(SECRET)
        self.polls.append(observation)


def test_empty_incomplete_published_unchanged_skips_derivation_and_mutation():
    store = Store()
    for state in (State.WAITING, State.INCOMPLETE):
        result = runner.execute(SOURCE, EXECUTION, Adapter(state), store)
        assert result['state'] == state.value and result['latest_period_published'] == '2026-09-30'
        assert store.submissions == len(store.claims) == 0
    first = runner.execute(SOURCE, EXECUTION, Adapter(), store)
    repeated = Adapter()
    second = runner.execute(SOURCE, EXECUTION+'2', repeated, store)
    assert first['state'] == 'PUBLISHED' and second['state'] == 'UNCHANGED'
    assert repeated.preparations == 0 and store.submissions == len(store.claims) == 1
    revised = runner.execute(SOURCE, EXECUTION+'3', Adapter(raw=b'%PDF-revised'), store)
    assert revised['state'] == 'PUBLISHED' and store.submissions == 2
    assert store.claims[0]['reporting_period'] == store.claims[1]['reporting_period']
    assert store.claims[0]['work_id'] != store.claims[1]['work_id']


@pytest.mark.parametrize('effect,state', [
    ('UNKNOWN', 'PUBLICATION_OUTCOME_UNKNOWN'),
    ('COMMITTED_UNVERIFIED', 'PUBLICATION_OUTCOME_UNKNOWN'),
    ('ROLLBACK_CONFIRMED', 'PUBLICATION_ROLLED_BACK'),
    ('PUBLICATION_VERIFIED', 'PUBLISHED'),
])
def test_strongest_effect_survives_exception_and_failed_diagnostic_persistence(effect, state):
    store = Store(effect=effect, poll_error=True)
    result = runner.execute(SOURCE, EXECUTION, Adapter(), store)
    assert result['state'] == state and result['publication_effect'] == effect
    assert result['claim_id'] == 'c'*64 and result['stage'] == 'publication'
    assert result['execution_id'] == EXECUTION and result['live_consumer_acceptance'] is False
    assert SECRET not in json.dumps(result)
    assert result['diagnostic_persistence'] == 'UNCONFIRMED'
    assert store.submissions == 1


def test_uncertain_claim_commit_never_reaches_business_publisher():
    store = Store(claim_error=True)
    result = runner.execute(SOURCE, EXECUTION, Adapter(), store)
    assert result['state'] == 'HELD_FOR_RECONCILIATION'
    assert store.submissions == 0 and not store.claims
    assert SECRET not in json.dumps(result)


def test_one_fetch_failure_cannot_suppress_another_source():
    class Other(Adapter):
        def discover(self, deadline):
            discovery, candidate = super().discover(deadline)
            return replace(discovery, source_key='kotak-expanded'), candidate
    sources = ['uti-expanded', 'kotak-expanded']
    stores = {s: Store() for s in sources}
    results = runner.independent(sources, EXECUTION, {sources[0]: Adapter(error=True), sources[1]: Other()}, stores)
    assert [r['state'] for r in results] == ['FETCH_FAILED', 'PUBLISHED']
    assert stores[sources[0]].submissions == 0 and stores[sources[1]].submissions == 1


def test_expired_wall_and_changed_preparation_inputs_do_not_publish():
    times = iter([0, 521])
    store = Store()
    result = runner.execute(SOURCE, EXECUTION, Adapter(), store, clock=lambda: next(times))
    assert result['reason'] == 'DEADLINE_EXHAUSTED' and store.submissions == 0
    class Changed(Adapter):
        def prepare(self, candidate, deadline):
            candidate.originals[0]['content'] = b'%PDF-swapped-after-hash'
            return super().prepare(candidate, deadline)
    result = runner.execute(SOURCE, EXECUTION, Changed(), store)
    assert result['state'] == 'PARSE_OR_VALIDATION_FAILED' and store.submissions == 0
