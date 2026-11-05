"""Actual main/environment validation and fixed current method dispatch, no I/O."""
import importlib.util
import json
import os
from pathlib import Path
import pytest
from mfdataindia.enrichment import kotak_current_workload as current, allocated_amc_workload as prior, book_sources
from test_allocated_amc_workload import environment

ROOT = Path(__file__).resolve().parents[1]


def entrypoint():
    spec = importlib.util.spec_from_file_location('kotak_current_entry', ROOT/'scripts/refresh_kotak_current_dev.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('kotak_only', [False, True])
def test_actual_environ_mapping_snapshot_validation_and_no_io(monkeypatch, capsys, kotak_only):
    assert isinstance(os.environ, os._Environ)
    for key in tuple(os.environ):
        if key.startswith(('PG', 'CLOUD_RUN_')):
            monkeypatch.delenv(key)
    for k,v in environment(CLOUD_RUN_TASK_COUNT='1' if kotak_only else '6', CLOUD_RUN_TASK_INDEX='0' if kotak_only else '4').items():
        monkeypatch.setenv(k,v)
    monkeypatch.setattr(prior, '_connect', lambda *a,**k: pytest.fail('validation opened database'))
    args = ['--validate', '--kotak-only'] if kotak_only else ['--validate']
    entrypoint().main(args, source_root=ROOT)
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'local_validation_passed'
    assert result['database_connections'] == result['provider_requests'] == 0
    assert result['database_and_provider_readiness'] == 'UNVERIFIED'


@pytest.mark.parametrize('changes', [
    {'CLOUD_RUN_TASK_COUNT':'6'}, {'CLOUD_RUN_TASK_INDEX':'4'},
    {'CLOUD_RUN_TASK_ATTEMPT':'1'}, {'CLOUD_RUN_JOB':'other'},
    {'CLOUD_RUN_EXECUTION':'another-job-x'}, {'PGOPTIONS':'-c role=postgres'},
])
def test_one_task_recovery_cannot_silently_run_six_or_impersonate_task4(changes):
    env = environment(CLOUD_RUN_TASK_COUNT='1', CLOUD_RUN_TASK_INDEX='0')
    env.update(changes)
    with pytest.raises(ValueError):
        current.validate(env, ROOT, kotak_only=True)


def test_current_method_is_explicit_and_legacy_default_uti_is_not_upgraded(monkeypatch):
    from mfdataindia.enrichment import kotak_runtime
    seen=[]
    def run(dsn, execution, *, method):
        assert method == 2 and book_sources.registry()['version'] == 6
        assert book_sources.source('uti-expanded')['adapter'] == 'uti-english-monthly-book-v1'
        seen.append((dsn,execution))
        return {'status':'updated'}
    monkeypatch.setattr(kotak_runtime, 'run', run)
    document=current.allocation(ROOT)
    result=current.execute('not-connected', environment(), document, 4)
    assert len(seen)==1 and result['status']=='completed'
    assert book_sources.registry()['version']==5


def test_legacy_v4_authority_full_bytes_stays_exact_and_unknown_modified_bytes_reject():
    from mfdataindia.enrichment.amc_source_revision import previous_bytes
    import hashlib
    import subprocess
    for name in ('book_sources','book_store','kotak_batch','kotak_runtime'):
        path=ROOT/f'src/mfdataindia/enrichment/{name}.py'
        expected=subprocess.check_output(['git','show','7856eeb:'+str(path.relative_to(ROOT))],cwd=ROOT)
        assert previous_bytes(path)==expected
        for tamper in (path.read_bytes()+b'\n# tamper',path.read_bytes()[:-1]):
            with pytest.raises(ValueError):
                previous_bytes(path,tamper)
    assert current.allocation(ROOT)['book_configuration_sha256'] != prior.allocation(ROOT)['book_configuration_sha256']
    assert hashlib.sha256((ROOT/prior.ALLOCATION_FILE).read_bytes()).hexdigest()==prior.ALLOCATION_SHA256


def test_compiler_current_two_period_complete_graph_keeps_all_original_guards():
    from test_kotak_method_postgres import inputs
    from mfdataindia.enrichment import kotak_book
    identity = {'code':112090, 'amc':kotak_book.AMC,
                'scheme_name':'Kotak Flexicap Fund - Regular Plan - Growth',
                'plan':'REGULAR','option':'GROWTH','isins':['INF174K01336']}
    for version, method in ((4,1),(6,2)):
        with book_sources.select_registry(version):
            batches, originals, archives = inputs([identity],method=method)
            assert len(batches)==2 and len(archives)==3
            assert {r['role'] for r in originals}=={'catalogue','directory','index','identity','book'}
            assert all(b['observations'][0]['result']['status']=='usable' for b in batches)
            assert all(len(b['document_refs'])==7 for b in batches)
            if method==2:
                assert batches[0]['observations'][0]['result']['fields']['aum']['value']=='52614.58'
                assert batches[0]['observations'][0]['result']['fields']['base_expense_ratio']['reason']=='same_period_expense_values_conflict'


def test_noneditable_image_layout_uses_exact_installed_module_bytes(tmp_path):
    import shutil
    document = current.allocation(ROOT)
    copied = {current.FILE, prior.ALLOCATION_FILE, 'config/official-amc-gate-source-predecessors-v1.json',
              'config/official-hdfc-dynamic-predecessors-v1.json', *document['files'],
              *prior.allocation(ROOT)['file_sha256']}
    for name in copied:
        if name.startswith('src/'):
            continue
        destination = tmp_path/name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,destination)
    assert not (tmp_path/'src').exists()
    assert current.allocation(tmp_path)==document


def test_noneditable_image_layout_never_accepts_changed_installed_code(tmp_path,monkeypatch):
    import shutil
    from types import SimpleNamespace
    document=current.allocation(ROOT)
    copied = {current.FILE, prior.ALLOCATION_FILE, 'config/official-amc-gate-source-predecessors-v1.json',
              'config/official-hdfc-dynamic-predecessors-v1.json', *document['files'],
              *prior.allocation(ROOT)['file_sha256']}
    for name in copied:
        if not name.startswith('src/'):
            dest=tmp_path/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,dest)
    bad=tmp_path/'changed_module.py'
    bad.write_text('# not the reviewed package\n')
    monkeypatch.setattr(current,'import_module',lambda _:SimpleNamespace(__file__=str(bad)))
    with pytest.raises(ValueError,match='KOTAK_CURRENT_SOURCE_MISMATCH'):
        current.allocation(tmp_path)
