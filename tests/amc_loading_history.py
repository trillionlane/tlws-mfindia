"""Finite current byte restoration for older release evidence tests only."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SHA256 = '9e68e1ad4e62d9726360657a23c870f245f2d82834439fd7afe1f86ca115243d'


@lru_cache(maxsize=1)
def manifest():
    raw = (ROOT / 'docs/AMC_LOADING_CURRENT_SOURCE_CHANGES_20261105.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA256
    return json.loads(raw)


def previous(path, data):
    name = Path(path).resolve().relative_to(ROOT).as_posix()
    row = manifest()['modified'].get(name)
    if row is None or hashlib.sha256(data).hexdigest() != row['current_sha256']:
        return data  # Earlier chains still validate their own exact historical states.
    old = subprocess.check_output(['git', 'show', manifest()['base'] + ':' + name], cwd=ROOT)
    assert hashlib.sha256(old).hexdigest() == row['previous_sha256']
    return old
