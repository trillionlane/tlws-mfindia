"""Finite current-source inverses, not runtime authority or ignored regions."""
import hashlib
import json
from pathlib import Path
import subprocess
from dataset_source_history import previous as dataset_previous

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SHA256 = 'd364b2efd124a7897d590475f3250ac6deb546cc7daad1c398dd4f15eb5603b4'


def manifest():
    raw = (ROOT / 'docs/DEV_READER_COMPATIBILITY_SOURCE_CHANGES_20261102.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == MANIFEST_SHA256
    return json.loads(raw)


def previous(path, data):
    assert type(data) is bytes
    data = dataset_previous(path, data)
    try:
        name = Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return data
    row = manifest()['modified'].get(name)
    if row is None:
        return data
    digest = hashlib.sha256(data).hexdigest()
    if digest == row['previous_sha256']:
        return data
    if name == '.github/workflows/verify.yml':
        # RO-02 may already have passed this exact earlier workflow through the
        # current chain. Keep the same explicitly committed historical states.
        old = json.loads((ROOT / 'docs/RO03_CURRENT_SOURCE_CHANGES_20261101.json').read_bytes())
        if digest == old['modified'][name]['previous_sha256']:
            return data
    assert digest == row['current_sha256'], name
    old = subprocess.check_output(['git', 'show', manifest()['base'] + ':' + name], cwd=ROOT)
    assert hashlib.sha256(old).hexdigest() == row['previous_sha256'], name
    return old
