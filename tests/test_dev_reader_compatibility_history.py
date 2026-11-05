"""Current smoke is tested directly; old release assertions retain whole bytes."""
import hashlib
from pathlib import Path
import subprocess

import pytest
import yaml

from dev_reader_compatibility_history import manifest, previous
from dataset_source_history import previous as dataset_previous

ROOT = Path(__file__).resolve().parents[1]
BASE = '84f6c91976ef6da0a8d8e4dfcd2f42a86a2632d2'


def test_only_explicit_current_sources_restore_whole_base_bytes():
    doc = manifest()
    assert doc['base'] == BASE and doc['historical_only'] is True
    assert set(doc['modified']) == {'.github/workflows/verify.yml', 'tests/ro03_historical_source.py',
        'src/mfdataindia/enrichment/book_sources.py',
        'src/mfdataindia/review/broad_reader_source_authority.py',
        'src/mfdataindia/review/broad_reader_view_profile.py',
        'src/mfdataindia/review/reader_execution_contract.py',
        'tests/reader_execution_native_fixture.py', 'tests/test_reader_execution_postgres.py',
        'tests/test_reader_execution_source.py', 'tests/test_release_admission.py'}
    for name, recipe in doc['modified'].items():
        raw = dataset_previous(ROOT / name, (ROOT / name).read_bytes())
        old = subprocess.check_output(['git', 'show', BASE + ':' + name], cwd=ROOT)
        assert previous(ROOT / name, raw) == old
        assert hashlib.sha256(raw).hexdigest() == recipe['current_sha256']
        assert hashlib.sha256(old).hexdigest() == recipe['previous_sha256']
        for mutated in (raw + b'\n# tamper', raw[:-1], raw + raw):
            with pytest.raises(AssertionError):
                previous(ROOT / name, mutated)


def test_actual_workflow_adds_only_two_smokes_to_existing_step_without_gate_changes():
    relative = '.github/workflows/verify.yml'
    before = yaml.load(subprocess.check_output(['git', 'show', BASE + ':' + relative], cwd=ROOT), Loader=yaml.BaseLoader)
    after = yaml.load(dataset_previous(ROOT / relative, (ROOT / relative).read_bytes()), Loader=yaml.BaseLoader)
    steps = after['jobs']['verify']['steps']
    step = next(s for s in steps if s.get('id') == 'original_34')
    old = next(s for s in before['jobs']['verify']['steps'] if s.get('id') == 'original_34')
    added = ''.join('docker run --rm --entrypoint python "' + target + '" /app/scripts/smoke_allocated_amc_workload.py\n'
                    for target in ('$image', '$tools_image'))
    assert step['run'] == old['run'] + added
    step['run'] = old['run']
    assert after == before
