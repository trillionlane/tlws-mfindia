"""Current packaged CLI and immutable profile bindings; no live DB/provider."""
import importlib.util
import json
import os
from pathlib import Path
import pytest

from mfdataindia.enrichment import ingestion_workload as workload, book_sources
from test_allocated_amc_workload import environment

ROOT = Path(__file__).resolve().parents[1]


def entrypoint():
    spec = importlib.util.spec_from_file_location('amc_gate_main',ROOT/'scripts/refresh_registered_amc_gate_dev.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def set_environment(monkeypatch, **changes):
    for name in tuple(os.environ):
        if name.startswith(('PG','CLOUD_RUN_')):
            monkeypatch.delenv(name)
    for name,value in environment(**changes).items():
        monkeypatch.setenv(name,value)


@pytest.mark.parametrize('only', [False,True])
def test_real_environ_mapping_local_validation_has_zero_database_or_provider_io(monkeypatch,capsys,only):
    set_environment(monkeypatch,CLOUD_RUN_TASK_COUNT='1' if only else '6',CLOUD_RUN_TASK_INDEX='0' if only else '5')
    monkeypatch.setattr(workload.psycopg,'connect',lambda *a,**k:pytest.fail('validation touched database'))
    entrypoint().main(['--validate',*(['--kotak-only'] if only else [])],source_root=ROOT)
    result = json.loads(capsys.readouterr().out)
    assert result['database_connections']==result['provider_requests']==0
    assert result['database_and_provider_readiness']=='UNVERIFIED'
    assert isinstance(os.environ,os._Environ)


@pytest.mark.parametrize('change', [
    {'CLOUD_RUN_TASK_ATTEMPT':'1'},{'CLOUD_RUN_JOB':'unrelated'},
    {'CLOUD_RUN_EXECUTION':'arbitrary-execution'}, {'CLOUD_RUN_TASK_COUNT':'7'},
    {'PGOPTIONS':'-c role=postgres'},{'DEPLOYMENT_GIT_SHA':'not-a-release'},
])
def test_wrong_runtime_inputs_fail_before_dispatch_without_spending_identity(monkeypatch,capsys,change):
    set_environment(monkeypatch,**change)
    monkeypatch.setattr(workload,'execute',lambda *a,**k:pytest.fail('dispatched'))
    with pytest.raises(SystemExit) as stopped:
        entrypoint().main([],source_root=ROOT)
    result = json.loads(capsys.readouterr().out)
    assert stopped.value.code==1 and result['stage']=='validation'
    assert result['publication_effect']=='NOT_SUBMITTED'


def test_current_profile_preserves_all_six_predecessor_configurations_exactly():
    import subprocess
    from importlib.util import module_from_spec,spec_from_loader
    baseline=module_from_spec(spec_from_loader('gate_baseline_sources',loader=None))
    baseline.__file__=str(ROOT/'src/mfdataindia/enrichment/book_sources.py')
    # Actual predecessor constructors already bind exact predecessor modules
    # through the finite current compatibility manifest, independently asserted
    # by whole-byte restoration below. Compare the retained committed results.
    from mfdataindia.enrichment.amc_gate_source_revision import previous_bytes
    source=subprocess.check_output(['git','show','96802df804e5fc8596fc75caa35e3a6e8aa5fd11:src/mfdataindia/enrichment/book_sources.py'],cwd=ROOT)
    exec(compile(source,baseline.__file__,'exec'),baseline.__dict__)
    class PredecessorPath(type(Path())):
        def read_bytes(self):
            raw=super().read_bytes()
            if self.suffix=='.py' and self.resolve().is_relative_to(ROOT/'src'):
                return previous_bytes(self,data=raw)
            return raw
    baseline.Path=PredecessorPath
    for version in range(1,7):
        assert book_sources.configuration(version)==baseline.configuration(version)
    for name in ('book_sources','book_store','sbi_runtime','kotak_runtime','uti_runtime','monthly_store','kotak_current_workload'):
        path=ROOT/f'src/mfdataindia/enrichment/{name}.py'
        old=subprocess.check_output(['git','show','96802df804e5fc8596fc75caa35e3a6e8aa5fd11:'+str(path.relative_to(ROOT))],cwd=ROOT)
        assert previous_bytes(path)==old
        with pytest.raises(ValueError):
            previous_bytes(path,data=path.read_bytes()+b'\n# unknown bytes')
    allocation=workload.allocation(ROOT)
    assert allocation['book_configuration_sha256']!=__import__('mfdataindia.review.policy',fromlist=['fingerprint']).fingerprint(book_sources.configuration(6))
    assert book_sources.CURRENT_VERSION==5 and book_sources.registry(7)['sources'][-1]['adapter']=='uti-english-monthly-book-v1'


@pytest.mark.parametrize('effect', ['UNKNOWN','COMMITTED_UNVERIFIED','PUBLICATION_VERIFIED'])
def test_real_cli_reporting_failure_does_not_downgrade_strongest_result(monkeypatch,capsys,effect):
    set_environment(monkeypatch)
    module=entrypoint()
    monkeypatch.setattr(workload,'verify_database',lambda *a:None)
    observation={'publication_effect':effect,'source_key':'uti-expanded','execution_id':environment()['CLOUD_RUN_EXECUTION'],'state':'PUBLISHED' if effect=='PUBLICATION_VERIFIED' else 'PUBLICATION_OUTCOME_UNKNOWN'}
    monkeypatch.setattr(workload,'execute',lambda *a:{'status':'completed','discovery':observation})
    calls=[]
    def print_once(*args,**kwargs):
        calls.append(1)
        if len(calls)==1:
            raise OSError('SYNTHETIC_SECRET_MUST_NOT_ESCAPE')
        __import__('builtins').print(*args,**kwargs)
    monkeypatch.setattr(module,'print',print_once,raising=False)
    with pytest.raises(SystemExit) as stopped:
        module.main([],source_root=ROOT)
    raw=capsys.readouterr().out
    assert stopped.value.code==1 and 'SYNTHETIC_SECRET' not in raw
    result=json.loads(raw)
    assert result['publication_effect']==effect and result['discovery']==observation
