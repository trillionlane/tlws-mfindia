"""Test-only full-file previous-stage verification, separate from runtime pins."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
FILE='docs/HDFC_DYNAMIC_CURRENT_SOURCE_CHANGES_20261105.json'
SHA256='004b7c84fad3c0c7ca601054d685ac427b62a2cfc0a3908e85acd88552dae775'


def previous(path,data):
    raw=(ROOT/FILE).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==SHA256
    document=json.loads(raw)
    name=Path(path).resolve().relative_to(ROOT).as_posix()
    row=document['modified'].get(name)
    if row is None or hashlib.sha256(data).hexdigest()!=row['current_sha256']:
        return data
    old=subprocess.check_output(['git','show',document['base']+':'+name],cwd=ROOT)
    assert hashlib.sha256(old).hexdigest()==row['previous_sha256']
    return old
