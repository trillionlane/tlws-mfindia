"""Observed pilot coverage and limitations; database evidence is not deployment acceptance."""

from mfdataindia.enrichment import current_facts_store as current, monthly_current, monthly_facts
from mfdataindia.enrichment import (
    monthly_store,
    refresh_store,
    refresh_policy,
    fact_review_policy,
    fact_review_store,
)
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime


def inventory(conn):
    if conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] != "on":
        raise ValueError("read-only process inventory required")
    verify_runtime(conn, "api")
    current.verify_acl(conn)
    monthly_store.verify_acl(conn)
    refresh_store.verify_acl(conn)
    fact_review_store.verify_acl(conn)
    rows = conn.execute(
        "SELECT snapshot_id,amfi_scheme_code,disclosure_as_of,content_hash,document FROM mf_monthly_facts.snapshots WHERE amfi_scheme_code=ANY(%s) ORDER BY amfi_scheme_code,disclosure_as_of,snapshot_id LIMIT 101",
        ([103173, 103174],),
    ).fetchall()
    if len(rows) > 100:
        raise ValueError("bounded pilot inventory exceeded; use a newly approved inventory window")
    coverage = []
    for row in rows:
        fields = row["document"]["fields"]
        coverage.append(
            {
                "code": row["amfi_scheme_code"],
                "snapshot_id": row["snapshot_id"],
                "source_sha256": row["content_hash"],
                "disclosure_as_of": row["disclosure_as_of"].isoformat(),
                "reported_field_slots": len(fields),
                "accepted_fields": sorted(
                    k for k, v in fields.items() if v["status"] == "accepted"
                ),
                "unavailable_fields": {
                    k: v["reason"] for k, v in fields.items() if v["status"] != "accepted"
                },
                "snapshot_initial_verification_status": row["document"]["verification_status"],
                "publisher_reported_is_not_canonical_analytics": True,
            }
        )
    return {
        "format": "official-facts-process-inventory-v1",
        "level": "DEV_READONLY_DATABASE",
        "pilot_fund_name": monthly_facts.NAME,
        "scheme_variant_codes": [103173, 103174],
        "variant_count_is_not_economic_fund_count": True,
        "source_books": len({r["content_hash"] for r in rows}),
        "reporting_periods": sorted({r["disclosure_as_of"].isoformat() for r in rows}),
        "snapshots": coverage,
        "current_projection_active": current.projection_state(conn),
        "current_policy_is_monthly_v2": current.policy_document(conn) == monthly_current.policy(),
        "current": {str(code): current.get(conn, code) for code in (103173, 103174)},
        "refresh": refresh_store.status(conn),
        "verification": {
            str(code): fact_review_store.state(conn, code) for code in (103173, 103174)
        },
        "policy_hashes": {
            "monthly_reporting": fingerprint(monthly_facts.policy()),
            "current": fingerprint(current.policy_document(conn)),
            "refresh": fingerprint(refresh_policy.policy()),
            "human_verification": fingerprint(fact_review_policy.policy()),
        },
        "monthly_inventory": monthly_store.capture(conn),
        "refresh_inventory": refresh_store.capture(conn),
        "verification_inventory": fact_review_store.capture(conn),
        "source_rows_changed": 0,
        "human_reviews_created_by_inventory": 0,
        "all_amc_coverage": False,
        "entire_factsheet_coverage": False,
        "idcw_total_return_unlocked": False,
        "full_economic_overlap_certified": False,
        "requires_external_cloud_execution_witness": True,
        "private_http_certified_by_inventory": False,
        "UI_certified": False,
        "production_certified": False,
    }
