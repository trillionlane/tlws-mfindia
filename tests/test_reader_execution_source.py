"""Explicit current authority and finite historical compatibility, no bypass."""
import hashlib
import importlib.util
import json
from copy import deepcopy

import pytest

from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review import reader_execution_source_reference as current
from mfdataindia.review import broad_reader_view_profile as views
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


def populated_source_rows():
    reference = json.loads((ROOT / "docs/RO02_POPULATED_SOURCE_VIEW_REFERENCE_20261031.json").read_text())
    compiled = reference["compilation"]
    assert compiled["head"] == "e5154ce097a04c3117ddb0e8302d3a4a2fe4e1c4"
    assert compiled["checkout"] == "b0dff71e013ce66b819d7d736272609c8779d1e4"
    assert compiled["tree"] == "61f44eca89ba02be308f6346f3bfceaa9736e48b"
    assert compiled["source"] == source.verify_packaged_source(ROOT)
    # Synthetic OIDs isolate this pure definition-set matcher. Original native
    # wire OIDs/proofs are never rewritten by this source-profile unit test.
    rows = [{**deepcopy(row), "oid": i + 1, "owner": 100, "relkind": "v"}
            for i, row in enumerate(compiled["view_projection"])]
    return {"relations": deepcopy(rows)}, rows


def test_actual_compiled_populated_graph_has_only_the_new_exact_authority_pair():
    state, candidates = populated_source_rows()
    result = views.select(state, candidates, base_authority_sha256=source.BASE_SHA256,
                          variant_authority_sha256=source.VARIANT_SHA256)
    assert result["profile"] == views.RO02_POPULATED_PROFILE
    assert result["views"] == 16
    assert result["views_sha256"] == "e2fca3236a4372cb705ba1eda686763221a60354837df3419743fbe643ee664c"
    assert result["permission_enabled"] is result["live_admission"] is False
    with pytest.raises(views.ViewProfileError):
        views.select(state, candidates, base_authority_sha256=source.BASE_SHA256,
                     variant_authority_sha256=views.VARIANT_AUTHORITY_SHA256)


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "definition", "size", "one_sided", "unknown_authority"])
def test_populated_profile_does_not_admit_missing_changed_mixed_or_unknown_source(change):
    state, candidates = populated_source_rows()
    selected = source.VARIANT_SHA256
    if change == "missing":
        state["relations"].pop()
        candidates.pop()
    elif change == "extra":
        extra = {**deepcopy(candidates[-1]), "oid": 999, "relname": "unknown_view"}
        state["relations"].append(extra)
        candidates.append(deepcopy(extra))
    elif change == "duplicate":
        state["relations"].append(deepcopy(state["relations"][0]))
        candidates.append(deepcopy(candidates[0]))
    elif change in ("definition", "size"):
        key, value = ("sha256", "f" * 64) if change == "definition" else ("utf8_bytes", 1)
        state["relations"][0]["definition"][key] = candidates[0]["definition"][key] = value
    elif change == "one_sided":
        candidates[0]["owner"] += 1
    else:
        selected = "f" * 64
    with pytest.raises(views.ViewProfileError):
        views.select(state, candidates, base_authority_sha256=source.BASE_SHA256,
                     variant_authority_sha256=selected)
