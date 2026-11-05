"""Append-only work claims and atomic publication completion.

The caller supplies a clean connection for short control transactions and the
existing business publisher's connection for completion. No leases, deletion,
automatic reversal, provider calls or implicit mutation retry occur here.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from psycopg.types.json import Jsonb
from psycopg.pq import TransactionStatus

from mfdataindia.enrichment.ingestion_discovery import SOURCES, State, work
from mfdataindia.review.policy import fingerprint

EXECUTION = re.compile(r'tlws-mf-official-facts-refresh-dev-[a-z0-9-]{1,40}')
REASONS = frozenset({
    'CHECKED_ISSUER_CATALOGUE_EMPTY', 'COMPLETE_ISSUER_LINKED_ENGLISH_PAIR_REQUIRED',
    'IDENTICAL_INPUT_ALREADY_PUBLISHED', 'PRIOR_WORK_REQUIRES_RECONCILIATION',
    'SOURCE_REQUEST_FAILED', 'SOURCE_RESPONSE_INVALID', 'PDF_DATE_OR_LAYOUT_INVALID',
    'ACTUAL_REPORTING_PERIOD_VALIDATED', 'PUBLICATION_STARTED',
    'ACTUAL_PUBLICATION_RECEIPT_VERIFIED', 'TRANSACTION_ROLLBACK_CONFIRMED',
    'PUBLICATION_OUTCOME_UNRESOLVED', 'COMMIT_OBSERVED_READBACK_PENDING',
    'PREPARATION_FAILED_BEFORE_PUBLICATION', 'CADENCE_NOT_DUE',
    'DISCOVERY_NOT_SUPPORTED', 'INSTALLED_SOURCE_POLICY_DIFFERS',
    'DEADLINE_EXHAUSTED', 'SOURCE_ACCESS_DENIED', 'DATABASE_CONNECTION_FAILED',
    'CONTROL_PERSISTENCE_FAILED', 'PARTIAL_SOURCE_FILES', 'CURRENT_INPUT_UNCHANGED',
    'ISSUER_LISTING_NO_ELIGIBLE_BOOK_LINK', 'NEW_EDITION_ADAPTER_REVIEW_REQUIRED',
    'SOURCE_ARCHIVE_FAILED',
    'SOURCE_LOCK_UNAVAILABLE', 'SOURCE_SQL_LIMIT',
})


@dataclass(frozen=True)
class Claim:
    state: State
    document: dict | None
    work: dict


def _identity(source, execution):
    if source not in SOURCES or type(execution) is not str or EXECUTION.fullmatch(execution) is None:
        raise ValueError('INGESTION_EXECUTION_IDENTITY_INVALID')


def _idle(conn):
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('INGESTION_CONTROL_REQUIRES_IDLE_CONNECTION')


def _bounds(conn):
    conn.execute("SET LOCAL statement_timeout='30s'")
    conn.execute("SET LOCAL transaction_timeout='60s'")
    conn.execute("SET LOCAL lock_timeout='5s'")


def claim(conn, candidate, execution, *, source_inputs=()):
    """Serialize identical candidates before any business submission.

    A connection/COMMIT error escapes. It is never proof that the claim did not
    persist; callers stop and inspect. No publisher is invoked by this method.
    """
    source = candidate['source_key']
    _identity(source, execution)
    expected = work(source, candidate['reporting_period'], candidate['documents'],
                    candidate['derivation_version'], candidate['population_hash'])
    if candidate != expected:
        raise ValueError('INGESTION_WORK_SEAL_DIFFERS')
    if (type(source_inputs) is not tuple or len(source_inputs) > 1024
            or any(type(r) is not dict or set(r) != {'source_url', 'request', 'role', 'sha256', 'evidence_class'}
                   or type(r['source_url']) is not str or len(r['source_url']) > 2048
                   or not r['source_url'].startswith('https://')
                   or type(r['request']) is not dict or set(r['request'])-{'book', 'catalog_api'}
                   or any(type(v) is not bool for v in r['request'].values())
                   or r['evidence_class'] not in ('input','discovery')
                   or type(r['role']) is not str or re.fullmatch('[a-z][a-z0-9_]{0,47}',r['role']) is None
                   or type(r['sha256']) is not str or re.fullmatch('[0-9a-f]{64}',r['sha256']) is None
                   or r['evidence_class']=='input' and {'role': r['role'], 'sha256': r['sha256']} not in candidate['documents']
                   for r in source_inputs)):
        raise ValueError('INGESTION_SOURCE_INPUT_REFERENCES_INVALID')
    _idle(conn)
    result = None
    with conn.transaction():
        _bounds(conn)
        # Serialize only this source's short claim decision, not publication
        # or other AMCs. A changed key must not bypass unresolved effects.
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                     ('amc-ingestion-source-v1:'+source,))
        conn.execute('SELECT pg_advisory_xact_lock(%s)',
                     (int(candidate['work_id'][:16], 16) - 2**63,))
        conn.execute('INSERT INTO mf_ingestion_ops.work VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                     (candidate['work_id'], source, Jsonb(candidate)))
        actual = conn.execute('SELECT document FROM mf_ingestion_ops.work WHERE work_id=%s',
                              (candidate['work_id'],)).fetchone()
        if actual is None or actual['document'] != candidate:
            raise ValueError('INGESTION_DURABLE_WORK_DIFFERS')
        # An execution identity is never repurposed for a changed candidate.
        spent = conn.execute('SELECT document FROM mf_ingestion_ops.claims WHERE source_key=%s AND execution_id=%s',
                             (source, execution)).fetchone()
        if spent:
            result = Claim(State.HELD, spent['document'], candidate)
        else:
            previous = conn.execute('SELECT document FROM mf_ingestion_ops.claims WHERE work_id=%s ORDER BY attempt DESC LIMIT 1',
                                    (candidate['work_id'],)).fetchone()
            if previous:
                events = conn.execute('SELECT kind FROM mf_ingestion_ops.events WHERE claim_id=%s',
                                      (previous['document']['claim_id'],)).fetchall()
                kinds = {r['kind'] for r in events}
                if 'PUBLISHED' in kinds:
                    result = Claim(State.UNCHANGED, previous['document'], candidate)
                elif not kinds or kinds != {'ROLLBACK_CONFIRMED'}:
                    # PUBLICATION_INTENT + confirmed rollback is also resolved.
                    if 'ROLLBACK_CONFIRMED' not in kinds or kinds & {'OUTCOME_UNKNOWN', 'COMMITTED_UNVERIFIED'}:
                        result = Claim(State.HELD, previous['document'], candidate)
            if result is None:
                blocked = conn.execute('''SELECT c.document FROM mf_ingestion_ops.claims c
                    WHERE c.source_key=%s
                    AND NOT EXISTS(SELECT 1 FROM mf_ingestion_ops.events e
                                   WHERE e.claim_id=c.claim_id AND e.kind='PUBLISHED')
                    AND (NOT EXISTS(SELECT 1 FROM mf_ingestion_ops.events e
                                    WHERE e.claim_id=c.claim_id AND e.kind='ROLLBACK_CONFIRMED')
                         OR EXISTS(SELECT 1 FROM mf_ingestion_ops.events e
                                   WHERE e.claim_id=c.claim_id
                                   AND e.kind IN('OUTCOME_UNKNOWN','COMMITTED_UNVERIFIED')))
                    ORDER BY c.claim_id LIMIT 1''', (source,)).fetchone()
                if blocked:
                    result = Claim(State.HELD, blocked['document'], candidate)
            if result is None:
                body = {'format': 'amc-ingestion-claim-v1', 'source_key': source,
                        'work_id': candidate['work_id'], 'execution_id': execution,
                        'attempt': previous['document']['attempt']+1 if previous else 1,
                        'source_inputs': list(source_inputs)}
                document = {**body, 'claim_id': fingerprint(body)}
                conn.execute('INSERT INTO mf_ingestion_ops.claims VALUES(%s,%s,%s,%s,%s,%s)',
                             (document['claim_id'], candidate['work_id'], source,
                              document['attempt'], execution, Jsonb(document)))
                result = Claim(State.CLAIMED, document, candidate)
    return result


def _event(conn, claim_document, kind, reason, **extra):
    if reason not in REASONS:
        raise ValueError('INGESTION_REASON_NOT_ALLOWLISTED')
    body = {'format': 'amc-ingestion-event-v1', 'claim_id': claim_document['claim_id'],
            'kind': kind, 'reason': reason, **extra}
    document = {**body, 'event_id': fingerprint(body)}
    conn.execute('INSERT INTO mf_ingestion_ops.events VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                 (document['event_id'], document['claim_id'], kind, Jsonb(document)))
    row = conn.execute('SELECT document FROM mf_ingestion_ops.events WHERE claim_id=%s AND kind=%s',
                       (document['claim_id'], kind)).fetchone()
    if row is None or row['document'] != document:
        raise ValueError('INGESTION_EVENT_REPLAY_DIFFERS')
    return document


def publication_intent(control, claim_document, writer):
    """Durable independent write-ahead record, before calling the publisher."""
    identity = writer.execute('SELECT pid,backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()').fetchone()
    _idle(control)
    with control.transaction():
        _bounds(control)
        return _event(control, claim_document, 'PUBLICATION_INTENT', 'PUBLICATION_STARTED',
                      backend_pid=identity['pid'], backend_start=identity['backend_start'].isoformat())


def complete(writer, claim_document, receipt, *, equivalent_derivations=()):
    """The ACTUAL publisher receipt and completion commit in the same TX."""
    if writer.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError('INGESTION_COMPLETION_REQUIRES_PUBLICATION_TRANSACTION')
    if (type(receipt) is not dict or receipt.get('execution_id') != claim_document['execution_id']
            or receipt.get('status') not in ('updated', 'unchanged', 'completed_with_field_gaps')
            or type(receipt.get('run_id')) is not str):
        raise ValueError('INGESTION_ACTUAL_PUBLICATION_RECEIPT_REQUIRED')
    if len(equivalent_derivations) > 2:
        raise ValueError('INGESTION_EQUIVALENT_DERIVATION_BOUND_EXCEEDED')
    return _event(writer, claim_document, 'PUBLISHED', 'ACTUAL_PUBLICATION_RECEIPT_VERIFIED',
                  publication={'run_id': receipt['run_id']},
                  equivalent_derivations=list(equivalent_derivations))


def outcome(control, claim_document, *, confirmed_rollback=False, commit_observed=False):
    """Caller must supply an observed outcome, never infer rollback from absence."""
    if confirmed_rollback and commit_observed:
        raise ValueError('INGESTION_OUTCOME_CONTRADICTS')
    kind, reason = ('ROLLBACK_CONFIRMED', 'TRANSACTION_ROLLBACK_CONFIRMED') if confirmed_rollback else (
        ('COMMITTED_UNVERIFIED', 'COMMIT_OBSERVED_READBACK_PENDING') if commit_observed else
        ('OUTCOME_UNKNOWN', 'PUBLICATION_OUTCOME_UNRESOLVED'))
    _idle(control)
    with control.transaction():
        _bounds(control)
        return _event(control, claim_document, kind, reason)


def published_input(conn, source, documents, derivation_version, population_hash):
    """Read-only short circuit, only an atomically committed completion counts.

    All original document bytes must have been hashed before this lookup. It
    saves PDF/identity derivation/publication, not the required source fetch.
    """
    if source not in SOURCES:
        raise ValueError('INGESTION_SOURCE_UNKNOWN')
    return conn.execute('''SELECT w.document FROM mf_ingestion_ops.work w
        JOIN mf_ingestion_ops.claims c USING(work_id)
        JOIN mf_ingestion_ops.events e USING(claim_id)
        WHERE w.source_key=%s AND w.document->'documents'=%s
        AND w.document->>'derivation_version'=%s AND w.document->>'population_hash'=%s
        AND e.kind='PUBLISHED' ORDER BY w.document->>'reporting_period' DESC LIMIT 1''',
        (source, Jsonb(documents), derivation_version, population_hash)).fetchone()


def record_poll(conn, source, execution, state, reason, *, latest_found=None, latest_published=None, observed=None, context=None, discovery_hash=None):
    _identity(source, execution)
    if not isinstance(state, State) or state in (State.READY, State.CLAIMED) or reason not in REASONS:
        raise ValueError('INGESTION_POLL_STATE_INVALID')
    timestamp = observed or datetime.now(timezone.utc)
    if timestamp.utcoffset() is None:
        raise ValueError('INGESTION_POLL_TIME_INVALID')
    if discovery_hash is not None and re.fullmatch('[0-9a-f]{64}',discovery_hash) is None:
        raise ValueError('INGESTION_DISCOVERY_OBSERVATION_HASH_INVALID')
    if context is not None and (type(context) is not dict or set(context) != {'stage','publication_effect','claim_id','claim_execution_id'}
            or context['stage'] not in ('discovery','control_readiness','cadence','identical_input_check','preparation','claim','publication','completion')
            or context['publication_effect'] not in ('NOT_SUBMITTED','UNKNOWN','COMMITTED_UNVERIFIED','ROLLBACK_CONFIRMED','PUBLICATION_VERIFIED')
            or context['claim_id'] is not None and re.fullmatch('[0-9a-f]{64}',context['claim_id']) is None
            or context['claim_execution_id'] is not None and EXECUTION.fullmatch(context['claim_execution_id']) is None):
        raise ValueError('INGESTION_POLL_CONTEXT_INVALID')
    body = {'format': 'amc-ingestion-poll-v1', 'source_key': source, 'execution_id': execution,
            'observed_at': timestamp.isoformat(), 'state': state.value, 'reason': reason,
            'latest_period_found': latest_found, 'latest_period_published': latest_published,
            **({'context':context} if context else {}),
            'discovery_observation_hash':discovery_hash}
    _idle(conn)
    with conn.transaction():
        _bounds(conn)
        conn.execute('INSERT INTO mf_ingestion_ops.polls VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                     (source, execution, timestamp, Jsonb(body)))
        actual = conn.execute('SELECT document FROM mf_ingestion_ops.polls WHERE source_key=%s AND execution_id=%s',
                              (source, execution)).fetchone()
        if actual is None or actual['document'] != body:
            raise ValueError('INGESTION_POLL_REPLAY_DIFFERS')
    return body


def inspect(conn, source, execution):
    """Only SELECT. Even a missing record is not proof of no publication."""
    _identity(source, execution)
    row = conn.execute('SELECT document FROM mf_ingestion_ops.claims WHERE source_key=%s AND execution_id=%s',
                       (source, execution)).fetchone()
    if row is None:
        return {'state': 'UNKNOWN', 'reason': 'NO_DURABLE_CLAIM_OBSERVED',
                'disposition': 'INSPECT_ONLY_NO_REPLAY', 'source_key': source, 'execution_id': execution}
    events = conn.execute('SELECT kind,document FROM mf_ingestion_ops.events WHERE claim_id=%s',
                          (row['document']['claim_id'],)).fetchall()
    kinds = {r['kind'] for r in events}
    effect = ('PUBLICATION_VERIFIED' if 'PUBLISHED' in kinds else
              'COMMITTED_UNVERIFIED' if 'COMMITTED_UNVERIFIED' in kinds else
              'UNKNOWN' if 'OUTCOME_UNKNOWN' in kinds else
              'ROLLBACK_CONFIRMED' if 'ROLLBACK_CONFIRMED' in kinds else 'UNKNOWN')
    return {'state': effect, 'source_key': source, 'execution_id': execution,
            'claim': row['document'], 'events': [r['document'] for r in events],
            'disposition': 'INSPECT_ONLY_NO_REPLAY'}
