"""Test-only full-file previous-stage verification, separate from runtime pins."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
FILE='docs/HDFC_DYNAMIC_CURRENT_SOURCE_CHANGES_20261105.json'
SHA256='a2e7b13d5252beb49eb97339976195f6640f30d06d4ad32c54bf14fcc1a7bb84'
LATEST_SHA256='7fa0c1119ab05c8ab1a4508c4893cd555b196d0190c3c3a1a8b84c1c3db61068'


def previous(path,data):
    # Explicit current policy-neutral changes restore whole reviewed files;
    # this is test-only historical composition, never executable authority.
    latest_raw=(ROOT/'docs/HDFC_INSTALLER_CURRENT_SOURCE_CHANGES_20261109.json').read_bytes()
    assert hashlib.sha256(latest_raw).hexdigest()==LATEST_SHA256
    latest=json.loads(latest_raw)
    name=Path(path).resolve().relative_to(ROOT).as_posix()
    row=latest['modified'].get(name)
    if row is not None and hashlib.sha256(data).hexdigest()==row['current_sha256']:
        old=subprocess.check_output(['git','show',latest['base']+':'+name],cwd=ROOT)
        assert hashlib.sha256(old).hexdigest()==row['previous_sha256']
        data=old
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
