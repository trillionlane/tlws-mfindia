"""Disposable PG18-only view recursive-lock calibration, never DEV access."""

import json
from pathlib import Path
import subprocess

import psycopg
from psycopg.rows import dict_row
import pytest

import reader_source_packets as packets
import reader_source_reference as legacy
import reader_source_variants as variants
import reader_source_view_locks as view_locks
from test_reader_source_variants_postgres import observe

pytest_plugins = ("test_reader_source_variants_postgres",)
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.postgres
def test_native_pristine_target_observer_complete_view_locks(source_variant_db, capsys):
    dsn, authority = source_variant_db
    expected_git = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    base, _ = observe(dsn, authority, variants.PROFILES[0])
    base_record = view_locks.observe_profile(
        dsn, variants.PROFILES[0], base["raw"]["legacy"],
        legacy.AUTHORITY_SHA, expected_git=expected_git)
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5,
                         row_factory=dict_row) as owner:
        variants.install_book_variant(owner, ROOT)
    book, _ = observe(dsn, authority, variants.PROFILES[1])
    book_record = view_locks.observe_profile(
        dsn, variants.PROFILES[1], book["raw"]["legacy"],
        legacy.AUTHORITY_SHA, expected_git=expected_git)
    bundle = packets.normal_ci_bundle(expected_git)
    for record in (base_record, book_record):
        assert record["ordinary_views"] == 15 and record["materialized_views"] == 0
        assert all(item["classification_complete"] is False and item["rollback_confirmed"]
                   for item in record["observations"])
        record["bundle"] = bundle
    with capsys.disabled():
        for record in (base_record, book_record):
            print(view_locks.MARKER + json.dumps(record, sort_keys=True,
                                               separators=(",", ":")), flush=True)

    # Changed source view with a real outside user relation: its actual lock
    # appears and the reviewed catalog-only outside classification rejects it.
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5,
                         row_factory=dict_row) as owner:
        owner.execute("CREATE SCHEMA ci_outside")
        try:
            owner.execute("CREATE TABLE ci_outside.base (id integer)")
            owner.execute("CREATE VIEW mf.ci_outside_view AS SELECT id FROM ci_outside.base")
            try:
                with psycopg.connect(dsn, autocommit=True, connect_timeout=5,
                                     row_factory=dict_row) as observer:
                    database_oid = observer.execute(
                        "SELECT oid::bigint AS oid FROM pg_catalog.pg_database "
                        "WHERE datname=pg_catalog.current_database()", prepare=False
                    ).fetchone()["oid"]
                    view_oid = observer.execute(
                        "SELECT 'mf.ci_outside_view'::regclass::oid::bigint AS oid",
                        prepare=False
                    ).fetchone()["oid"]
                    candidate = view_locks.observe_view(
                        dsn, observer, database_oid=database_oid,
                        view={"oid": view_oid, "nspname": "mf",
                              "relname": "ci_outside_view", "relkind": "v"},
                        expected_seal=view_locks._view_seal(observer, view_oid),
                    )
                assert any(row["nspname"] == "ci_outside" and row["relname"] == "base"
                           for row in candidate["observed_lock_delta"])
                with pytest.raises(ValueError, match="INCOMPLETE_CI_VIEW_LOCK_CANDIDATE"):
                    view_locks.require_source_outside_scope(candidate)
            finally:
                owner.execute("DROP VIEW mf.ci_outside_view")
        finally:
            owner.execute("DROP SCHEMA ci_outside CASCADE")


def test_source_outside_gate_rejects_an_unreviewed_relation():
    row = {"observed_lock_delta": [
        {"nspname": "pg_catalog", "relname": "pg_class"},
        {"nspname": "ci_outside", "relname": "base"},
    ]}
    with pytest.raises(ValueError, match="INCOMPLETE_CI_VIEW_LOCK_CANDIDATE"):
        view_locks.require_source_outside_scope(row)


def test_source_outside_gate_requires_all_partition_health_catalog_leaves():
    row = {"observed_lock_delta": [
        {"nspname": "pg_catalog", "relname": "pg_class"},
        {"nspname": "pg_catalog", "relname": "pg_inherits"},
    ]}
    with pytest.raises(ValueError, match="INCOMPLETE_CI_VIEW_LOCK_CANDIDATE"):
        view_locks.require_source_outside_scope(row, partition_health=True)
