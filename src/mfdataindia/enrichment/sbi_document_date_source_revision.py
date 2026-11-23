"""Exact SBI v9 predecessor stage before retained HDFC and AMC chains.

Only three reviewed whole files are recognized. Unknown paths pass through for
the next authority; unknown bytes for a named path fail closed.
"""
import hashlib
import json
from pathlib import Path

FILE = "config/official-sbi-document-date-source-predecessors-v1.json"
SHA256 = "7e262bdc06e55ca310b4e52635096b90d39b218d676d52bd1aba396ea66cc2b6"


def previous(path, data=None, root=None):
    path = Path(path)
    root = Path(root) if root is not None else (
        Path("/app") if (Path("/app") / FILE).is_file() else Path(__file__).resolve().parents[3]
    )
    name = (
        "src/mfdataindia/enrichment/" + path.name
        if path.parent.resolve() == Path(__file__).parent.resolve()
        else path.resolve().relative_to(root.resolve()).as_posix()
    )
    raw = path.read_bytes() if data is None else data
    authority = (root / FILE).read_bytes()
    if hashlib.sha256(authority).hexdigest() != SHA256:
        raise ValueError("SBI_DOCUMENT_DATE_PREDECESSOR_AUTHORITY_DIFFERS")
    row = json.loads(authority)["modified"].get(name)
    if row is None:
        return raw
    digest = hashlib.sha256(raw).hexdigest()
    downstream = row.get("accepted_downstream_sha256", [])
    if (type(downstream) is not list
            or any(type(value) is not str or len(value) != 64 for value in downstream)):
        raise ValueError("SBI_DOCUMENT_DATE_PREDECESSOR_AUTHORITY_DIFFERS")
    if digest == row["previous_sha256"] or digest in downstream:
        return raw
    if digest != row["current_sha256"]:
        raise ValueError("SBI_DOCUMENT_DATE_UNREVIEWED_SOURCE_BYTES")
    for replacement in reversed(row["replacements"]):
        before = replacement["before"].encode()
        after = replacement["after"].encode()
        count = replacement["after_occurrences"]
        if type(count) is not int or count != 1 or raw.count(after) != count:
            raise ValueError("SBI_DOCUMENT_DATE_LITERAL_INVERSE_DIFFERS")
        raw = raw.replace(after, before, count)
    if hashlib.sha256(raw).hexdigest() != row["previous_sha256"]:
        raise ValueError("SBI_DOCUMENT_DATE_PREDECESSOR_RESTORATION_DIFFERS")
    return raw
