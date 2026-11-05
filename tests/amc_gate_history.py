"""Test-only whole-byte predecessor stage; earlier evidence still verifies itself."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FILE = 'docs/AMC_INGESTION_GATE_CURRENT_SOURCE_CHANGES_20261105.json'
SHA256 = 'dde1aea6b58871f6387d65cd8a26a4a267f1268c9eb52808ec83136b0b41f846'


def previous(path, data):
    raw = (ROOT/FILE).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA256
    name = Path(path).resolve().relative_to(ROOT).as_posix()
    document = json.loads(raw)
    row = document['modified'].get(name)
    if row is None or hashlib.sha256(data).hexdigest() != row['current_sha256']:
        return data
    old = subprocess.check_output(['git','show',document['base']+':'+name],cwd=ROOT)
    assert hashlib.sha256(old).hexdigest() == row['previous_sha256']
    return old
