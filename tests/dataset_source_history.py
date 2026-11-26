"""Finite whole-file current inverses for historical tests only; no runtime use."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PRODUCER_DEPENDENCY_BASE = '4b2244fbe45d90bc315b4ba48f2967d6869db002'
# Only the approved dev-validator dependency delta; no historical hash changes.
# The complete current bytes must match before restoring either predecessor.
PRODUCER_DEPENDENCIES = {
    'pyproject.toml': ('776b86656c4ca727a685fa60ab6e1a80621f8465743e31c3bc115be137aeb0e5',
                      'c8a762116b0f0d4538f08c9a81acdff2e9b99805e238b3824afc39e20a2a8c58'),
    'uv.lock': ('ba6835d8def1b5a26f97dadef4f031433a41656ea6cc93fb8107e01e055e985c',
                '2d043415e945fbdfcad39660232f61b445f468d6dde8a7b23feb3e17515d5101'),
}


def source_native_previous(path, data):
    assert type(data) is bytes
    # Intentional current API/schema/contract change. Validate complete bytes
    # before restoring the reviewed predecessor for old release assertions.
    name = Path(path).resolve().relative_to(ROOT).as_posix() if Path(path).resolve().is_relative_to(ROOT) else None
    dependency = PRODUCER_DEPENDENCIES.get(name)
    if dependency and hashlib.sha256(data).hexdigest() == dependency[1]:
        data = subprocess.check_output(['git', 'show', PRODUCER_DEPENDENCY_BASE+':'+name], cwd=ROOT)
        assert hashlib.sha256(data).hexdigest() == dependency[0], name
    current_raw = (ROOT / 'docs/NSE_SOURCE_NATIVE_CURRENT_SOURCE_CHANGES_20261119.json').read_bytes()
    assert hashlib.sha256(current_raw).hexdigest() == '03bb7b5f0fcfad32aa3e84ad6d30921f67745b42f2ae583a9619b1fc015aecfb'
    current = json.loads(current_raw)
    row = current['modified'].get(name)
    if row and hashlib.sha256(data).hexdigest() == row['current_sha256']:
        data = subprocess.check_output(['git', 'show', current['base']+':'+name], cwd=ROOT)
        assert hashlib.sha256(data).hexdigest() == row['previous_sha256'], name
    return data


def previous(path, data):
    assert type(data) is bytes
    from amc_loading_history import previous as amc_previous
    data = source_native_previous(path, data)
    data = amc_previous(path, data)
    try:
        name = Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return data
    document = json.loads((ROOT / 'docs/DATASET_COVERAGE_CURRENT_SOURCE_CHANGES_20261104.json').read_text())
    row = document['modified'].get(name)
    if row is None:
        return data
    digest = hashlib.sha256(data).hexdigest()
    if digest == row['previous_sha256']:
        return data
    if digest in row.get('historical_passthrough_sha256', []):
        return data  # Exact previously restored whole files, never ignored bytes.
    assert digest == row['current_sha256'], name
    old = subprocess.check_output(['git','show',document['base']+':'+name],cwd=ROOT)
    assert hashlib.sha256(old).hexdigest() == row['previous_sha256'], name
    return old
