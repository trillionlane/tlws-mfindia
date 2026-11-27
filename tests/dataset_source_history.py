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


CI_PHASE1_BASE = '25b22da659afadf419ef8c3987f4eae70ba54b7d'
CI_PHASE1_PATH = '.github/workflows/verify.yml'
CI_PHASE1_PREVIOUS = '2b58c4c87aa99240a38d6b6e2a564933742be99255db68dd79c003e054f84e34'
CI_PHASE1_CURRENT = 'e424dfc2b021c643b7e7f59637ccc2dd72337e655c3c3f9c561170665e5131ce'
CI_PHASE1_FILES = {
    CI_PHASE1_PATH: (CI_PHASE1_PREVIOUS, CI_PHASE1_CURRENT),
    'tests/test_ci_risk_policy.py': (
        '73d637e1fc32345718237e70da18b0eaf0f430866905b01b51468c5be2e08b2d',
        'f94f98e4ce0705cb00e445989cf78a3f8da8ee5b8b7d833549df32e13540997b'),
    'tests/reader_execution_native_fixture.py': (
        '7b63d27727becf2c8879802865fe3ea0d0091b9be30d0dc792287e16d6b64771',
        '4df28928471a7d016ebfc50bac3f3cae0a2dd8f1d7937c8e23c6b2b0a5c6c4e6'),
    'tests/test_reader_execution_postgres.py': (
        'eab735e0adbddeb5ef0f338495c435599c44a75a918ce832fa94c05f2fcbeda5',
        'fd439e3287fdade04b3b4bafd415f16e0a37bd4b108dc45211353d55db167840'),
    'scripts/run_ci_tests.py': (
        '5f70fe7ee6a7baae45fd3e00a69ca27e63f8f5239f777e1ef08f62176360bd6c',
        '833138ed8112c770e42c4fcb97d9bc71e676cafc9e64863170d6d36cc2ae4b21'),
    'tests/test_certification_input_native.py': (
        'faba515679188843adefa3d602534f92fae16ef62969fc7fc2b7acbdfe73e8b2',
        'e1e7cf5099094f483d48ee6d0a6d7c978c55e31a7140a3f888a54ddbd394936e'),
    'tests/test_verified_review_store.py': (
        'e5bc1408256940c9864946bf6f4cc42bda3387b08d318b68349775e7d3013fbd',
        '0d3f659a021819d485236db45e60eca2e862d7ab72ceff32be573f9f9965e62d'),
}


def ci_phase1_previous(path, data):
    """Restore seven exact reviewed whole files for historical proofs only."""
    assert type(data) is bytes
    candidate = Path(path)
    try:
        name = candidate.absolute().relative_to(ROOT).as_posix()
    except ValueError:
        return data
    binding = CI_PHASE1_FILES.get(name)
    if binding is None:
        return data
    # No symlinked file/parent, escaped path, or non-file may borrow this inverse.
    assert candidate.resolve() == ROOT / name, name
    assert candidate.is_file() and not any(
        part.is_symlink() for part in (candidate, *candidate.parents)), name
    if hashlib.sha256(data).hexdigest() != binding[1]:
        return data  # Unknown bytes still encounter the unchanged historical guard.
    old = subprocess.check_output(['git', 'show', CI_PHASE1_BASE + ':' + name], cwd=ROOT)
    assert hashlib.sha256(old).hexdigest() == binding[0], name
    return old


def source_native_previous(path, data):
    assert type(data) is bytes
    data = ci_phase1_previous(path, data)
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
