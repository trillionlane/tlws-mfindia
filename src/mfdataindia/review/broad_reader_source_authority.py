"""Read-only byte attestation of the two reviewed migration-source ledgers.

The final image includes only these exact authority documents and the source
files they name. This local check never contacts PostgreSQL or authorizes a
grant; a fresh full live migration ledger/profile comparison remains required.
"""

from hashlib import sha256
import importlib.util
import json
from pathlib import Path

from mfdataindia.store.postgres import DEFAULT_MIGRATIONS
from mfdataindia.review import broad_reader_embedded_authority as embedded


BASE_DOCUMENT = "docs/READER_SOURCE_REFERENCE_MIGRATION_AUTHORITY_20261029.json"
BASE_SHA256 = "efec1d25524362b3caa568a14b096935969de0cc277704d1c04d2ddb95242372"
VARIANT_DOCUMENT = "docs/CI_SOURCE_REFERENCE_BOOK_VARIANT_AUTHORITY_20261029.json"
VARIANT_SHA256 = "2d144f61fa67d936e537fb06b3845569e441f54a2dbde8075f0cd112b3993450"
_VARIANT_PATHS = (
    "sql/optional/amc_book_current_projection_v2.sql.inc",
    "sql/optional/amc_book_core_projection_v1.sql.inc",
    "src/mfdataindia/enrichment/book_core_projection.py",
    "src/mfdataindia/enrichment/current_facts_store.py",
    "src/mfdataindia/enrichment/book_projection.py",
    "src/mfdataindia/enrichment/book_sources.py",
    "config/official-book-sources-v5-aum-row.json",
    "src/mfdataindia/enrichment/book_activation.py",
    "src/mfdataindia/enrichment/book_store.py",
)
LEDGER_SQL = ("SELECT migration_name,content_sha256 FROM mf.schema_migrations "
              "ORDER BY migration_name")


class SourceAuthorityError(ValueError):
    """The packaged source bytes do not match both reviewed ledgers."""


def _require(condition):
    if not condition:
        raise SourceAuthorityError("UNMATCHED_BROAD_READER_SOURCE_BYTES")


def _read(root, relative, digest):
    path = root / relative
    if path.is_file():
        _require(not path.is_symlink() and path.resolve().is_relative_to(root))
        body = path.read_bytes()
    elif relative in (BASE_DOCUMENT, VARIANT_DOCUMENT):
        # Final app/tools images contain installed package code, not repository
        # docs. The complete embedded bytes remain pinned to the SAME SHA.
        body = embedded.document("base" if relative == BASE_DOCUMENT else "variant")
    elif relative.startswith("src/mfdataindia/") and relative.endswith(".py"):
        module = relative[4:-3].replace("/", ".")
        spec = importlib.util.find_spec(module)
        _require(spec is not None and type(spec.origin) is str)
        installed = Path(spec.origin)
        _require(installed.is_file() and not installed.is_symlink())
        body = installed.read_bytes()
    else:
        raise SourceAuthorityError("UNMATCHED_BROAD_READER_SOURCE_BYTES")
    _require(sha256(body).hexdigest() == digest)
    return body


def _verify_files(root, entries, *, expected_count, expected_bytes, paths):
    _require(type(entries) is list and len(entries) == expected_count)
    _require(all(type(row) is dict and set(row) == {"path", "bytes", "sha256"}
                 and type(row["bytes"]) is int and row["bytes"] > 0
                 and type(row["path"]) is str and type(row["sha256"]) is str
                 for row in entries))
    _require([row.get("path") for row in entries] == list(paths))
    _require(sum(row.get("bytes", -1) for row in entries) == expected_bytes)
    for row in entries:
        _require(len(_read(root, row["path"], row["sha256"])) == row["bytes"])
        if row["path"].startswith("src/mfdataindia/"):
            module = row["path"][4:-3].replace("/", ".")
            spec = importlib.util.find_spec(module)
            _require(spec is not None and type(spec.origin) is str)
            installed = Path(spec.origin)
            _require(installed.is_file() and not installed.is_symlink())
            installed_bytes = installed.read_bytes()
            _require(len(installed_bytes) == row["bytes"]
                     and sha256(installed_bytes).hexdigest() == row["sha256"])


def verify_packaged_source(root):
    """Attest complete 44+9 exact file bytes; no wildcard migration selection."""
    root = Path(root).resolve()
    base_raw = _read(root, BASE_DOCUMENT, BASE_SHA256)
    variant_raw = _read(root, VARIANT_DOCUMENT, VARIANT_SHA256)
    _require(base_raw == embedded.document("base")
             and variant_raw == embedded.document("variant"))
    base = json.loads(base_raw)
    variant = json.loads(variant_raw)
    _require(type(base) is dict and base.get("format") ==
             "immutable44migration-source-authority-v2"
             and base.get("base") == "c550e3c21482f45684a1b63b7c70fc84b8a62b50"
             and base.get("total_bytes") == 783832)
    optional = tuple(f"sql/optional/{i:03d}_" for i in range(23, 45))
    _require(type(base.get("files")) is list and len(base["files"]) == 44
             and all(type(row) is dict for row in base["files"]))
    base_paths = [row.get("path") for row in base["files"]]
    _require(base_paths[:22] == ["sql/" + name for name in DEFAULT_MIGRATIONS]
             and len(DEFAULT_MIGRATIONS) == 22
             and all(path.startswith(prefix) for path, prefix in zip(base_paths[22:], optional)))
    _verify_files(root, base["files"], expected_count=44, expected_bytes=783832,
                  paths=base_paths)
    _require(type(variant) is dict and variant.get("format") ==
             "source-only-nine-file-book-variant-authority-v2"
             and variant.get("base44_authority_sha256") == BASE_SHA256
             and variant.get("implementation_parent") ==
             "016e63afb7b82aa1369b97bfdc63a729524d0c1d"
             and variant.get("total_bytes") == 95068
             and variant.get("runtime_authority") is False)
    _verify_files(root, variant["files"], expected_count=9, expected_bytes=95068,
                  paths=_VARIANT_PATHS)
    return {"format": "mf-broad-reader-packaged-source-v1-nonexecutable",
            "base_sha256": BASE_SHA256, "variant_sha256": VARIANT_SHA256,
            "base_files": 44, "variant_files": 9,
            "migration_ledger": [{"migration_name": Path(row["path"]).name,
                                  "content_sha256": row["sha256"]}
                                 for row in base["files"]],
            "live_migration_ledger_compared": False,
            "permission_enabled": False}


def verify_live_ledger(rows, packaged):
    """Require the complete ordered live migration ledger, not a subset."""
    _require(type(rows) is list and type(packaged) is dict
             and packaged.get("format") == "mf-broad-reader-packaged-source-v1-nonexecutable"
             and packaged.get("base_sha256") == BASE_SHA256
             and packaged.get("variant_sha256") == VARIANT_SHA256)
    expected = packaged.get("migration_ledger")
    _require(type(expected) is list and len(expected) == 44)
    _require(all(type(row) is dict and set(row) == {"migration_name", "content_sha256"}
                 for row in rows))
    _require(rows == expected)
    return {"format": "mf-broad-reader-migration-ledger-v1-nonexecutable",
            "rows": 44, "base_sha256": BASE_SHA256,
            "live_ledger_sha256": sha256(json.dumps(
                rows, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
            "grant_authorized": False}
