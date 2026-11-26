"""Current fixed six-task composition; operation history is in PostgreSQL.

The v1/v2 allocations remain explicit predecessors. Actual current sources are
pinned here while registry 9 remains outside this registry-8 workload.
"""
from importlib import import_module
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from mfdataindia.enrichment import (kotak_current_workload as prior,
    allocated_amc_workload as original, book_sources, book_store, ingestion_adapters,
    ingestion_postgres, ingestion_runner, ingestion_gate, ingestion_install,
    sbi_book_sources_v2 as sbi_sources, sbi_book_store_v2 as sbi_store)
from mfdataindia.enrichment.ingestion_discovery import SOURCES
from mfdataindia.review.policy import fingerprint

FILE = 'config/official-amc-discovery-dev-workload-v6.json'
SHA256='22563ebc5f0eed19feb7a6092f72f4077fdac172f72e6eba74cd43705423eb88'
PREVIOUS_SHA256='86f46acadcf953554a6e4f02b03b2edb05fc2e6b64870c0411027b007df7d675'
SOURCE_COMPATIBILITY = {
    'mode':'sbi_document_date_isolated_dormant',
    'default_registry_version':5,
    'allocated_registry_version':8,
    'excluded_registry_versions':[9],
    'allocated_migration':'041_uti_book_evidence.sql',
    'candidate_migration_not_allocated':'050_sbi_document_as_of_evidence.sql',
}


def allocation(root):
    root = Path(root)
    prior.allocation(root)
    path = root/FILE
    raw = path.read_bytes()
    if path.is_symlink() or not 0 < len(raw) <= 65536 or hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError('INGESTION_ALLOCATION_DIFFERS')
    document = json.loads(raw)
    if (document['book_configuration_sha256'] != fingerprint(book_sources.configuration(8))
            or document['predecessor_allocation_sha256'] != prior.SHA256
            or document['previous_current_allocation_sha256'] != PREVIOUS_SHA256
            or document['source_compatibility'] != SOURCE_COMPATIBILITY
            or document['live_execution_approval'] is not False
            or book_sources.CURRENT_VERSION != 5
            or set(book_sources.REGISTRIES) != set(range(1,9))
            or 9 in book_store.MIGRATIONS
            or book_store.MIGRATIONS[8][0] != SOURCE_COMPATIBILITY['allocated_migration']
            or sbi_sources.registry()['version'] != 9
            or sbi_store.MIGRATIONS[9][0] != SOURCE_COMPATIBILITY['candidate_migration_not_allocated']
            or document['tasks'] != [{'index':n,'source_key':s} for n,s in enumerate(SOURCES)]):
        raise ValueError('INGESTION_ALLOCATION_POLICY_DIFFERS')
    for name, digest in document['files'].items():
        path = root/name
        if name.startswith('src/') and name.endswith('.py'):
            module = import_module(name[4:-3].replace('/','.'))
            installed = Path(module.__file__)
            if installed.is_symlink() or hashlib.sha256(installed.read_bytes()).hexdigest() != digest:
                raise ValueError('INGESTION_INSTALLED_SOURCE_DIFFERS')
            if not path.exists():
                continue
        if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError('INGESTION_SOURCE_DIFFERS')
    return document


def validate(environment, root, *, kotak_only=False):
    document = allocation(root)
    dsn, index = original.validate_environment(environment, root, task_count=1 if kotak_only else 6)
    return document, dsn, 4 if kotak_only else index


def verify_database(dsn, document):
    with book_sources.select_registry(8):
        original.verify_database(dsn, document)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY')
        conn.execute("SET LOCAL statement_timeout='30s'")
        conn.execute("SET LOCAL transaction_timeout='60s'")
        ingestion_install.verify(conn)


def execute(dsn, environment, document, index):
    if type(index) is not int or not 0 <= index < len(SOURCES):
        raise ValueError('INGESTION_TASK_MAPPING_DIFFERS')
    source, execution = SOURCES[index], environment['CLOUD_RUN_EXECUTION']
    ingestion_gate._identity(source, execution)
    store = ingestion_postgres.Store(lambda: psycopg.connect(dsn, autocommit=True,
                                      connect_timeout=10, row_factory=dict_row))
    adapter = ingestion_adapters.Adapter(source, execution, dsn, store)
    observed = ingestion_runner.execute(source, execution, adapter, store)
    healthy = {'PUBLISHED','UNCHANGED','WAITING_FOR_PUBLICATION','INCOMPLETE_SOURCE_FILES','NOT_DUE'}
    return {'format':'current-amc-discovery-workload-v5', 'allocation_sha256':SHA256,
            'git_sha':environment['DEPLOYMENT_GIT_SHA'],'task_index':index,
            'source_key':source,'execution_id':execution,
            'status':'completed' if observed['state'] in healthy and observed.get('diagnostic_persistence')!='UNCONFIRMED' else 'failed',
            'discovery':observed,'live_consumer_acceptance':False}
