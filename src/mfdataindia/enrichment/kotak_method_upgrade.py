"""Explicit retained-v4 -> Kotak-layout-v2 transition; no default-v5 adoption.

The original seal function, source rows, receipts and all financial selection
expressions are retained. The new view additionally admits the exact retained
v4 source hashes, so installing the method cannot hide September observations.
"""
import hashlib
from pathlib import Path
from psycopg.types.json import Jsonb

from mfdataindia.enrichment import book_sources, book_store, current_facts_store as current
from mfdataindia.enrichment.monthly_projection import owner, seal_configuration
from mfdataindia.review import store as review, reader_access_profile as profiles
from mfdataindia.review.policy import fingerprint

PROJECTION = 'amc_book_current_projection_v3.sql.inc'
PROJECTION_SHA256 = '28cfbe8edabe6af5527066ae79a79a4e04cb1b416cd3d7d13c4886ea2c38ae59'


def install(conn, root, *, expected_book_hash, expected_current_hash):
    """Caller owns one transaction/commit. No provider calls, ingestion or grants."""
    with conn.transaction():
        owner(conn)
        book_store.verify_acl(conn, principal='mfdata_verified_reader', reader_profile=profiles.BROAD)
        current.verify_acl(conn)
        installed = book_store.installed_configuration(conn)
        prior = dict(current.configuration(conn))
        if installed['document'] == book_sources.configuration(6):
            if prior['policy_hash'] != fingerprint(book_sources.current_policy(6)):
                raise ValueError('KOTAK_METHOD_INSTALLATION_INCOMPLETE')
            return {'status': 'ALREADY_INSTALLED', 'source_rows_changed': 0}
        if (installed['document'] != book_sources.configuration(4)
                or installed['configuration_hash'] != expected_book_hash
                or fingerprint(prior) != expected_current_hash
                or prior['policy_hash'] != fingerprint(book_sources.current_policy(4))
                or not current.projection_state(conn)):
            raise ValueError('KOTAK_METHOD_BASE_MISMATCH')
        root = Path(root)
        raw = (root / 'sql/optional' / PROJECTION).read_bytes()
        if hashlib.sha256(raw).hexdigest() != PROJECTION_SHA256:
            raise ValueError('KOTAK_METHOD_PROJECTION_MISMATCH')
        # The new policy admits the exact previous hash alongside the new one.
        # All other source specifications and selection expressions are equal.
        cfg = book_sources.configuration(6)
        old = installed['document']
        for key, spec in old['source_specifications'].items():
            if key != 'kotak-expanded' and cfg['source_specifications'][key] != spec:
                raise ValueError('KOTAK_METHOD_UNRELATED_SOURCE_CHANGED')
        before = conn.execute("SELECT count(*) AS n,encode(sha256(convert_to(COALESCE(string_agg(batch_id,',' ORDER BY batch_id),''),'UTF8')),'hex') AS sha256 FROM mf_book_facts.batches").fetchone()
        intent = {'format': 'kotak-method-upgrade-v1', 'mode': 'kotak_layout_upgrade',
                  'prior_book_configuration': dict(installed), 'prior_current_configuration': prior,
                  'target_configuration_hash': fingerprint(cfg),
                  'prior_batch_inventory': before, 'source_rows_changed': 0}
        intent['transition_id'] = fingerprint(intent)
        book_store.append_exact(conn, 'transitions', 'transition_id', intent, {})
        conn.execute('INSERT INTO mf_book_facts.configuration VALUES(%s,%s,%s)',
                     (installed['generation'] + 1, fingerprint(cfg), Jsonb(cfg)))
        policy = book_sources.current_policy(6)
        review._insert_exact(conn, 'policies', 'policy_hash', fingerprint(policy), policy,
                             {'policy_id': policy['id'], 'policy_version': policy['version']})
        conn.execute(raw.decode())
        definitions = dict(prior['view_definitions'])
        definitions['field_versions'] = current.view_definition(conn, 'mf_live_facts.field_versions')
        seal_configuration(conn, prior['generation'] + 1, prior['baseline_definition'],
                           fingerprint(policy), definitions)
        book_store.verify_acl(conn, principal='mfdata_verified_reader', reader_profile=profiles.BROAD)
        current.verify_acl(conn)
        after = conn.execute("SELECT count(*) AS n,encode(sha256(convert_to(COALESCE(string_agg(batch_id,',' ORDER BY batch_id),''),'UTF8')),'hex') AS sha256 FROM mf_book_facts.batches").fetchone()
        if before != after:
            raise ValueError('KOTAK_METHOD_BATCH_PRESERVATION_MISMATCH')
        return {'status': 'INSTALLED', 'transition_id': intent['transition_id'],
                'source_rows_changed': 0, 'configuration_hash': fingerprint(cfg)}
