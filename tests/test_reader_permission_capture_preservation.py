"""Counted full-byte inverse only. No ignored ranges, AST or whitespace exemptions."""
import base64
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = json.loads((ROOT / "docs/READER_PERMISSION_CAPTURE_PARENT_AUTHORITY_20261025.json").read_text())
PLAN = json.loads((ROOT / "docs/READER_PERMISSION_CAPTURE_LITERAL_PLAN_20261025.json").read_text())


def original_parent_bytes(path):
    raw = (ROOT / path).read_bytes()
    if path == ".github/workflows/verify.yml":
        suffix = PLAN["workflow_append"].encode()
        assert raw.endswith(suffix) and raw.count(suffix) == 1
        raw = raw[:-len(suffix)]
    for entry in PLAN["old_test_substitutions"]:
        if entry["path"] == path:
            for sub in reversed(entry["substitutions"]):
                assert raw.count(sub["new"].encode()) == sub["count"]
                raw = raw.replace(sub["new"].encode(), sub["old"].encode())
    assert hashlib.sha256(raw).hexdigest() == AUTHORITY["sha256"][path], path
    return raw


def test_all_825_parent_files_restore_full_bytes_exactly():
    assert AUTHORITY["tracked_file_count"] == len(AUTHORITY["sha256"]) == 825
    assert PLAN["base"] == AUTHORITY["base"] == "21f337ea0dcefc210827118e66fdd821dadd6025"
    for path in AUTHORITY["sha256"]:
        original_parent_bytes(path)


def test_full_template_copies_reverse_only_counted_identity_source_and_owner_literals():
    for entry in PLAN["copies"]:
        raw = (ROOT / entry["new_path"]).read_bytes()
        for sub in reversed(entry["substitutions"]):
            assert raw.count(sub["new"].encode()) == sub["count"], sub
            raw = raw.replace(sub["new"].encode(), sub["old"].encode())
        old = gzip.decompress(base64.b64decode(AUTHORITY["full_bytes_gzip_base64"][entry["old_path"]]))
        assert raw == old == (ROOT / entry["old_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["parent_sha256"]
