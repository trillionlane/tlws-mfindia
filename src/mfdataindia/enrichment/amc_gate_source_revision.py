"""Finite full-byte predecessor bindings for the shared execution composition.

Only declared current source bytes and complete literal inverses are accepted.
Version <=5 retains MAIN785; v6 retains the explicit Kotak release. The new v7
specification and allocation bind actual current execution bytes separately.
"""
import hashlib
import json
from pathlib import Path

FILE = 'config/official-amc-gate-source-predecessors-v1.json'
SHA256 = 'fc346908a73b547ee749b04dd86db8f4026fcdbb83ef170f903d78cd44750322'


def previous_bytes(path, *, version=6, data=None, root=None):
    path = Path(path)
    root = Path(root) if root is not None else (Path('/app') if (Path('/app')/FILE).is_file() else Path(__file__).resolve().parents[3])
    name = ('src/mfdataindia/enrichment/'+path.name if path.parent.resolve() == Path(__file__).parent.resolve()
            else path.resolve().relative_to(root.resolve()).as_posix())
    raw = path.read_bytes() if data is None else data
    authority = (root/FILE).read_bytes()
    if hashlib.sha256(authority).hexdigest() != SHA256:
        raise ValueError('AMC_GATE_PREDECESSOR_AUTHORITY_DIFFERS')
    row = json.loads(authority)['versions']['legacy' if version <= 5 else 'kotak_v6'].get(name)
    if row is None or hashlib.sha256(raw).hexdigest() == row['previous_sha256']:
        return raw
    if hashlib.sha256(raw).hexdigest() != row['current_sha256']:
        raise ValueError('AMC_GATE_UNREVIEWED_SOURCE_BYTES')
    for before, after in reversed(row['replacements']):
        before, after = before.encode(), after.encode()
        if raw.count(after) != 1:
            raise ValueError('AMC_GATE_LITERAL_INVERSE_DIFFERS')
        raw = raw.replace(after, before, 1)
    if hashlib.sha256(raw).hexdigest() != row['previous_sha256']:
        raise ValueError('AMC_GATE_PREDECESSOR_RESTORATION_DIFFERS')
    return raw


def engine_hash(module, version):
    return hashlib.sha256(previous_bytes(module.__file__, version=version)).hexdigest()
