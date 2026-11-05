"""PostgreSQL composition for the shared gate and existing source publishers."""
from datetime import datetime, timezone, timedelta
from psycopg.pq import TransactionStatus, ConnStatus

from mfdataindia.enrichment import ingestion_gate as gate


class Store:
    def __init__(self, connect, *, now=None):
        # Existing direct non-elevated generator connection factory. It must
        # return autocommit connections; methods explicitly own their TXs.
        self.connect = connect
        self.now = now or (lambda: datetime.now(timezone.utc))

    def latest_published(self, source):
        with self.connect() as conn:
            if source == 'absl-large-cap':
                row = conn.execute('SELECT max(disclosure_as_of)::text AS period FROM mf_fact_refresh.publications').fetchone()
            elif source == 'hdfc-flexi-cap':
                row = conn.execute("SELECT max(s.disclosure_as_of)::text AS period FROM mf_multi_facts.snapshots s WHERE EXISTS(SELECT 1 FROM mf_multi_facts.runs r WHERE r.status<>'failed' AND r.document->'snapshot_ids' ? s.snapshot_id)").fetchone()
            else:
                row = conn.execute("SELECT max(b.disclosure_as_of)::text AS period FROM mf_book_facts.batches b WHERE b.source_key=%s AND EXISTS(SELECT 1 FROM mf_book_facts.runs r WHERE r.source_key=b.source_key AND r.status<>'failed' AND r.document->'batch_ids' ? b.batch_id)", (source,)).fetchone()
            return row['period']

    def due(self, source):
        from mfdataindia.enrichment.ingestion_discovery import due
        with self.connect() as conn:
            row = conn.execute("SELECT observed_at FROM mf_ingestion_ops.polls WHERE source_key=%s AND document->>'state'<>'NOT_DUE' ORDER BY observed_at DESC LIMIT 1", (source,)).fetchone()
        now = self.now()
        target = (now.astimezone(timezone.utc).date().replace(day=1)-timedelta(days=1)).isoformat()
        published = self.latest_published(source)
        return due(source, now, row['observed_at'].isoformat() if row else None,
                   waiting=published is None or published < target)

    def published_input(self, *args):
        with self.connect() as conn:
            row = gate.published_input(conn, *args)
            return row['document'] if row else None

    def claim(self, candidate, execution, *, source_inputs=()):
        with self.connect() as conn:
            return gate.claim(conn, candidate, execution, source_inputs=source_inputs)

    def prior_inputs(self, source, seed, derivation_version, population_hash):
        with self.connect() as conn:
            from psycopg.types.json import Jsonb
            row = conn.execute('''SELECT c.document,w.document AS work FROM mf_ingestion_ops.claims c
                JOIN mf_ingestion_ops.events e USING(claim_id)
                JOIN mf_ingestion_ops.work w USING(work_id)
                WHERE c.source_key=%s AND e.kind='PUBLISHED'
                AND w.document->>'derivation_version'=%s
                AND w.document->>'population_hash'=%s
                AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(%s::jsonb) requested
                    WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(c.document->'source_inputs') prior
                        WHERE prior->>'role'=requested->>'role' AND prior->>'sha256'=requested->>'sha256'))
                ORDER BY w.document->>'reporting_period' DESC,c.attempt DESC LIMIT 1''',
                (source,derivation_version,population_hash,Jsonb(seed))).fetchone()
            return {'inputs':row['document']['source_inputs'],'population_hash':row['work']['population_hash']} if row else None

    def poll(self, observation):
        with self.connect() as conn:
            return gate.record_poll(conn, observation['source_key'], observation['execution_id'],
                gate.State(observation['state']), observation['reason'],
                latest_found=observation['latest_period_found'], latest_published=observation['latest_period_published'],
                observed=self.now(),context={k:observation[k] for k in ('stage','publication_effect','claim_id','claim_execution_id')},
                discovery_hash=observation.get('discovery_observation_hash'))

    def publish(self, claimed, publisher, progress, deadline):
        executing_error = False
        with self.connect() as control:
            with self.connect() as writer:
                try:
                    with writer.transaction():
                        statement, transaction = {'absl-large-cap':(120,240),
                            'hdfc-flexi-cap':(120,180)}.get(claimed.work['source_key'],(30,60))
                        writer.execute("SELECT set_config('statement_timeout',%s,true),set_config('transaction_timeout',%s,true),set_config('lock_timeout','5s',true)",
                                       (f'{statement}s',f'{transaction}s'))
                        gate.publication_intent(control, claimed.document, writer)
                        deadline()
                        # Before the publisher can have a business effect.
                        progress.effect = 'UNKNOWN'
                        try:
                            receipt = publisher(writer)
                            gate.complete(writer, claimed.document, receipt,
                                equivalent_derivations=getattr(publisher, 'equivalent_derivations', ()))
                        except BaseException:
                            executing_error = True
                            raise
                    # The single business COMMIT returned. Record this before
                    # control writes/connection cleanup can fail.
                    progress.effect = 'PUBLICATION_VERIFIED'
                    return receipt
                except Exception:
                    # IDLE alone is not proof of rollback after a COMMIT error.
                    # Only an exception from inside the body, followed by a
                    # successful rollback to an intact idle session, qualifies.
                    if (executing_error and writer.info.status == ConnStatus.OK
                            and writer.info.transaction_status == TransactionStatus.IDLE):
                        progress.effect = 'ROLLBACK_CONFIRMED'
                    try:
                        gate.outcome(control, claimed.document,
                                     confirmed_rollback=progress.effect == 'ROLLBACK_CONFIRMED',
                                     commit_observed=progress.effect == 'COMMITTED_UNVERIFIED')
                    except Exception:
                        pass
                    raise

    def inspect(self, source, execution):
        with self.connect() as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION READ ONLY')
                return gate.inspect(conn, source, execution)
