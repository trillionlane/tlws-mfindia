import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from dataset_source_history import previous

ROOT = Path(__file__).resolve().parents[1]
BASE = '6e074b0d32fe5923d8adba384d9784d08ae4afaa'


def test_current_source_changes_are_finite_whole_files_and_reject_tampering():
    document = json.loads((ROOT / 'docs/DATASET_COVERAGE_CURRENT_SOURCE_CHANGES_20261104.json').read_text())
    assert document['historical_only'] and not document['runtime_authority'] and document['base'] == BASE
    assert set(document['modified']) == {'scripts/migrate.py','.github/workflows/verify.yml',
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
    assert after == before and new == old


def test_packaged_ASGI_smoke_runs_actual_routes_without_external_IO():
    result = subprocess.run([sys.executable,
        str(ROOT/'scripts/smoke_dataset_read_api.py')],capture_output=True,text=True,check=True,cwd=ROOT)
    receipt = json.loads(result.stdout.splitlines()[-1])
    assert receipt['packaged_dataset_ASGI_entrypoint'] == 'PASS'
    assert receipt['database_connections'] == receipt['provider_requests'] == receipt['writes'] == 0
