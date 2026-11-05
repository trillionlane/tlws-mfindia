"""Test-only full-file previous-stage verification, separate from runtime pins."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
FILE='docs/HDFC_DYNAMIC_CURRENT_SOURCE_CHANGES_20261105.json'
SHA256='1b4a4d01778c7a9b64085988fa323369068a28d0d21c7215206f72a444c08c2a'


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
