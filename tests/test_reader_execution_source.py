"""Explicit current authority and finite historical compatibility, no bypass."""
import hashlib
import importlib.util
import json

import pytest

from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review import reader_execution_source_reference as current
from ro02_historical_source import ROOT, authority, previous


def test_current_source_is_ten_complete_files_and_prior_nine_file_doc_immutable():
    raw = (ROOT / current.DOCUMENT).read_bytes()
    assert raw == current.document()
    assert hashlib.sha256(raw).hexdigest() == current.SHA256 == source.VARIANT_SHA256
    value = json.loads(raw)
    assert len(value["files"]) == 10
    assert [r["path"] for r in value["files"]] == list(current.PATHS)
    assert source.verify_packaged_source(ROOT)["variant_files"] == 10
    old = (ROOT / "docs/RO01_BOOK_SOURCE_AUTHORITY_20261031.json").read_bytes()
    assert hashlib.sha256(old).hexdigest() == value["historical_ro01_authority_sha256"]
    assert json.loads(old)["files"] != value["files"]
    assert value["runtime_authority"] is False


@pytest.mark.parametrize("name", sorted(authority()))
def test_only_listed_complete_current_files_restore_and_every_byte_tamper_rejects(name):
    raw = (ROOT / name).read_bytes()
    restored = previous(ROOT / name, raw)
    row = authority()[name]
    assert raw != restored
    assert hashlib.sha256(restored).hexdigest() == row["previous_sha256"]
    for changed in (raw + b"\n#tamper", raw[:-1], raw + raw):
        with pytest.raises(AssertionError):
            previous(ROOT / name, changed)


def test_both_images_add_actual_current_entrypoint_smoke_without_changing_old_body():
    path = ROOT / ".github/workflows/verify.yml"
    raw = path.read_bytes()
    command = b'            docker run --rm --entrypoint python --env "DEPLOYMENT_GIT_SHA=$GITHUB_SHA" "$target" /app/scripts/smoke_reader_execution_dev.py\n'
    assert raw.count(command) == 1
    assert raw.replace(command, b"", 1) == previous(path, raw)
    before = raw.split(command)[0]
    assert before.rfind(b'for target in "$image" "$tools_image"; do') > before.rfind(b"      - name:")


def test_actual_packaged_parse_and_all_three_main_boundaries_without_provider_or_db():
    found = importlib.util.spec_from_file_location("_ro02_image_smoke", ROOT / "scripts/smoke_reader_execution_dev.py")
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    result = module.smoke(root=ROOT, git_sha="a" * 40)
    assert result["packaged_reader_execution_entrypoints"] == "PASS"
    assert len(result["boundaries"]) == 3
    assert result["provider_requests"] == result["database_connections"] == 0
    assert result["live_consumer_acceptance"] is False
