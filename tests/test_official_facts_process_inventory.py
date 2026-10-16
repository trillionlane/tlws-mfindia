"""Inventory honesty: actual counts and declared gaps are not HTTP/cloud acceptance."""

import psycopg
import pytest
from psycopg.rows import dict_row
from mfdataindia.enrichment.process_inventory import inventory
from test_fact_verification import prepared
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES
from test_verified_review_store import verified_db as verified_db


@pytest.mark.postgres
def test_inventory_reports_actual_partial_coverage_without_promoting_verification(
    verified_db, monkeypatch
):
    prepared(verified_db, monkeypatch)
    with psycopg.connect(runtime_dsn(verified_db, ROLES[1]), row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        result = inventory(conn)
    assert result["source_books"] == 1 and len(result["snapshots"]) == 2
    assert result["current_projection_active"] and result["current_policy_is_monthly_v2"]
    assert result["human_reviews_created_by_inventory"] == 0
    assert result["verification_inventory"]["events"]["rows"] == 0
    assert result["requires_external_cloud_execution_witness"]
    for key in (
        "all_amc_coverage",
        "entire_factsheet_coverage",
        "idcw_total_return_unlocked",
        "full_economic_overlap_certified",
        "private_http_certified_by_inventory",
        "UI_certified",
        "production_certified",
    ):
        assert result[key] is False
    for state in result["verification"].values():
        assert all(field["status"] == "needs_verification" for field in state["fields"].values())
    with psycopg.connect(runtime_dsn(verified_db, ROLES[1]), row_factory=dict_row) as conn:
        with pytest.raises(ValueError, match="read-only"):
            inventory(conn)
