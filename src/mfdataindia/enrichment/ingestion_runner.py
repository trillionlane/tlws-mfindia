"""One source's discovery/claim/publication cycle with no mutation replay.

Adapters keep their existing source validation and publisher. The shared store
is append-only; a pending/uncertain claim never becomes available by timeout.
There is no global loop lock, so one failed source cannot suppress its peers.
"""
from dataclasses import dataclass
import time

from mfdataindia.enrichment.ingestion_discovery import State, manifest, work
from mfdataindia.enrichment import ingestion_gate as gate
from mfdataindia.review.policy import fingerprint


@dataclass(frozen=True)
class Candidate:
    """Complete originals, not URLs or a filename-derived reporting period."""
    originals: list
    derivation_version: str
    population_hash: str
    complete: bool = True
    inputs: tuple = ()


@dataclass(frozen=True)
class Prepared:
    actual_period: str
    publish: object
    originals: list | None = None
    inputs: tuple = ()


@dataclass
class Progress:
    stage: str = 'discovery'
    effect: str = 'NOT_SUBMITTED'
    claim: dict | None = None


def execute(source, execution, adapter, store, *, clock=time.monotonic, maximum_seconds=520):
    """Provider/database implementations are injected, never monkeypatched.

    discover() returns (Discovery, Candidate|None). prepare() must validate the
    actual PDF dates and retain immutable originals before returning Prepared.
    store.publish() owns write-ahead intent, the existing publisher transaction
    and the atomic completion; it updates Progress as soon as evidence exists.
    """
    started = clock()
    progress = Progress()
    latest_found = latest_published = None
    discovery_hash = None

    def deadline():
        if clock()-started >= maximum_seconds:
            raise TimeoutError('INGESTION_DEADLINE_EXHAUSTED')

    def result(state, reason):
        return {'format': 'amc-discovery-result-v1', 'source_key': source,
                'execution_id': execution, 'state': state.value, 'reason': reason,
                'stage': progress.stage, 'publication_effect': progress.effect,
                'claim_id': progress.claim['claim_id'] if progress.claim else None,
                'claim_execution_id': progress.claim['execution_id'] if progress.claim else None,
                'discovery_observation_hash':discovery_hash,
                'latest_period_found': latest_found,
                'latest_period_published': latest_published,
                'disposition': 'INSPECT_ONLY_NO_REPLAY' if state==State.HELD or progress.effect in
                    ('UNKNOWN', 'COMMITTED_UNVERIFIED') else 'NO_IN_TASK_RETRY',
                'live_consumer_acceptance': False}

    try:
        if type(maximum_seconds) not in (int, float) or not 0 < maximum_seconds <= 520:
            raise ValueError('INGESTION_WALL_BOUND_INVALID')
        gate._identity(source, execution)
        progress.stage = 'control_readiness'
        latest_published = store.latest_published(source)
        deadline()
        if not store.due(source):
            progress.stage = 'cadence'
            observed = result(State.NOT_DUE, 'CADENCE_NOT_DUE')
            store.poll(observed)
            return observed
        progress.stage = 'discovery'
        discovery, candidate = adapter.discover(deadline)
        if discovery.source_key != source:
            raise ValueError('INGESTION_DISCOVERY_SOURCE_DIFFERS')
        latest_found=discovery.latest_period_found
        if discovery.state != State.READY:
            if candidate is not None or discovery.state not in (State.WAITING, State.INCOMPLETE, State.UNSUPPORTED, State.NOT_DUE, State.UNCHANGED):
                raise ValueError('INGESTION_DISCOVERY_STATE_INVALID')
            observed = result(discovery.state, discovery.reason)
            store.poll(observed)
            return observed
        if not isinstance(candidate, Candidate):
            raise ValueError('INGESTION_CANDIDATE_REQUIRED')
        documents = manifest(candidate.originals)
        discovery_hash = fingerprint(candidate.inputs)
        deadline()
        progress.stage = 'identical_input_check'
        known = store.published_input(source, documents, candidate.derivation_version,
                                      candidate.population_hash) if candidate.complete else None
        if known:
            latest_found = known['reporting_period']
            return_value = result(State.UNCHANGED, 'IDENTICAL_INPUT_ALREADY_PUBLISHED')
            store.poll(return_value)
            return return_value
        progress.stage = 'preparation'
        prepared = adapter.prepare(candidate, deadline)
        if not isinstance(prepared, Prepared) or not callable(prepared.publish):
            raise ValueError('INGESTION_PREPARED_PUBLICATION_REQUIRED')
        if manifest(candidate.originals) != documents:
            raise ValueError('INGESTION_PREPARATION_INPUTS_CHANGED')
        if prepared.originals is not None:
            complete_documents = manifest(prepared.originals)
            if not all(row in complete_documents for row in documents):
                raise ValueError('INGESTION_DISCOVERY_INPUTS_NOT_RETAINED')
            documents = complete_documents
        if prepared.inputs:
            discovery_hash = fingerprint(prepared.inputs)
        deadline()
        candidate_work = work(source, prepared.actual_period, documents,
                              candidate.derivation_version, candidate.population_hash)
        latest_found = prepared.actual_period
        progress.stage = 'claim'
        # Claim COMMIT must have returned successfully before publication. An
        # uncertain claim does not authorize the business publisher.
        claimed = store.claim(candidate_work, execution,
                              source_inputs=prepared.inputs or candidate.inputs)
        progress.claim = claimed.document
        if claimed.state in (State.HELD, State.UNCHANGED):
            observed = result(claimed.state, 'PRIOR_WORK_REQUIRES_RECONCILIATION' if claimed.state == State.HELD
                              else 'IDENTICAL_INPUT_ALREADY_PUBLISHED')
            store.poll(observed)
            return observed
        if claimed.state != State.CLAIMED:
            raise ValueError('INGESTION_CLAIM_NOT_CONFIRMED')
        deadline()
        progress.stage = 'publication'
        store.publish(claimed, prepared.publish, progress, deadline)
        if progress.effect != 'PUBLICATION_VERIFIED':
            raise ValueError('INGESTION_PUBLICATION_UNVERIFIED')
        latest_published = max(filter(None, (latest_published, latest_found)))
        progress.stage = 'completion'
        observed = result(State.PUBLISHED, 'ACTUAL_PUBLICATION_RECEIPT_VERIFIED')
        store.poll(observed)
        return observed
    except Exception as error:
        from mfdataindia.enrichment.source_publication import PreparationError
        from psycopg import errors, OperationalError
        # This is the only error conversion. Persistence/cleanup cannot replace
        # the strongest effect already observed in Progress with a false no-op.
        reason = (error.reason if isinstance(error, PreparationError) else
                  'SOURCE_ACCESS_DENIED' if isinstance(error,errors.InsufficientPrivilege) else
                  'SOURCE_LOCK_UNAVAILABLE' if isinstance(error,errors.LockNotAvailable) else
                  'SOURCE_SQL_LIMIT' if isinstance(error,errors.QueryCanceled) else
                  'DATABASE_CONNECTION_FAILED' if isinstance(error,OperationalError) else
                  'DEADLINE_EXHAUSTED' if isinstance(error, TimeoutError) else
                  'SOURCE_REQUEST_FAILED' if progress.stage == 'discovery' else
                  'PDF_DATE_OR_LAYOUT_INVALID' if progress.stage == 'preparation' else
                  'CONTROL_PERSISTENCE_FAILED')
        state = (State.PUBLISHED if progress.effect == 'PUBLICATION_VERIFIED' else
                 State.UNKNOWN if progress.effect in ('UNKNOWN', 'COMMITTED_UNVERIFIED') else
                 State.PUBLICATION_FAILED if progress.effect == 'ROLLBACK_CONFIRMED' else
                 State.HELD if isinstance(error,OperationalError) else
                 State.FETCH_FAILED if isinstance(error, PreparationError) and error.stage in ('acquisition','archive') else
                 State.FETCH_FAILED if progress.stage == 'discovery' else
                 State.PARSE_FAILED if progress.stage == 'preparation' else State.HELD)
        if progress.effect == 'PUBLICATION_VERIFIED':
            latest_published = max(filter(None, (latest_published, latest_found)))
        observed = result(state, reason)
        try:
            store.poll(observed)
        except Exception:
            observed['diagnostic_persistence'] = 'UNCONFIRMED'
        return observed


def independent(sources, execution, adapters, stores):
    """Integration harness; the production job still uses independent tasks."""
    return [execute(source, execution, adapters[source], stores[source]) for source in sources]
