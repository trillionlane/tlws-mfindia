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


def install(conn, root, *, expected_book_hash, expected_current_hash, source_version=4, target_version=6):
    """Caller owns one transaction/commit. No provider calls, ingestion or grants."""
    with conn.transaction():
        if (source_version,target_version) not in ((4,6),(6,7)):
            raise ValueError('AMC_METHOD_TRANSITION_NOT_REVIEWED')
        owner(conn)
        book_store.verify_acl(conn, principal='mfdata_verified_reader', reader_profile=profiles.BROAD)
        current.verify_acl(conn)
        installed = book_store.installed_configuration(conn)
        prior = dict(current.configuration(conn))
        if installed['document'] == book_sources.configuration(target_version):
            if prior['policy_hash'] != fingerprint(book_sources.current_policy(target_version)):
                raise ValueError('KOTAK_METHOD_INSTALLATION_INCOMPLETE')
            return {'status': 'ALREADY_INSTALLED', 'source_rows_changed': 0}
        if (installed['document'] != book_sources.configuration(source_version)
                or installed['configuration_hash'] != expected_book_hash
                or fingerprint(prior) != expected_current_hash
                or prior['policy_hash'] != fingerprint(book_sources.current_policy(source_version))
                or not current.projection_state(conn)):
            raise ValueError('KOTAK_METHOD_BASE_MISMATCH')
        root = Path(root)
        raw = (root / 'sql/optional' / PROJECTION).read_bytes()
        if hashlib.sha256(raw).hexdigest() != PROJECTION_SHA256:
            raise ValueError('KOTAK_METHOD_PROJECTION_MISMATCH')
        # The new policy admits the exact previous hash alongside the new one.
        # All other source specifications and selection expressions are equal.
        cfg = book_sources.configuration(target_version)
        old = installed['document']
        for key, spec in old['source_specifications'].items():
            expected = spec
            if target_version == 7:
                from copy import deepcopy
                from importlib import import_module
                from mfdataindia.enrichment.amc_gate_source_revision import engine_hash
                expected = deepcopy(spec)
                for module_name in ('sbi_runtime','kotak_runtime','uti_runtime'):
                    hashes = expected.get('evidence_engine_hashes',{})
                    if module_name in hashes:
                        module = import_module('mfdataindia.enrichment.'+module_name)
                        if engine_hash(module,6) != hashes[module_name]:
                            raise ValueError('AMC_GATE_PREDECESSOR_ENGINE_DIFFERS')
                        hashes[module_name] = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            if (key != 'kotak-expanded' or target_version == 7) and cfg['source_specifications'][key] != expected:
                raise ValueError('KOTAK_METHOD_UNRELATED_SOURCE_CHANGED')
        before = conn.execute("SELECT count(*) AS n,encode(sha256(convert_to(COALESCE(string_agg(batch_id,',' ORDER BY batch_id),''),'UTF8')),'hex') AS sha256 FROM mf_book_facts.batches").fetchone()
        intent = {'format': 'kotak-method-upgrade-v1', 'mode': 'kotak_layout_upgrade' if target_version==6 else 'shared_discovery_gate',
                  'prior_book_configuration': dict(installed), 'prior_current_configuration': prior,
                  'target_configuration_hash': fingerprint(cfg),
                  'prior_batch_inventory': before, 'source_rows_changed': 0}
        intent['transition_id'] = fingerprint(intent)
        book_store.append_exact(conn, 'transitions', 'transition_id', intent, {})
        conn.execute('INSERT INTO mf_book_facts.configuration VALUES(%s,%s,%s)',
                     (installed['generation'] + 1, fingerprint(cfg), Jsonb(cfg)))
        policy = book_sources.current_policy(target_version)
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
