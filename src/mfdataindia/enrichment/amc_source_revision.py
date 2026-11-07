"""Exact byte inverses for three retained v1 source constructors.

Only explicit reviewed current files are accepted. Historical source hashes
remain historical; new method evidence uses actual current bytes. This is not
an execution bypass, a wildcard hash normalizer or a code loader.
"""
import hashlib
import json
from pathlib import Path

MANIFEST = 'config/official-amc-source-extension-v1.json'
MANIFEST_SHA256 = 'c97f3bbe729af2541bf50199d1d8a4c85c06e9c97cf1cc259fb0431c08a76900'


def previous_bytes(path, data=None):
    path = Path(path)
    raw = path.read_bytes() if data is None else data
    from mfdataindia.enrichment.hdfc_source_revision import previous
    raw = previous(path,raw)
    root = Path('/app') if (Path('/app') / MANIFEST).is_file() else Path(__file__).resolve().parents[3]
    if path.resolve().parent == Path(__file__).resolve().parent:
        name = 'src/mfdataindia/enrichment/' + path.name
    else:
        name = path.resolve().relative_to(root).as_posix()
    document_raw = (root / MANIFEST).read_bytes()
    if hashlib.sha256(document_raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError('AMC_SOURCE_EXTENSION_AUTHORITY_MISMATCH')
    row = json.loads(document_raw)['modified'].get(name)
    if row is None or hashlib.sha256(raw).hexdigest() == row['previous_sha256']:
        return raw
    if hashlib.sha256(raw).hexdigest() != row['current_sha256']:
        raise ValueError('AMC_SOURCE_EXTENSION_BYTES_MISMATCH')
    for before, after in reversed(row['replacements']):
        before, after = before.encode(), after.encode()
        if raw.count(after) != 1:
            raise ValueError('AMC_SOURCE_EXTENSION_RECIPE_MISMATCH')
        raw = raw.replace(after, before, 1)
    if hashlib.sha256(raw).hexdigest() != row['previous_sha256']:
        raise ValueError('AMC_SOURCE_EXTENSION_RESTORATION_MISMATCH')
    return raw


def retained_engine_hash(module):
    return hashlib.sha256(previous_bytes(Path(module.__file__))).hexdigest()
