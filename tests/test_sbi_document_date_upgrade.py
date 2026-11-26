"""Source proofs plus one disposable PostgreSQL migration/configuration proof."""

import hashlib
import shutil
from pathlib import Path

import pytest
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from mfdataindia.enrichment import (
    book_runtime_v2,
    book_sources,
    book_store,
    sbi_book,
    sbi_book_sources_v2 as sbi_sources,
    sbi_book_store_v2 as sbi_store,
    sbi_method_upgrade,
    sbi_runtime_v2,
)
from mfdataindia.review.policy import fingerprint
from test_verified_review_store import verified_db as verified_db

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def test_frozen_sbi_v1_sources_and_historical_sql_are_unchanged():
    assert (
        digest("src/mfdataindia/enrichment/sbi_book.py")
        == "6b8b61a253fe0909ad042f9a07cf26c2b424f87cae9bfdc0bcc81f954c8a48ee"
    )
    assert (
        digest("src/mfdataindia/enrichment/sbi_batch.py")
        == "0685a5c89621d6afbd6b7a7b70ff8012fe44a827a60bfc0bf41febeb7c8b9dfc"
    )
    assert (
        digest("src/mfdataindia/enrichment/sbi_runtime.py")
        == "5738349ff41727ea76b27e4be2706089d4cd5b80d3a09de7c35e55ac74418c54"
    )
    assert (
        digest("sql/optional/044_uti_complete_aum_rows.sql")
        == "4b4e3d73d49a4844c4da300a17a952a8e24a47a6fa025382a2db064f5ed1a778"
    )
    assert (
        digest("sql/optional/amc_book_current_projection_v3.sql.inc")
        == "28cfbe8edabe6af5527066ae79a79a4e04cb1b416cd3d7d13c4886ea2c38ae59"
    )


def test_v9_is_derived_only_from_v5_and_changes_only_sbi_adapter():
    assert book_sources.CURRENT_VERSION == 5
    assert set(book_sources.REGISTRIES) == set(range(1, 9))
    with pytest.raises(ValueError, match="reviewed book registry version required"):
        book_sources.registry(9)
    previous = book_sources.registry(5)
    revised = sbi_sources.registry()
    assert revised["authority_transition"]["previous_source_version"] == 5
    assert revised["authority_transition"]["excluded_allocated_side_versions"] == [6, 7, 8]
    expected = previous["sources"]
    for old, new in zip(expected, revised["sources"]):
        if old["key"] == sbi_book.KEY:
            assert {**new, "adapter": old["adapter"]} == old
            assert new["adapter"] == "sbi-full-book-v2"
        else:
            assert new == old
    old_config = book_sources.configuration(5)
    new_config = sbi_sources.configuration()
    for key, specification in old_config["source_specifications"].items():
        if key != sbi_book.KEY:
            assert new_config["source_specifications"][key] == specification
    old_sbi_hash = fingerprint(old_config["source_specifications"][sbi_book.KEY])
    assert new_config["retained_source_specification_hashes"] == {sbi_book.KEY: [old_sbi_hash]}
    assert sbi_sources.current_policy()["previous_policy_hash"] == fingerprint(
        book_sources.current_policy(5)
    )
    assert sbi_store.MIGRATIONS == {
        9: (
            "050_sbi_document_as_of_evidence.sql",
            "ba2e7fb6073136a57bdfced59c30d5705d2e61b80e9d69ef338a346d9a52b422",
        )
    }


def test_migration_050_retains_every_non_sbi_seal_byte_for_byte():
    old = (ROOT / "sql/optional/044_uti_complete_aum_rows.sql").read_text()
    new = (ROOT / "sql/optional/050_sbi_document_as_of_evidence.sql").read_text()
    old_start = old.index("    -- Registry v1 interpretation below remains byte-for-byte frozen.")
    new_start = new.index("    -- The v1 branch remains exact;")
    suffix = (
        "    IF TG_TABLE_NAME='sources' THEN\n        IF NOT ((NEW.media_type='application/pdf'"
    )
    old_end = old.index(suffix, old_start)
    new_end = new.index(suffix, new_start)
    assert new[:new_start] == old[:old_start]
    assert new[new_end:] == old[old_end:]
    sbi = new[new_start:new_end]
    assert "official-amc-book-batch-v2','official-amc-book-batch-v3" in sbi
    assert "retained_source_specification_hashes" in sbi
    assert "field->>'reporting_period'=NEW.disclosure_as_of::text" in sbi
    assert "THEN field->>'document_as_of' ELSE NEW.disclosure_as_of::text END" in sbi


def test_migration_050_sbi_structure_has_no_unresolved_identifier_or_duplicate_period_if():
    source = (ROOT / "sql/optional/050_sbi_document_as_of_evidence.sql").read_text()
    start = source.index("    -- The v1 branch remains exact;")
    suffix = (
        "    IF TG_TABLE_NAME='sources' THEN\n        IF NOT ((NEW.media_type='application/pdf'"
    )
    end = source.index(suffix, start)
    sbi = source[start:end]
    assert "cfg" not in source
    assert sbi.count("IF ids IS DISTINCT FROM NEW.document->'batch_ids'") == 1
    assert "IF ids IS DISTINCT FROM NEW.document->'batch_ids'" not in sbi.replace(
        "IF ids IS DISTINCT FROM NEW.document->'batch_ids'", "", 1
    )
    assert source.count("\nBEGIN;\n") == 1
    assert (
        source.count("CREATE OR REPLACE FUNCTION mf_book_facts.seal_input() RETURNS trigger") == 1
    )
    assert source.count("AS $$") == 1
    assert source.count("END $$;") == 1
    assert source.rstrip().endswith("COMMIT;")


def test_projection_never_promotes_document_date_to_canonical_as_of():
    source = (ROOT / "sql/optional/amc_book_current_projection_v4.sql.inc").read_text()
    assert "b.disclosure_as_of AS as_of" in source
    assert "'reporting_period',k.value->>'reporting_period'" in source
    assert "'document_as_of',k.value->>'document_as_of'" in source
    assert "document_as_of AS as_of" not in source


def test_new_migration_and_projection_are_exactly_pinned():
    assert hashlib.sha256(sbi_store.migration_source(ROOT, version=9).encode()).hexdigest() == (
        "ba2e7fb6073136a57bdfced59c30d5705d2e61b80e9d69ef338a346d9a52b422"
    )
    assert hashlib.sha256(sbi_method_upgrade.projection_source(ROOT).encode()).hexdigest() == (
        "4861ce38094f26b07b3d40dcdc7b47d2a69218d8f607c47da5ba6a9c6b04eb80"
    )


def test_v9_runtime_route_is_explicit_and_context_local(monkeypatch):
    calls = []

    def run(dsn, execution):
        calls.append((dsn, execution, sbi_sources.registry()["version"]))
        return {"status": "unchanged"}

    monkeypatch.setattr(sbi_runtime_v2, "run", run)
    result = book_runtime_v2.execute(
        "unused-provider-free-dsn",
        "tlws-mf-official-facts-refresh-dev-source-only",
        task_index=3,
    )
    assert calls == [
        (
            "unused-provider-free-dsn",
            "tlws-mf-official-facts-refresh-dev-source-only",
            9,
        )
    ]
    assert result["source_key"] == sbi_book.KEY and result["status"] == "completed"
    assert book_sources.registry()["version"] == 5


def test_isolated_registry_and_migration_tamper_fail_closed(tmp_path):
    shutil.copytree(ROOT / "config", tmp_path / "config")
    registry = tmp_path / "config" / sbi_sources.REGISTRY_FILE
    registry.write_bytes(registry.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="registry differs from reviewed pin"):
        sbi_sources.registry(root=tmp_path)

    migration_dir = tmp_path / "sql" / "optional"
    migration_dir.mkdir(parents=True)
    migration = migration_dir / sbi_store.MIGRATIONS[9][0]
    shutil.copyfile(ROOT / "sql" / "optional" / migration.name, migration)
    migration.write_bytes(migration.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="migration differs from reviewed pin"):
        sbi_store.migration_source(tmp_path)


@pytest.mark.postgres
def test_isolated_v9_migration_and_configuration_on_postgres(verified_db):
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        for version in (1, 2, 3, 4, 5):
            raw = book_store.migration_source(ROOT, version=version)
            conn.execute(raw.replace("\nBEGIN;\n", "\n", 1).rstrip()[: -len("COMMIT;")])
        raw = sbi_store.migration_source(ROOT)
        conn.execute(raw.replace("\nBEGIN;\n", "\n", 1).rstrip()[: -len("COMMIT;")])
        document = sbi_sources.configuration()
        conn.execute(
            "INSERT INTO mf_book_facts.configuration VALUES(1,%s,%s)",
            (fingerprint(document), Jsonb(document)),
        )
        installed = sbi_store.installed_configuration(conn)
        assert installed["document"] == document
        assert installed["configuration_hash"] == fingerprint(document)
        body = conn.execute(
            "SELECT prosrc FROM pg_proc WHERE pronamespace='mf_book_facts'::regnamespace "
            "AND proname='seal_input'"
        ).fetchone()["prosrc"]
        assert body == raw.split("AS $$", 1)[1].split("$$;", 1)[0]
