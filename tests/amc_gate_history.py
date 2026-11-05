"""Test-only whole-byte predecessor stage; earlier evidence still verifies itself."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def previous(path, data):
    from mfdataindia.enrichment.amc_gate_source_revision import FILE, SHA256
    raw = (ROOT/FILE).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA256
    name = Path(path).resolve().relative_to(ROOT).as_posix()
    row = json.loads(raw)['versions']['kotak_v6'].get(name)
    if row is None or hashlib.sha256(data).hexdigest() != row['current_sha256']:
        return data
    old = subprocess.check_output(['git','show','6d31b1b82c78f56fa3c36083404e61b7cb8cd38e:'+name],cwd=ROOT)
    assert hashlib.sha256(old).hexdigest() == row['previous_sha256']
    return old
