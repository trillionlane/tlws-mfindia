"""Exact reviewed CI policy inverses, before ALL historical preservation checks."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PLAN_BYTES = (ROOT / 'docs/CI_RISK_POLICY_LITERAL_PLAN_20261026.json').read_bytes()
assert hashlib.sha256(PLAN_BYTES).hexdigest() == 'e68eadfd84b2d866953a6a131e7f4f89da4e8d27348a2c59c76afffc1d7870d3'
PLAN = json.loads(PLAN_BYTES)
RECIPES = PLAN['exact_changed_old_files']
PRESENTATION_BYTES = (ROOT / 'docs/CI_RISK_POLICY_PRESENTATION_AUTHORITY_20261026.json').read_bytes()
assert hashlib.sha256(PRESENTATION_BYTES).hexdigest() == '7f60c64c1b8bd9f3c9239d7ffa184e5c17b3eb5241b5c7330517a62d284cb7d9'
PRESENTATION = json.loads(PRESENTATION_BYTES)['files']


def restore_presentation(name, data):
    """Human-approved source-stasis exception; never used by runtime code."""
    row = PRESENTATION[name]
    old = gzip.decompress(base64.b64decode(row['complete_gzip_base64']))
    assert hashlib.sha256(old).hexdigest() == row['sha256'], name
    path = ROOT / name
    # Check types even for byte-identical files: chmod/symlink is not approved.
    for parent in path.parents:
        if parent == ROOT:
            break
        assert not parent.is_symlink(), name
    assert stat.S_ISREG(path.lstat().st_mode) and not path.stat().st_mode & 0o111, name
    entry = subprocess.check_output(['git', 'ls-tree', '-z', 'HEAD', '--', name], cwd=ROOT)
    rows = entry.split(b'\x00')
    assert len(rows) == 2 and rows[1] == b'', name
    metadata, found = rows[0].split(b'\t', 1)
    assert metadata.startswith(b'100644 blob ') and found == name.encode(), name
    committed = subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=ROOT)
    assert path.read_bytes() == committed, name
    assert data == committed or data == old, name
    return old


def restore_policy_parent(name, data):
    if name in PRESENTATION:
        return restore_presentation(name, data)
    if name not in RECIPES:
        return data
    row = RECIPES[name]
    old = gzip.decompress(base64.b64decode(row['old_complete_gzip_base64']))
    assert row['count'] == 1 and hashlib.sha256(old).hexdigest() == row['old_full_sha256'], name
    actual = hashlib.sha256(data).hexdigest()
    # A historical caller may already have restored this layer. Nothing else
    # is normalized: unknown bytes, append/drop/duplicate all fail closed.
    assert actual in {row['old_full_sha256'], row['new_full_sha256']}, name
    return old
