"""One finite whole-byte predecessor stage for current HDFC composition.

Historical hashes stay historical. Current allocation binds actual current
bytes; unknown bytes never become a predecessor or executable authority.
"""
import hashlib
import json
from pathlib import Path

FILE='config/official-hdfc-dynamic-predecessors-v1.json'
SHA256='f277bafabe7209d0c089bdd80ec95d2a13d3e3541f83a8306ecf258b5afe91aa'


def previous(path,data=None,root=None):
    path=Path(path)
    root=Path(root) if root is not None else (Path('/app') if (Path('/app')/FILE).is_file() else Path(__file__).resolve().parents[3])
    name=('src/mfdataindia/enrichment/'+path.name if path.parent.resolve()==Path(__file__).parent.resolve()
          else path.resolve().relative_to(root.resolve()).as_posix())
    raw=path.read_bytes() if data is None else data
    authority=(root/FILE).read_bytes()
    if hashlib.sha256(authority).hexdigest()!=SHA256:
        raise ValueError('HDFC_PREDECESSOR_AUTHORITY_DIFFERS')
    row=json.loads(authority)['modified'].get(name)
    if row is None or hashlib.sha256(raw).hexdigest()==row['previous_sha256']:
        return raw
    if hashlib.sha256(raw).hexdigest()!=row['current_sha256']:
        raise ValueError('HDFC_UNREVIEWED_SOURCE_BYTES')
    for before,after in reversed(row['replacements']):
        before,after=before.encode(),after.encode()
        if raw.count(after)!=1:
            raise ValueError('HDFC_LITERAL_INVERSE_DIFFERS')
        raw=raw.replace(after,before,1)
    if hashlib.sha256(raw).hexdigest()!=row['previous_sha256']:
        raise ValueError('HDFC_PREDECESSOR_RESTORATION_DIFFERS')
    return raw
