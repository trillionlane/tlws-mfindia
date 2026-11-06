"""One explicit Kotak layout-v2 allocation on the existing DEV generator job.

Source count, cohort, target, identities and all source/SQL/resource bounds are
retained. New deployment/execution approval is not supplied by this descriptor.
"""
import hashlib
import json
from importlib import import_module
from pathlib import Path
from mfdataindia.enrichment import allocated_amc_workload as prior, book_sources, kotak_runtime
from mfdataindia.review.policy import fingerprint

FILE = 'config/official-kotak-layout-dev-workload-v2.json'
SHA256 = 'bea38196fdf266ea1e3fc84d750e77192f4861bdb750bf21671952bc0c9d5a58'


def allocation(root):
    root = Path(root)
    # Full retained allocation validation still runs, including dependency,
    # six-source mapping, financial policies and exact finite compatibility.
    prior.allocation(root)
    raw = (root / FILE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError('KOTAK_CURRENT_ALLOCATION_MISMATCH')
    document = json.loads(raw)
    if (document['book_configuration_sha256'] != fingerprint(book_sources.configuration(6))
            or document['source_key'] != 'kotak-expanded'
            or document['predecessor_allocation_sha256'] != prior.ALLOCATION_SHA256):
        raise ValueError('KOTAK_CURRENT_POLICY_MISMATCH')
    for name, digest in document['files'].items():
        from mfdataindia.enrichment.amc_gate_source_revision import previous_bytes
        path = root / name
        if name.startswith('src/mfdataindia/') and name.endswith('.py'):
            # Runtime images contain a non-editable wheel, not /app/src.
            # Check the actually imported module as well as any checkout copy.
            module = import_module(name[4:-3].replace('/', '.'))
            installed = Path(module.__file__)
            if installed.is_symlink() or hashlib.sha256(previous_bytes(installed,root=root)).hexdigest() != digest:
                raise ValueError('KOTAK_CURRENT_SOURCE_MISMATCH')
            if not path.exists():
                continue
        if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                or hashlib.sha256(previous_bytes(path,root=root)).hexdigest() != digest):
            raise ValueError('KOTAK_CURRENT_SOURCE_MISMATCH')
    return document


def validate(environment, root, *, kotak_only=False):
    # No provider/database I/O or identity consumption during local validation.
    document = allocation(root)
    dsn, index = prior.validate_environment(environment, root, task_count=1 if kotak_only else 6)
    return document, dsn, 4 if kotak_only else index


def execute(dsn, environment, document, index):
    if index != 4:
        result = prior.execute(dsn, environment, index, document)
        return {**result, 'format': 'current-amc-workload-result-v2',
                'allocation_id': document['id'], 'allocation_sha256': SHA256}
    with book_sources.select_registry(6):
        result = kotak_runtime.run(dsn, environment['CLOUD_RUN_EXECUTION'], method=2)
    return {'format': 'kotak-current-workload-result-v2',
            'allocation_sha256': SHA256, 'git_sha': environment['DEPLOYMENT_GIT_SHA'],
            'source_key': 'kotak-expanded', 'execution_id': environment['CLOUD_RUN_EXECUTION'],
            'status': 'completed' if result['status'] in ('updated', 'unchanged') else 'failed',
            'receipt': result, 'live_consumer_acceptance': False}
