"""Current fixed six-task composition; operation history is in PostgreSQL.

The v1/v2 allocations remain explicit predecessors. Actual v3 sources are
pinned in this descriptor, not normalized into historical release evidence.
"""
from importlib import import_module
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from mfdataindia.enrichment import (kotak_current_workload as prior,
    allocated_amc_workload as original, book_sources, ingestion_adapters,
    ingestion_postgres, ingestion_runner, ingestion_gate, ingestion_install)
from mfdataindia.enrichment.ingestion_discovery import SOURCES
from mfdataindia.review.policy import fingerprint

FILE = 'config/official-amc-discovery-dev-workload-v3.json'
SHA256 = 'bacbeefe629d3fa034b3760ce091f775aa8a881b6945368494c7488f79f00bc3'


def allocation(root):
    root = Path(root)
    prior.allocation(root)
    path = root/FILE
    raw = path.read_bytes()
    if path.is_symlink() or not 0 < len(raw) <= 65536 or hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError('INGESTION_ALLOCATION_DIFFERS')
    document = json.loads(raw)
    if (document['book_configuration_sha256'] != fingerprint(book_sources.configuration(7))
            or document['predecessor_allocation_sha256'] != prior.SHA256
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
    with book_sources.select_registry(7):
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
    return {'format':'current-amc-discovery-workload-v3', 'allocation_sha256':SHA256,
            'git_sha':environment['DEPLOYMENT_GIT_SHA'],'task_index':index,
            'source_key':source,'execution_id':execution,
            'status':'completed' if observed['state'] in healthy and observed.get('diagnostic_persistence')!='UNCONFIRMED' else 'failed',
            'discovery':observed,'live_consumer_acceptance':False}
