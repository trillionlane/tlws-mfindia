"""Test-only whole-byte predecessor stage; earlier evidence still verifies itself."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FILE = 'docs/AMC_INGESTION_GATE_CURRENT_SOURCE_CHANGES_20261105.json'
SHA256 = 'c625199096981cbf89d947a9fab72b9d165cdd44aec98bf3c29fae3185d2099a'


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
