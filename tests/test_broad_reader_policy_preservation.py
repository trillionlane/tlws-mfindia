"""Every pre-existing tracked file remains byte-identical; only additive scope."""

import hashlib
import json
from pathlib import Path

from ci_cost_preservation import restore_parent_file

from mfdataindia.review import broad_reader_policy as policy
from mfdataindia.review import reader_access_correction as historical

ROOT = Path(__file__).resolve().parents[1]


def test_all_parent_tracked_files_preserved_whole_bytes_without_normalization():
    authority = json.loads((ROOT / "docs/BROAD_READER_PARENT_AUTHORITY_20261026.json").read_text())
    assert authority["base"] == "b017121fcd33ee6f7287821410cb4f6cf0eb2e2a"
    assert len(authority["files"]) == authority["old_tracked_files"]
    for name, expected in authority["files"].items():
        path = ROOT / name
        if expected["kind"] == "symlink":
            assert path.is_symlink(), name
            data = str(path.readlink()).encode()
        else:
            assert path.is_file() and not path.is_symlink(), name
            data = restore_parent_file(name, path.read_bytes())
        assert hashlib.sha256(data).hexdigest() == expected["sha256"], name


def test_catalogue_composition_is_only_three_fixed_schema_predicate_inverses():
    literal = ",".join("'" + schema + "'" for schema in policy.SCHEMAS)
    rel, ns = "n.nspname IN (" + literal + ")", "n.nspname IN (" + literal + ",'public')"
    assert policy.STATE_SQL.count(rel) == 2 and policy.STATE_SQL.count(ns) == 1
    restored = policy.STATE_SQL.replace(rel, "n.nspname IN ('mf','mf_review')").replace(
        ns, "n.nspname IN ('mf','mf_review','public')"
    )
    assert restored == historical.STATE_SQL
    assert (
        hashlib.sha256(
            (ROOT / "src/mfdataindia/review/reader_access_correction.py").read_bytes()
        ).hexdigest()
        == "863fd37dfa45be74642cf731f44bbd7cc182e263c95309a7d48a4c91af315d00"
    )
    assert (
        hashlib.sha256(
            (ROOT / "src/mfdataindia/review/reader_access_profile_v1.py").read_bytes()
        ).hexdigest()
        == "5d54dbee9631c806b039306599ad0de0e07533fe4be54e93c6d9a817bcf5fdb9"
    )


def test_new_module_has_no_secret_provider_connector_or_old_private_executor_calls():
    source = (ROOT / "src/mfdataindia/review/broad_reader_policy.py").read_text()
    for forbidden in (
        "psycopg.connect(",
        "os.environ",
        "subprocess.",
        "gcloud",
        "run_change(",
        "historical._",
        "engine._execute",
        "SET ROLE",
        "pg_read_all_data",
    ):
        assert forbidden not in source, forbidden
    operations = json.loads(
        (ROOT / "docs/BROAD_READER_POLICY_OPERATIONS_20261026.json").read_text()
    )
    assert "No Insights code edits here" in operations["Insights_owner"]
