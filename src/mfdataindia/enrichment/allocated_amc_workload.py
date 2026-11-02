"""One reviewed process allocation for the existing DEV book workload.

The historical deny is unchanged. This entrypoint alone admits the retained
financial/source/decoder policies on the patched dependency. No caller flag,
URL, role, registry, limit or dependency override can allocate another workload.
It is a code contract, not live deployment or execution authorization.
"""
import hashlib
import io
from importlib import import_module
from importlib.metadata import version
import json
from pathlib import Path
import re

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from mfdataindia.review.policy import fingerprint

ALLOCATION_ID = 'official-amc-dev-workload-v1'
ALLOCATION_FILE = 'config/official-amc-dev-workload-v1.json'
ALLOCATION_SHA256 = '4050e4e704195b92a759f2f3c1051d4cea0d539ffe369fd3c96be402f3d9fb63'
JOB = 'tlws-mf-official-facts-refresh-dev'
CONNECTION = 'trillionlane-dev:asia-south1:tlws-mf-data-dev'
DATABASE = 'mfdataindia'
GENERATOR = 'mfdata_verified_generator'
_connect = psycopg.connect


class AllocationError(ValueError):
    """Only fixed, value-free allocation reasons cross the process boundary."""


def _reject(reason):
    raise AllocationError(reason)


def allocation(root):
    """Check committed allocation and actual installed code/dependency, no I/O to providers/DB."""
    root = Path(root)
    path = root / ALLOCATION_FILE
    try:
        raw = path.read_bytes()
        if path.is_symlink() or not 0 < len(raw) <= 65536 or hashlib.sha256(raw).hexdigest() != ALLOCATION_SHA256:
            _reject('ALLOCATION_DOCUMENT_MISMATCH')
        document = json.loads(raw)
        if document['id'] != ALLOCATION_ID or document['format'] != 'official-amc-dev-process-allocation-v1':
            _reject('ALLOCATION_DOCUMENT_MISMATCH')
        if version('pypdf') != document['dependency']['version']:
            _reject('ALLOCATION_DEPENDENCY_MISMATCH')
        for name, expected in document['module_sha256'].items():
            module = import_module('mfdataindia.enrichment.' + name)
            if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() != expected:
                _reject('ALLOCATION_SOURCE_MISMATCH')
        for name, expected in document['file_sha256'].items():
            file = root / name
            if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                _reject('ALLOCATION_SOURCE_MISMATCH')
        from mfdataindia.enrichment import book_sources, multi_facts, refresh_policy
        keys = [s['key'] for s in multi_facts.registry()['sources']]
        keys += [s['key'] for s in book_sources.registry(4)['sources']]
        if (book_sources.CURRENT_VERSION != 5
                or fingerprint(book_sources.configuration(4)) != document['book_configuration_sha256']
                or fingerprint(multi_facts.registry()) != document['multi_registry_sha256']
                or fingerprint(refresh_policy.policy()) != document['monthly_policy_sha256']
                or document['tasks'] != [dict(index=i, source_key=k) for i, k in enumerate(keys)]):
            _reject('ALLOCATION_POLICY_MISMATCH')
        return document
    except AllocationError:
        raise
    except (OSError, KeyError, TypeError, ValueError, ImportError):
        _reject('ALLOCATION_SOURCE_UNAVAILABLE')


def validate(environment, root):
    document = allocation(root)
    if (type(environment) is not dict
            or any(type(k) is not str or type(v) is not str for k, v in environment.items())
            or sum(len(k.encode()) + len(v.encode()) for k, v in environment.items()) > 65536):
        _reject('ALLOCATION_ENVIRONMENT_INVALID')
    if any(k.startswith('PG') for k in environment):
        _reject('ALLOCATION_CONNECTION_OVERRIDE')
    if (environment.get('CLOUD_RUN_JOB') != JOB
            or environment.get('CLOUD_RUN_TASK_COUNT') != '6'
            or environment.get('CLOUD_RUN_TASK_ATTEMPT') != '0'
            or environment.get('CLOUD_RUN_TASK_INDEX') not in tuple(map(str, range(6)))
            or re.fullmatch(re.escape(JOB) + r'-[a-z0-9-]{1,40}',
                            environment.get('CLOUD_RUN_EXECUTION', '')) is None
            or re.fullmatch(r'[0-9a-f]{40}', environment.get('DEPLOYMENT_GIT_SHA', '')) is None):
        _reject('ALLOCATION_RUNTIME_IDENTITY_INVALID')
    dsn = environment.get('MFDATAINDIA_DSN', '')
    if not 0 < len(dsn.encode()) <= 32768:
        _reject('ALLOCATION_CONNECTION_INVALID')
    try:
        parsed = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        _reject('ALLOCATION_CONNECTION_INVALID')
    if (set(parsed) - {'host', 'dbname', 'user', 'password', 'port', 'sslmode', 'connect_timeout', 'application_name'}
            or parsed.get('host') != '/cloudsql/' + CONNECTION
            or parsed.get('dbname') != DATABASE or parsed.get('user') != GENERATOR
            or not parsed.get('password') or parsed.get('port', '5432') != '5432'
            or parsed.get('sslmode', 'disable') not in ('disable', 'require', 'verify-full')):
        _reject('ALLOCATION_CONNECTION_INVALID')
    # Reuse the existing operational input contract rather than replace it.
    from importlib.util import module_from_spec, spec_from_file_location
    script = Path(root) / 'scripts/refresh_official_facts_dev.py'
    spec = spec_from_file_location('_allocated_existing_refresh_validation', script)
    original = module_from_spec(spec)
    spec.loader.exec_module(original)
    try:
        original.validate(environment, Path(root), task_count=6)
    except (KeyError, TypeError, ValueError):
        _reject('ALLOCATION_RUNTIME_INPUT_INVALID')
    return document, dsn, int(environment['CLOUD_RUN_TASK_INDEX'])


def verify_database(dsn, document):
    """Direct non-elevated generator, real installed source policy, read-only preflight."""
    from mfdataindia.review.privileges import verify_runtime
    from mfdataindia.enrichment import book_store
    with _connect(dsn, connect_timeout=10, row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY')
        conn.execute("SET LOCAL statement_timeout='30s'")
        conn.execute("SET LOCAL transaction_timeout='60s'")
        conn.execute("SET LOCAL lock_timeout='5s'")
        identity = conn.execute('SELECT current_database() AS database,current_user AS principal,session_user AS session').fetchone()
        if identity != {'database': DATABASE, 'principal': GENERATOR, 'session': GENERATOR}:
            _reject('ALLOCATION_DATABASE_IDENTITY_MISMATCH')
        verify_runtime(conn, 'generator')
        book_store.verify_acl(conn)
        installed = book_store.installed_configuration(conn)
        if (fingerprint(installed['document']) != document['book_configuration_sha256']
                or installed['configuration_hash'] != document['book_configuration_sha256']):
            _reject('ALLOCATION_INSTALLED_POLICY_MISMATCH')


def execute(dsn, environment, index, document):
    """No new runtime copies, source mappings, SQL, retries or provider overrides."""
    from mfdataindia.enrichment import book_runtime, book_sources
    # Pin the actually installed source method without changing CURRENT_VERSION
    # or monkeypatching module globals. All old orchestration/parsers see v4 in
    # this execution context and reset to their original default afterwards.
    with book_sources.select_registry(4):
        result = book_runtime.execute(dsn, environment['CLOUD_RUN_EXECUTION'], task_index=index)
    status = result.get('status')
    if status not in ('completed', 'failed'):
        _reject('ALLOCATION_RUNTIME_RESULT_INVALID')
    return {'format': 'allocated-amc-workload-result-v1', 'allocation_id': document['id'],
            'allocation_sha256': ALLOCATION_SHA256, 'dependency': document['dependency']['version'],
            'git_sha': environment['DEPLOYMENT_GIT_SHA'],
            'execution_id': environment['CLOUD_RUN_EXECUTION'], 'task_index': index,
            'source_key': document['tasks'][index]['source_key'], 'status': status,
            'consumer_acceptance': False}


def verify_pdf_runtime():
    """A fixed synthetic PDF through the actual bounded worker, never a source fetch."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject
    from mfdataindia.enrichment import amc_book_pdf
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(b'')
    page.replace_contents(stream)
    output = io.BytesIO()
    writer.write(output)
    result = amc_book_pdf.extract(output.getvalue())
    if (result['extractor_version'] != '6.21.4' or result['document_pages'] != 1
            or result['pages'][0]['text'] != ''):
        _reject('ALLOCATION_PDF_RUNTIME_MISMATCH')


def safe_reason(error, stage):
    known = {'ALLOCATION_DOCUMENT_MISMATCH', 'ALLOCATION_DEPENDENCY_MISMATCH',
             'ALLOCATION_SOURCE_MISMATCH', 'ALLOCATION_POLICY_MISMATCH',
             'ALLOCATION_SOURCE_UNAVAILABLE', 'ALLOCATION_ENVIRONMENT_INVALID',
             'ALLOCATION_CONNECTION_OVERRIDE', 'ALLOCATION_RUNTIME_IDENTITY_INVALID',
             'ALLOCATION_CONNECTION_INVALID', 'ALLOCATION_RUNTIME_INPUT_INVALID',
             'ALLOCATION_DATABASE_IDENTITY_MISMATCH', 'ALLOCATION_INSTALLED_POLICY_MISMATCH',
             'ALLOCATION_RUNTIME_RESULT_INVALID', 'ALLOCATION_PDF_RUNTIME_MISMATCH'}
    if (type(error) is AllocationError and error.args and type(error.args[0]) is str
            and error.args[0] in known):
        return error.args[0]
    return {'validation': 'ALLOCATION_VALIDATION_FAILED',
            'database_policy': 'ALLOCATION_DATABASE_CHECK_FAILED',
            'source_execution': 'ALLOCATION_SOURCE_EFFECT_UNCONFIRMED',
            'pdf_compatibility': 'ALLOCATION_PDF_CHECK_FAILED',
            'reporting': 'ALLOCATION_REPORTING_FAILED'}[stage]
