"""Exact packaged migration authority; no live catalogue assumption."""

from pathlib import Path
import shutil
from copy import deepcopy

import pytest

from mfdataindia.review import broad_reader_source_authority as source


ROOT = Path(__file__).resolve().parents[1]


def test_exact_repo_source_files_match_reviewed_ledgers():
    result = source.verify_packaged_source(ROOT)
    assert result["base_files"] == 44 and result["variant_files"] == 9
    assert result["live_migration_ledger_compared"] is False
    assert result["permission_enabled"] is False
    assert len(result["migration_ledger"]) == 44
    checked = source.verify_live_ledger(result["migration_ledger"], result)
    assert checked["rows"] == 44 and checked["grant_authorized"] is False
    assert source.require_packaged_source(result, ROOT) == result


def test_matching_forged_package_and_live_ledger_are_not_source_authority():
    package = source.verify_packaged_source(ROOT)
    forged = deepcopy(package)
    forged["migration_ledger"][0]["content_sha256"] = "0" * 64
    with pytest.raises(source.SourceAuthorityError,
                       match="UNMATCHED_BROAD_READER_SOURCE_BYTES"):
        source.verify_live_ledger(deepcopy(forged["migration_ledger"]), forged)
    with pytest.raises(source.SourceAuthorityError,
                       match="UNMATCHED_BROAD_READER_SOURCE_BYTES"):
        source.require_packaged_source(forged, ROOT)


def test_final_image_layout_uses_embedded_full_docs_and_installed_python(tmp_path):
    """No Dockerfile/source exception: final /app has SQL/config plus wheel."""
    import json
    base = json.loads((ROOT / source.BASE_DOCUMENT).read_bytes())
    variant = json.loads((ROOT / source.VARIANT_DOCUMENT).read_bytes())
    for row in base["files"] + variant["files"]:
        if row["path"].startswith("src/"):
            continue
        target = tmp_path / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / row["path"], target)
    assert not (tmp_path / "docs").exists()
    assert not (tmp_path / "src").exists()
    result = source.verify_packaged_source(tmp_path)
    assert result["base_files"] == 44 and result["variant_files"] == 9
    assert result["permission_enabled"] is False


@pytest.mark.parametrize("defect", ["missing", "extra", "changed", "reordered"])
def test_live_ledger_must_be_complete_and_exact(defect):
    packaged = source.verify_packaged_source(ROOT)
    rows = [dict(row) for row in packaged["migration_ledger"]]
    if defect == "missing":
        rows.pop()
    elif defect == "extra":
        rows.append({"migration_name": "045_unreviewed.sql", "content_sha256": "0" * 64})
    elif defect == "changed":
        rows[0]["content_sha256"] = "0" * 64
    else:
        rows.reverse()
    with pytest.raises(source.SourceAuthorityError,
                       match="UNMATCHED_BROAD_READER_SOURCE_BYTES"):
        source.verify_live_ledger(rows, packaged)


@pytest.mark.parametrize("defect", ["base_document", "variant_document", "migration", "variant_file"])
def test_source_byte_or_authority_drift_fails_closed(tmp_path, defect):
    base = (ROOT / source.BASE_DOCUMENT).read_bytes()
    variant = (ROOT / source.VARIANT_DOCUMENT).read_bytes()
    (tmp_path / "docs").mkdir()
    (tmp_path / source.BASE_DOCUMENT).write_bytes(base)
    (tmp_path / source.VARIANT_DOCUMENT).write_bytes(variant)
    import json
    rows = json.loads(base)["files"] + json.loads(variant)["files"]
    for row in rows:
        target = tmp_path / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / row["path"], target)
    path = {
        "base_document": source.BASE_DOCUMENT,
        "variant_document": source.VARIANT_DOCUMENT,
        "migration": rows[0]["path"],
        "variant_file": rows[-1]["path"],
    }[defect]
    target = tmp_path / path
    target.write_bytes(target.read_bytes() + b"\n-- unreviewed\n")
    with pytest.raises(source.SourceAuthorityError,
                       match="UNMATCHED_BROAD_READER_SOURCE_BYTES"):
        source.verify_packaged_source(tmp_path)
