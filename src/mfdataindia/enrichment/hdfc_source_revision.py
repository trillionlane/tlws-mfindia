"""One finite whole-byte predecessor stage for current HDFC composition.

Historical hashes stay historical. Current allocation binds actual current
bytes; unknown bytes never become a predecessor or executable authority.
"""
import hashlib
import json
from pathlib import Path

FILE='config/official-hdfc-dynamic-predecessors-v1.json'
SHA256='f4a55546b54b0e1e90b98369f321fb7d364c80b37c260ec39b6f1e8cad5597ba'
OPTIMIZATION_FILE='config/official-hdfc-installer-policy-reuse-predecessors-v1.json'
OPTIMIZATION_SHA256='ad83dd1fef91440800af9dbe28cbd8cc321b6099215ea8310ff6bd38b4adb3c9'


def previous(path,data=None,root=None):
    path=Path(path)
    root=Path(root) if root is not None else (Path('/app') if (Path('/app')/FILE).is_file() else Path(__file__).resolve().parents[3])
    name=('src/mfdataindia/enrichment/'+path.name if path.parent.resolve()==Path(__file__).parent.resolve()
          else path.resolve().relative_to(root.resolve()).as_posix())
    raw=path.read_bytes() if data is None else data
    optimization=(root/OPTIMIZATION_FILE).read_bytes()
    if hashlib.sha256(optimization).hexdigest()!=OPTIMIZATION_SHA256:
        raise ValueError('HDFC_INSTALLER_PREDECESSOR_AUTHORITY_DIFFERS')
    optimized=json.loads(optimization)['modified'].get(name)
    if optimized is not None and hashlib.sha256(raw).hexdigest()!=optimized['previous_sha256']:
        if hashlib.sha256(raw).hexdigest()!=optimized['current_sha256']:
            raise ValueError('HDFC_INSTALLER_UNREVIEWED_SOURCE_BYTES')
        for before,after in reversed(optimized['replacements']):
            before,after=before.encode(),after.encode()
            if raw.count(after)!=1:
                raise ValueError('HDFC_INSTALLER_LITERAL_INVERSE_DIFFERS')
            raw=raw.replace(after,before,1)
        if hashlib.sha256(raw).hexdigest()!=optimized['previous_sha256']:
            raise ValueError('HDFC_INSTALLER_PREDECESSOR_RESTORATION_DIFFERS')
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
