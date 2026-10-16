"""Narrow scratch-only replay of the reproduced pg_dump UNION-alias roundtrip.

Do not normalize arbitrary SQL or rewrite a stored seal to make restore pass.
Replay only the pinned original owned view, then require the original seals.
Full source/row/privilege comparison remains the caller's next mandatory gate.
"""

import re
from pathlib import Path
from mfdataindia.enrichment import current_facts, current_facts_store as current
from mfdataindia.enrichment import monthly_current, refresh_store
from mfdataindia.enrichment.current_view_ddl import replay_current_values
from mfdataindia.review.policy import fingerprint
from mfdataindia.store.locks import acquire_nav_data_operation_lock


def replay_owned_current_values(conn, root: Path, *, scratch_database: str, backup_sha256: str):
    if (
        conn.info.host not in ("127.0.0.1", "::1", "localhost")
        or not 55500 <= conn.info.port <= 55699
        or conn.info.dbname != scratch_database
        or not re.fullmatch(
            r"mfdata_(?:fs[0-9]{2}_[a-z0-9_]{1,60}|facts_process_[a-z0-9_]{1,60}|verified_[0-9a-f]{12})",
            scratch_database,
        )
        or not re.fullmatch("[0-9a-f]{64}", backup_sha256)
    ):
        raise ValueError(
            "exact local scratch target and bound backup hash required; never source/prod"
        )
    with conn.transaction():
        who = conn.execute(
            "SELECT current_database() AS database,session_user AS actor,current_user=session_user AS direct"
        ).fetchone()
        if who != {"database": scratch_database, "actor": "postgres", "direct": True}:
            raise ValueError("direct scratch restore owner required")
        if (
            conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
            != "repeatable read"
        ):
            raise ValueError("consistent scratch reconciliation required")
        conn.execute("SET LOCAL statement_timeout='5s'")
        conn.execute("SET LOCAL transaction_timeout='15s'")
        acquire_nav_data_operation_lock(conn)
        cfg = current.configuration(conn)
        core = current.view_definition(conn, "mf.current_fund_facts")
        if core not in (cfg["baseline_definition"], cfg["view_definitions"]["resolved"]):
            raise ValueError("restored core differs from the captured approved target")
        actual = {
            name: current.view_definition(conn, "mf_live_facts." + name)
            for name in sorted(current.VIEWS)
        }
        mismatches = {
            name for name in current.VIEWS if actual[name] != cfg["view_definitions"][name]
        }
        result = {
            "level": "LOCAL_RESTORED_VIEW_REPLAY_NOT_DEV_CERTIFICATION",
            "database": scratch_database,
            "backup_sha256": backup_sha256,
            "source_rows_changed": 0,
            "configuration_rows_changed": 0,
            "requires_full_source_preservation_comparison": True,
        }
        if not mismatches:
            current.verify_acl(conn)
            return {**result, "status": "ALREADY_EXACT"}
        if mismatches != {"current_values"}:
            raise ValueError("restored differences exceed the reproduced owned-view roundtrip")
        version = (
            1
            if cfg["policy_hash"] == fingerprint(current_facts.policy())
            else 2
            if cfg["policy_hash"] == fingerprint(monthly_current.policy())
            else None
        )
        if version is None:
            raise ValueError("unknown restored current-facts policy")
        tokens = ["benchmark", "fund_manager_name", "base_expense_ratio"] + (
            ["risk_level", "aum"] if version == 2 else []
        )
        reproduced = cfg["view_definitions"]["current_values"]
        for token in tokens:
            reproduced = reproduced.replace(
                "'" + token + "'::text,", "'" + token + "'::text AS text,"
            )
        if reproduced != actual["current_values"]:
            raise ValueError("restored view is not the reproduced internal-alias-only difference")
        pin = current.SCHEMA_SHA256 if version == 1 else refresh_store.PROJECTION_SHA256
        replay_current_values(
            conn,
            root,
            policy_hash=cfg["policy_hash"],
            expected_definition=cfg["view_definitions"]["current_values"],
        )
        current.verify_acl(conn)
        if current.configuration(conn) != cfg:
            raise ValueError("original immutable configuration changed during replay")
        return {
            **result,
            "status": "ORIGINAL_REVIEWED_DDL_REPLAYED",
            "original_migration_sha256": pin,
            "all_original_view_seals_restored": True,
            "configuration_hash": fingerprint(dict(cfg)),
            "restored_before_hash": fingerprint(actual["current_values"]),
            "restored_after_hash": cfg["view_hashes"]["current_values"],
        }
