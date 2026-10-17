"""Full-row equivalence, predicate pushdown, immutable inputs and exact replay."""

from pathlib import Path

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import (
    book_core_projection as optimized,
    book_preservation,
    book_projection,
    book_runtime,
    book_sources,
    book_store,
    book_upgrade,
    current_facts_store as current,
)
from mfdataindia.enrichment.current_view_ddl import replay_current_values
from mfdataindia.enrichment.monthly_projection import ORDER
from mfdataindia.review.policy import fingerprint
from test_amc_book_facts import ROOT, NOW
from test_sbi_store import accepted_fs08, source_inputs
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES, source_state
from test_verified_review_store import verified_db as verified_db


def test_reviewed_sql_is_pinned_and_does_not_rewrite_previous_sources(tmp_path):
    raw = optimized.source(ROOT)
    assert "selected_field_once" in raw
    assert "UNION ALL SELECT " in raw
    target = tmp_path / "sql/optional"
    target.mkdir(parents=True)
    (target / optimized.SOURCE).write_text(raw + "-- drift\n")
    with pytest.raises(ValueError, match="differs"):
        optimized.source(tmp_path)


@pytest.mark.postgres
def test_full_facade_values_columns_and_all_old_amc_rows_identical_then_recovery(
    verified_db, monkeypatch
):
    # Historical SBI equivalence proof: later AMC source authorities have not
    # been installed by this fixture. Their upgrades have separate tests.
    monkeypatch.setattr(
        book_sources, "REGISTRIES", {k: v for k, v in book_sources.REGISTRIES.items() if k <= 2}
    )
    accepted_fs08(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        original = source_state(conn)
        prior = book_preservation.capture(conn, 1)
        book_upgrade.install(conn, ROOT)
        identities = book_store.population(conn, "sbi-expanded")
    batches, originals, archives = source_inputs(identities)
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        book_runtime.publish(
            conn,
            "sbi-expanded",
            "tlws-mf-official-facts-refresh-dev-synthetic-core",
            NOW,
            identities,
            batches,
            originals,
            archives,
            availability=[],
        )
    references = [
        {
            "code": i["code"],
            "batch_id": batches[0]["batch_id"],
            "as_of": "2026-09-30",
            "values": {
                "benchmark_name": "BSE 500 (TRI)",
                "fund_managers": ["Mr. Anup Upadhyay"],
                "base_expense_ratio": "1.4",
                "aum": "22109.87",
                "risk_level": "Very High",
            },
        }
        for i in identities
    ]
    for sample in references:
        sample["source_values"] = {**sample["values"], "base_expense_ratio": "1.40"}
    real_install = optimized.install
    monkeypatch.setattr(optimized, "install", lambda conn, root: None)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        old_activation = book_projection.activate(
            conn,
            ROOT,
            references,
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        old_values = conn.execute(
            "SELECT * FROM mf_live_facts.current_values ORDER BY code"
        ).fetchall()
        old_rows = conn.execute(
            "SELECT * FROM mf_live_facts.resolved ORDER BY amfi_scheme_code"
        ).fetchall()
        old_columns = conn.execute(
            "SELECT attname,atttypid,atttypmod FROM pg_attribute WHERE attrelid='mf_live_facts.resolved'::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum"
        ).fetchall()
        # The SBI codes have overlay-only rows; pilots also exercise baseline
        # plus overlay. Missing/unavailable fields remain identical, not erased.
        assert {i["code"] for i in identities} <= {r["amfi_scheme_code"] for r in old_rows}
        with conn.transaction(force_rollback=True):
            real_install(conn, ROOT)
            assert (
                conn.execute("SELECT * FROM mf_live_facts.current_values ORDER BY code").fetchall()
                == old_values
            )
            assert (
                conn.execute(
                    "SELECT * FROM mf_live_facts.resolved ORDER BY amfi_scheme_code"
                ).fetchall()
                == old_rows
            )
            assert (
                conn.execute(
                    "SELECT * FROM mf.current_fund_facts ORDER BY amfi_scheme_code"
                ).fetchall()
                == old_rows
            )
            assert (
                conn.execute(
                    "SELECT attname,atttypid,atttypmod FROM pg_attribute WHERE attrelid='mf_live_facts.resolved'::regclass AND attnum>0 AND NOT attisdropped ORDER BY attnum"
                ).fetchall()
                == old_columns
            )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        book_projection.recover(conn, ROOT, old_activation["transition_id"])
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        book_upgrade.install(conn, ROOT)
    monkeypatch.setattr(optimized, "install", real_install)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        result = book_projection.activate(
            conn,
            ROOT,
            references,
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
        active = dict(current.configuration(conn))
        assert "selected_field_once." in active["view_definitions"]["current_values"]
        assert book_preservation.capture(conn, 1) == prior
        assert source_state(conn) == original
        replay_current_values(
            conn,
            Path(ROOT),
            policy_hash=active["policy_hash"],
            expected_definition=active["view_definitions"]["current_values"],
        )
        assert {
            name: current.view_definition(conn, "mf_live_facts." + name) for name in ORDER
        } == active["view_definitions"]
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert book_projection.recover(conn, ROOT, result["transition_id"])["status"] == "RECOVERED"
        assert (
            book_projection.recover(conn, ROOT, result["transition_id"])["status"]
            == "ALREADY_RECOVERED"
        )
        assert book_preservation.capture(conn, 1) == prior
        assert source_state(conn) == original
