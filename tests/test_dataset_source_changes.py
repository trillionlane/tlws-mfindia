import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import shlex
import sys

import pytest
import yaml

from dataset_source_history import previous

ROOT = Path(__file__).resolve().parents[1]
BASE = '6e074b0d32fe5923d8adba384d9784d08ae4afaa'


def test_current_source_changes_are_finite_whole_files_and_reject_tampering():
    document = json.loads((ROOT / 'docs/DATASET_COVERAGE_CURRENT_SOURCE_CHANGES_20261104.json').read_text())
    assert document['historical_only'] and not document['runtime_authority'] and document['base'] == BASE
    assert document['format']=='dataset-coverage-current-source-changes-v2'
    assert set(document['modified']) == {'Dockerfile','scripts/migrate.py','.github/workflows/verify.yml',
        'tests/dev_reader_compatibility_history.py','tests/test_dev_reader_compatibility_history.py'}
    for name, row in document['modified'].items():
        raw = (ROOT/name).read_bytes()
        old = subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT)
        assert hashlib.sha256(raw).hexdigest() == row['current_sha256']
        assert previous(ROOT/name,raw) == old
        for modified in (raw+b'\n# tamper',raw[:-1],raw+raw):
            with pytest.raises(AssertionError):
                previous(ROOT/name,modified)
    historical = json.loads((ROOT / 'docs/RO03_CURRENT_SOURCE_CHANGES_20261101.json').read_text())['modified']['.github/workflows/verify.yml']
    assert document['modified']['.github/workflows/verify.yml']['historical_passthrough_sha256'] == [
        historical['current_sha256'],historical['previous_sha256']]


def test_image_smoke_is_only_additive_command_in_existing_both_image_loop():
    name = '.github/workflows/verify.yml'
    old = yaml.load(subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT),Loader=yaml.BaseLoader)
    new = yaml.load((ROOT/name).read_bytes(),Loader=yaml.BaseLoader)
    before = next(s for s in old['jobs']['verify']['steps'] if s.get('id')=='original_41')
    after = next(s for s in new['jobs']['verify']['steps'] if s.get('id')=='original_41')
    addition = '  docker run --rm --entrypoint python "$target" /app/scripts/smoke_dataset_read_api.py\n'
    assert after['run'].count(addition) == 1
    after['run'] = after['run'].replace(addition,'')
    assert after == before
    default = next(s for s in new['jobs']['verify']['steps'] if s.get('id')=='original_4')
    commands = ('docker image inspect "$image" --format \'{{json .Config.Cmd}}\' > "$RUNNER_TEMP/dataset-default-command.json"\n'
                'docker run --rm --entrypoint python --mount "type=bind,source=$RUNNER_TEMP/dataset-default-command.json,target=/tmp/dataset-default-command.json,readonly" "$image" /app/scripts/smoke_dataset_read_api.py --image-command-json /tmp/dataset-default-command.json\n')
    assert default['run'].count(commands)==1
    default['run'] = default['run'].replace(commands,'')
    assert new == old


def test_packaged_ASGI_smoke_runs_actual_routes_without_external_IO():
    result = subprocess.run([sys.executable,
        str(ROOT/'scripts/smoke_dataset_read_api.py')],capture_output=True,text=True,check=True,cwd=ROOT)
    receipt = json.loads(result.stdout.splitlines()[-1])
    assert receipt['packaged_dataset_ASGI_entrypoint'] == 'PASS'
    assert receipt['database_connections'] == receipt['provider_requests'] == receipt['writes'] == 0


def test_default_core_image_command_selects_dataset_factory_and_retains_core_routes():
    command = json.loads(next(line[4:] for line in (ROOT/'Dockerfile').read_text().splitlines() if line.startswith('CMD ')))
    assert command[:2]==['sh','-c']
    argv = shlex.split(command[2])
    assert argv[:2]==['exec','uvicorn'] and '--factory' in argv
    module,name = argv[2].split(':')
    app = getattr(importlib.import_module(module),name)('postgresql://fixture:fixture@127.0.0.1:1/fixture')
    from mfdataindia.api.app import create_app as core
    original = core('postgresql://fixture:fixture@127.0.0.1:1/fixture')
    paths = {r.path for r in app.routes}
    assert {r.path for r in original.routes} <= paths
    assert {'/api/datasets/v1','/api/datasets/v1/{dataset_id}',
            '/api/datasets/v1/{dataset_id}/rows','/api/datasets/v1/{dataset_id}/content/{column}'} <= paths


def test_packaged_smoke_loads_the_actual_inspected_image_command(tmp_path):
    raw = next(line[4:] for line in (ROOT/'Dockerfile').read_text().splitlines() if line.startswith('CMD '))
    path = tmp_path/'image-command.json'
    path.write_text(raw)
    result = subprocess.run([sys.executable,str(ROOT/'scripts/smoke_dataset_read_api.py'),
        '--image-command-json',str(path)],capture_output=True,text=True,check=True,cwd=ROOT)
    assert json.loads(result.stdout.splitlines()[-1])['default_image_command_verified'] is True
    prior = subprocess.check_output(['git','show',BASE+':Dockerfile'],cwd=ROOT,text=True)
    path.write_text(next(line[4:] for line in prior.splitlines() if line.startswith('CMD ')))
    refused = subprocess.run([sys.executable,str(ROOT/'scripts/smoke_dataset_read_api.py'),
        '--image-command-json',str(path)],capture_output=True,text=True,cwd=ROOT)
    assert refused.returncode != 0
