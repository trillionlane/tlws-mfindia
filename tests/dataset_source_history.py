"""Finite whole-file current inverses for historical tests only; no runtime use."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def previous(path, data):
    assert type(data) is bytes
    from amc_loading_history import previous as amc_previous
    # Intentional current API/schema/contract change. Validate complete bytes
    # before restoring the reviewed predecessor for old release assertions.
    name = Path(path).resolve().relative_to(ROOT).as_posix() if Path(path).resolve().is_relative_to(ROOT) else None
    current_raw = (ROOT / 'docs/NSE_SOURCE_NATIVE_CURRENT_SOURCE_CHANGES_20261119.json').read_bytes()
    assert hashlib.sha256(current_raw).hexdigest() == '9a854f089a61f020934280930852d60ad1a02af3cec595dbad80ac33cde09477'
    current = json.loads(current_raw)
    row = current['modified'].get(name)
    if row and hashlib.sha256(data).hexdigest() == row['current_sha256']:
        data = subprocess.check_output(['git', 'show', current['base']+':'+name], cwd=ROOT)
        assert hashlib.sha256(data).hexdigest() == row['previous_sha256'], name
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
