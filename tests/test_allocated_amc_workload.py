"""Provider-free process allocation, real PDF workers, never live ingestion."""
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject
import pytest
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'scripts/refresh_allocated_amc_books_dev.py'
JOB = 'tlws-mf-official-facts-refresh-dev'


def environment(**changes):
    return {'MFDATAINDIA_DSN': make_conninfo(
        host='/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev',
        dbname='mfdataindia', user='mfdata_verified_generator',
        password='synthetic-secret-marker-not-a-real-credential'),
        'DEPLOYMENT_GIT_SHA': 'a' * 40, 'CLOUD_RUN_JOB': JOB,
        'CLOUD_RUN_EXECUTION': JOB + '-synthetic', 'CLOUD_RUN_TASK_INDEX': '5',
        'CLOUD_RUN_TASK_COUNT': '6', 'CLOUD_RUN_TASK_ATTEMPT': '0', **changes}


def entrypoint():
    spec = importlib.util.spec_from_file_location('allocated_amc_test_entry', ENTRY)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_full_factory_fixture_keeps_all_deployed_features_and_both_calculator_installers(monkeypatch):
    # Provider-free wiring check only: actual lifespan/SQL semantics are tested
    # by the hosted PostgreSQL case, not by this intercepted factory call.
    from mfdataindia.review import app
    from mfdataindia.calculators import store as forward_store, historical_store
    from test_allocated_amc_workload_postgres import CALCULATOR_POLICY_STORES
    monkeypatch.setattr(app, 'create_review_app', lambda dsn, **kwargs: (dsn, kwargs))
    sentinel = object()
    dsn, options = app.create_amc_books_app('synthetic-not-connected', verifier=sentinel)
    assert dsn == 'synthetic-not-connected' and options.pop('verifier') is sentinel
    assert options == dict.fromkeys((
        'research', 'calendar', 'rolling', 'calculators', 'historical', 'enrichment',
        'official_facts', 'portfolios', 'overlap', 'distributions', 'idcw_catalog',
        'current_facts', 'monthly_facts', 'fact_refresh', 'fact_verification',
        'amc_coverage', 'multi_amc', 'amc_books'), True)
    assert CALCULATOR_POLICY_STORES == (forward_store, historical_store)


@pytest.mark.parametrize('kind', ['forward', 'historical'])
def test_real_calculator_validators_reject_missing_or_different_current_policy(kind):
    # Recording SELECT transport; no assertion here claims native DB evidence.
    from copy import deepcopy
    from mfdataindia.calculators import forward, historical, store, historical_store
    from mfdataindia.review.policy import fingerprint
    engine, validator = {'forward': (forward, store),
                         'historical': (historical, historical_store)}[kind]
    expected = engine.policy()
    class Connection:
        document = None
        def execute(self, command, parameters):
            assert command == 'SELECT document FROM mf_review.policies WHERE policy_hash=%s'
            assert parameters == (fingerprint(expected),)
            return self
        def fetchone(self):
            return {'document': self.document} if self.document is not None else None
    conn = Connection()
    with pytest.raises(ValueError, match='policy not installed or differs'):
        validator.installed_policy(conn)
    conn.document = expected
    assert validator.installed_policy(conn) == expected
    conn.document = {**deepcopy(expected), 'unapproved': True}
    with pytest.raises(ValueError, match='policy not installed or differs'):
        validator.installed_policy(conn)


def test_native_replay_input_uses_scheduled_catalogues_not_owner_bootstrap(monkeypatch):
    from mfdataindia.enrichment import refresh_store
    from test_allocated_amc_workload_postgres import scheduled_replay_receipt
    calls = []
    connection = object()
    def retain(conn, url, media, raw):
        assert conn is connection
        calls.append((url, media, raw))
        return str(len(calls)) * 64
    monkeypatch.setattr(refresh_store, 'append_catalog', retain)
    snapshots = [{'snapshot_id': str(i) * 64, 'content_hash': 'a' * 64,
                  'disclosure_as_of': '2026-08-31'} for i in (1, 2)]
    body = scheduled_replay_receipt(connection, snapshots, JOB + '-synthetic-replay')
    assert body['mode'] == 'scheduled_refresh' and body['status'] == 'unchanged'
    assert body['catalog_ids'] == ['1' * 64, '2' * 64]
    assert body['snapshot_ids'] == ['1' * 64, '2' * 64]
    assert body['source_rows_changed'] == 0
    assert [media for _, media, _ in calls] == ['text/html', 'application/json']
    assert all(url.startswith('https://mutualfund.adityabirlacapital.com/')
               and type(raw) is bytes for url, _, raw in calls)


def test_historical_command_still_rejects_unallocated_valid_looking_invocation(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    old = runpy.run_path(str(ROOT / 'scripts/refresh_amc_books_dev.py'))
    with pytest.raises(ValueError, match='source authority unallocated'):
        old['run'](environment(), ROOT)


def test_supported_allocation_validates_real_environment_mapping_before_runtime(monkeypatch, capsys):
    from mfdataindia.enrichment import book_runtime, allocated_amc_workload as allocated
    module = entrypoint()
    calls = []
    monkeypatch.setattr(allocated, 'verify_database', lambda dsn, document: calls.append('database-policy'))
    def execute(dsn, execution, *, task_index):
        from mfdataindia.enrichment import book_sources
        assert book_sources.registry()['version'] == 4
        assert book_sources.source('uti-expanded')['adapter'] == 'uti-english-monthly-book-v1'
        calls.append(('execute', execution, task_index))
        return {'status': 'completed', 'source_key': 'uti-expanded', 'result': {'status': 'unchanged'}}
    monkeypatch.setattr(book_runtime, 'execute', execute)
    for key, val in environment().items():
        monkeypatch.setenv(key, val)
    module.main([], source_root=ROOT)
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'completed'
    assert result['allocation_id'] == 'official-amc-dev-workload-v1'
    assert calls == ['database-policy', ('execute', JOB + '-synthetic', 5)]
    from mfdataindia.enrichment import book_sources
    assert book_sources.registry()['version'] == 5
    assert 'synthetic-secret-marker' not in json.dumps(result)


@pytest.mark.parametrize('changes,reason', [
    ({'CLOUD_RUN_JOB': 'unrelated'}, 'RUNTIME_IDENTITY'),
    ({'CLOUD_RUN_EXECUTION': JOB + '-bad/slash'}, 'RUNTIME_IDENTITY'),
    ({'CLOUD_RUN_TASK_COUNT': '7'}, 'RUNTIME_IDENTITY'),
    ({'CLOUD_RUN_TASK_INDEX': '6'}, 'RUNTIME_IDENTITY'),
    ({'CLOUD_RUN_TASK_ATTEMPT': '1'}, 'RUNTIME_IDENTITY'),
    ({'DEPLOYMENT_GIT_SHA': 'not-a-commit'}, 'RUNTIME_IDENTITY'),
    ({'PGOPTIONS': '-c role=mfdata_admin'}, 'CONNECTION_OVERRIDE'),
    ({'MFDATAINDIA_DSN': 'synthetic-secret-marker malformed'}, 'CONNECTION_INVALID'),
    ({'MFDATAINDIA_DSN': make_conninfo(host='/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev',
       dbname='mfdataindia', user='mfdata_admin', password='synthetic-secret-marker')}, 'CONNECTION_INVALID'),
    ({'MFDATAINDIA_DSN': environment()['MFDATAINDIA_DSN'] + " options='-c role=mfdata_admin'"}, 'CONNECTION_INVALID'),
    ({'MFDATAINDIA_DSN': environment()['MFDATAINDIA_DSN'] + ' hostaddr=127.0.0.1'}, 'CONNECTION_INVALID'),
])
def test_invalid_runtime_inputs_stop_before_database_or_provider(changes, reason, monkeypatch, capsys):
    from mfdataindia.enrichment import allocated_amc_workload as allocated, book_runtime
    module = entrypoint()
    monkeypatch.setattr(allocated, 'verify_database', lambda *a: pytest.fail('database reached'))
    monkeypatch.setattr(book_runtime, 'execute', lambda *a, **k: pytest.fail('source reached'))
    for key in list(os.environ):
        if key.startswith(('CLOUD_RUN_', 'PG')):
            monkeypatch.delenv(key)
    for key, val in environment(**changes).items():
        monkeypatch.setenv(key, val)
    with pytest.raises(SystemExit) as stopped:
        module.main([], source_root=ROOT)
    assert stopped.value.code == 1
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert reason in value['reason']
    assert value['source_effect'] == 'NOT_SUBMITTED'
    assert value['stage'] == 'validation'
    assert 'synthetic-secret-marker' not in output.out + output.err


def test_validate_is_local_only_and_explicitly_not_runtime_readiness(monkeypatch, capsys):
    from mfdataindia.enrichment import allocated_amc_workload as allocated, book_runtime
    monkeypatch.setattr(allocated, 'verify_database', lambda *a: pytest.fail('database reached'))
    monkeypatch.setattr(book_runtime, 'execute', lambda *a, **k: pytest.fail('source reached'))
    for key, val in environment().items():
        monkeypatch.setenv(key, val)
    entrypoint().main(['--validate'], source_root=ROOT)
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'local_validation_passed'
    assert result['database_and_provider_readiness'] == 'UNVERIFIED'
    assert result['database_connections'] == result['provider_requests'] == 0


def test_installed_check_uses_supported_allocation_and_pdf_without_source_submission(monkeypatch, capsys):
    from mfdataindia.enrichment import allocated_amc_workload as allocated, book_runtime
    calls = []
    monkeypatch.setattr(allocated, 'verify_database', lambda *a: calls.append('readonly-database'))
    monkeypatch.setattr(book_runtime, 'execute', lambda *a, **k: pytest.fail('source submitted'))
    for key, val in environment().items():
        monkeypatch.setenv(key, val)
    entrypoint().main(['--check-installed'], source_root=ROOT)
    value = json.loads(capsys.readouterr().out)
    assert value['status'] == 'installed_policy_verified'
    assert value['synthetic_bounded_pdf'] == 'PASS'
    assert value['provider_requests'] == value['database_mutations'] == 0
    assert calls == ['readonly-database']


@pytest.mark.parametrize('kind', ['descriptor', 'source', 'dependency', 'configuration'])
def test_allocation_checks_actual_source_and_dependency_not_environment_flags(kind, monkeypatch):
    from mfdataindia.enrichment import allocated_amc_workload as allocated, book_sources
    original = Path.read_bytes
    if kind == 'dependency':
        monkeypatch.setattr(allocated, 'version', lambda _: '6.20.1')
    elif kind == 'configuration':
        monkeypatch.setattr(book_sources, 'CURRENT_VERSION', 4)
    else:
        def changed(path):
            raw = original(path)
            if path.name == ('official-amc-dev-workload-v1.json' if kind == 'descriptor' else '_monthly_pdf_worker.py'):
                return raw + b'\n# synthetic drift'
            return raw
        monkeypatch.setattr(Path, 'read_bytes', changed)
    with pytest.raises(allocated.AllocationError):
        allocated.validate(environment(PDF_SOURCE_ALLOW='true', SKIP_PDF_SECURITY='true'), ROOT)


def test_historical_and_current_allocation_have_distinct_meanings():
    from mfdataindia.enrichment import allocated_amc_workload as allocated, pdf_source_dispatch
    result = allocated.allocation(ROOT)
    assert result['live_execution_approval'] is False
    assert result['dormant_successor_decoder_allocation'] is False
    with pytest.raises(ValueError, match='source authority unallocated'):
        pdf_source_dispatch.require_source_authority()


@pytest.mark.parametrize('stage', ['database_policy', 'source_execution'])
def test_database_and_execution_failure_are_safe_and_never_replayed(stage, monkeypatch, capsys):
    from mfdataindia.enrichment import allocated_amc_workload as allocated, book_runtime
    calls = []
    def database(*args):
        calls.append('database')
        if stage == 'database_policy':
            raise RuntimeError('synthetic-secret-marker dsn and arbitrary provider body')
    def execute(*args, **kwargs):
        calls.append('execute')
        raise RuntimeError('synthetic-secret-marker SQL and arbitrary provider body')
    monkeypatch.setattr(allocated, 'verify_database', database)
    monkeypatch.setattr(book_runtime, 'execute', execute)
    for key, val in environment().items():
        monkeypatch.setenv(key, val)
    with pytest.raises(SystemExit) as stopped:
        entrypoint().main([], source_root=ROOT)
    assert stopped.value.code == 1
    out = capsys.readouterr()
    value = json.loads(out.out)
    assert value['stage'] == stage
    assert value['source_effect'] == ('UNKNOWN' if stage == 'source_execution' else 'NOT_SUBMITTED')
    assert calls == (['database', 'execute'] if stage == 'source_execution' else ['database'])
    assert 'synthetic-secret-marker' not in out.out + out.err


def original_pdf(*, encrypted=False, malformed=False):
    if malformed:
        return b'%PDF-1.7\nsynthetic-invalid-pdf'
    w = PdfWriter()
    p = w.add_blank_page(width=200, height=200)
    s = DecodedStreamObject()
    s.set_data(b'')
    p.replace_contents(s)
    if encrypted:
        w.encrypt('synthetic-only')
    output = io.BytesIO()
    w.write(output)
    return output.getvalue()


@pytest.mark.parametrize('kind', ['monthly', 'amc', 'kotak', 'uti'])
@pytest.mark.parametrize('invalid', [None, 'encrypted', 'malformed'])
def test_actual_allocated_dependency_and_retained_bounded_pdf_workers(kind, invalid):
    from mfdataindia.enrichment import allocated_amc_workload as allocated
    from mfdataindia.enrichment import monthly_pdf, amc_book_pdf, layout_book_pdf, uti_book_pdf
    allocated.allocation(ROOT)
    raw = original_pdf(encrypted=invalid == 'encrypted', malformed=invalid == 'malformed')
    decoders = {'monthly': lambda: monthly_pdf.extract(raw, start=1, end=1),
                'amc': lambda: amc_book_pdf.extract(raw),
                'kotak': lambda: layout_book_pdf.extract(raw, profile='kotak'),
                'uti': lambda: uti_book_pdf.extract(raw)}
    if invalid:
        with pytest.raises(ValueError):
            decoders[kind]()
    else:
        result = decoders[kind]()
        assert result['extractor_version'] == '6.21.4'
        assert result['document_pages'] == 1
        assert result['pages'][0]['text'] == ''


def test_allocated_patched_runtime_preserves_actual_uti_aum_tokens_and_geometry():
    from mfdataindia.enrichment import allocated_amc_workload as allocated, uti_book_pdf_v3
    allocated.allocation(ROOT)
    make = runpy.run_path(str(ROOT / 'scripts/smoke_uti_aum_decoder.py'))['fixture']
    for joined in (False, True):
        result = uti_book_pdf_v3.extract(make(joined=joined))
        assert result['extractor_version'] == '6.21.4'
        row = result['pages'][0]['physical_aum_row']
        assert row['spans'][-2]['text'] == '4,094.35'
        assert result['whole_document_semantic_coverage'] is False


def test_allocation_does_not_extend_expired_known_document_fallback():
    from datetime import datetime, timezone
    from mfdataindia.enrichment import allocated_amc_workload as allocated, kotak_known_document
    allocated.allocation(ROOT)
    request = {'expected_period': '2026-09-30', 'source_url': kotak_known_document.CATALOGUE_URL}
    assert kotak_known_document.original(request, datetime(2026, 11, 2, tzinfo=timezone.utc)) is None


def test_allocation_reports_actual_blank_page_policy_without_claiming_dormant_guards():
    from mfdataindia.enrichment import allocated_amc_workload as allocated, amc_book_pdf
    dependency = allocated.allocation(ROOT)['dependency']
    assert dependency['format'] == 'allocated-pdf-dependency-compatibility-v1'
    assert dependency['missing_contents'] == 'retained historical decoder: empty page text, not source completeness'
    assert dependency['dormant_successor_missing_contents'] == 'reject; explicit zero-byte stream accepted'
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)
    result = amc_book_pdf.extract(output.getvalue())
    assert result['pages'][0]['text'] == ''
    assert result['whole_document_semantic_coverage'] is False
    assert allocated.allocation(ROOT)['dormant_successor_decoder_allocation'] is False


@pytest.mark.parametrize('joined', [False, True])
def test_real_nonempty_pdf_through_all_current_allocated_workers(joined):
    from mfdataindia.enrichment import allocated_amc_workload as allocated
    from mfdataindia.enrichment import amc_book_pdf, layout_book_pdf, monthly_pdf, uti_book_pdf
    allocated.allocation(ROOT)
    make = runpy.run_path(str(ROOT / 'scripts/smoke_uti_aum_decoder.py'))['fixture']
    raw = make(joined=joined)
    for extract, coverage_key in (
            (amc_book_pdf.extract, 'whole_document_semantic_coverage'),
            (uti_book_pdf.extract, 'whole_document_semantic_coverage'),
            (lambda b: layout_book_pdf.extract(b, profile='kotak'), 'whole_document_semantic_coverage'),
            (lambda b: monthly_pdf.extract(b, start=1, end=1), 'whole_document_coverage')):
        result = extract(raw)
        assert result['extractor_version'] == '6.21.4'
        assert result['document_pages'] == 1
        assert '4,094.35' in result['pages'][0]['text']
        assert result[coverage_key] is False


@pytest.mark.parametrize('version', [4, 5])
def test_fixture_migration_plan_ends_with_the_real_selected_sealing_body(version, monkeypatch):
    import reader_execution_native_fixture as native
    from mfdataindia.enrichment import book_store
    selected = []
    class PlanCaptured(Exception):
        pass
    class Store:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def apply_migrations(self, root, *, files):
            selected.extend(files)
            raise PlanCaptured()
    monkeypatch.setattr(native, 'PostgresStore', Store)
    with pytest.raises(PlanCaptured):
        native.populate('not-a-database-connection', monkeypatch, book_version=version)
    # Provider-free source composition, not PostgreSQL execution. Native CI
    # separately verifies the real installed prosrc through the unchanged guard.
    bodies = [(ROOT / 'sql/optional' / name).read_text().split('AS $$', 1)[1].split('$$;', 1)[0]
              for name in selected if 'CREATE OR REPLACE FUNCTION mf_book_facts.seal_input()'
              in (ROOT / 'sql/optional' / name).read_text()]
    assert bodies[-1] == book_store.migration_source(version=version).split('AS $$', 1)[1].split('$$;', 1)[0]


def test_reporting_failure_keeps_observed_completion_and_never_resubmits(monkeypatch):
    import builtins
    from mfdataindia.enrichment import allocated_amc_workload as allocated, book_runtime
    calls, reports = [], []
    monkeypatch.setattr(allocated, 'verify_database', lambda *a: None)
    def execute(*a, **k):
        calls.append('execute')
        return {'status': 'completed'}
    monkeypatch.setattr(book_runtime, 'execute', execute)
    for key, val in environment().items():
        monkeypatch.setenv(key, val)
    def print_failed(value, **kwargs):
        reports.append(json.loads(value))
        raise OSError('synthetic-secret-marker broken stdout')
    monkeypatch.setattr(builtins, 'print', print_failed)
    with pytest.raises(SystemExit) as stopped:
        entrypoint().main([], source_root=ROOT)
    assert stopped.value.code == 1 and calls == ['execute']
    assert reports[-1]['stage'] == 'reporting'
    assert reports[-1]['source_effect'] == 'COMPLETED_RUNTIME_RESULT'
    assert reports[-1]['reason'] == 'ALLOCATION_REPORTING_FAILED'
    assert 'synthetic-secret-marker' not in json.dumps(reports)


def test_trusted_registry_selection_is_context_local_and_preserves_all_historical_policies():
    from concurrent.futures import ThreadPoolExecutor
    from mfdataindia.enrichment import book_sources
    from mfdataindia.review.policy import fingerprint
    original = {v: fingerprint(book_sources.configuration(v)) for v in book_sources.REGISTRIES}
    def check(v):
        with book_sources.select_registry(v):
            assert book_sources.registry()['version'] == v
            assert fingerprint(book_sources.configuration()) == original[v]
            assert book_sources.registry(5)['version'] == 5
            assert book_sources.CURRENT_VERSION == 5
        return book_sources.registry()['version']
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(check, (4, 5, 7, 8))) == [5, 5, 5, 5]
    with pytest.raises(RuntimeError):
        with book_sources.select_registry(4):
            raise RuntimeError('synthetic context exit')
    assert book_sources.registry()['version'] == 5
    assert original == {v: fingerprint(book_sources.configuration(v)) for v in book_sources.REGISTRIES}
    for bad in (True, '4', 0, 9):
        with pytest.raises(ValueError):
            with book_sources.select_registry(bad):
                pytest.fail('invalid version selected')
