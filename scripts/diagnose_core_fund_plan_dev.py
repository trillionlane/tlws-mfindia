#!/usr/bin/env python3
"""One exact-code read-only plan diagnostic; never a rollout or performance certificate.

No cloud creation or dispatch occurs here. A separately reviewed create-once
controller must bind this complete program and immutable job inputs before use.
The expensive sibling queries are planned, NOT executed. There are no SQL,
target, timeout, role or retry command-line options.
"""

import argparse
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys

import psycopg
from psycopg.rows import dict_row

from mfdataindia.api import queries
from mfdataindia.enrichment import book_sources, current_facts_store as current
from mfdataindia.enrichment.deployment import pinned_dsn
from mfdataindia.review.evidence_transport import render_evidence
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.privileges import verify_runtime
from mfdataindia.review.rollout import Manifest, seal_evidence

CODE = 154117
DIAGNOSTIC_ID = "dev-core-plan-261021a"
QUERY_FILE_SHA256 = "c2043667ab261be1ae02eb9e5f32658c142e325739487582a555530f0e3f691e"
STAGE = "core-plan-diagnostic-not-acceptance"
CONTEXT_SQL = """SELECT current_user AS effective, session_user AS authenticated,
       current_database() AS database,
       current_setting('transaction_read_only') AS read_only,
       current_setting('transaction_isolation') AS isolation,
       current_setting('statement_timeout') AS statement_timeout,
       current_setting('lock_timeout') AS lock_timeout,
       current_setting('transaction_timeout') AS transaction_timeout,
       pg_current_snapshot()::text AS snapshot
"""


def fixed_queries():
    """Reject different query source bytes before opening a database session."""
    if hashlib.sha256(Path(queries.__file__).read_bytes()).hexdigest() != QUERY_FILE_SHA256:
        raise ValueError("reviewed original query module differs")
    return (
        ("own_facts", queries._FACTS_SELECT, {"code": CODE}),
        ("single_sibling", queries._SIBLING_SOURCE_SQL, {"code": CODE}),
        ("batch_sibling", queries._BATCH_SIBLING_SOURCE_SQL, {"codes": [CODE]}),
    )


def worker_binding(manifest, environment):
    """Bind actual packaged bytes, not an impossible old-image script path.

    The controller must separately verify actual immutable OCI revision/config
    bytes and the WHOLE deployed/immutable task. An environment echo alone is
    not image provenance. No inline injected-program path is supported.
    """
    program_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if (manifest.rollout_id != DIAGNOSTIC_ID or manifest.cohort != [CODE]
            or manifest.canary_code != CODE
            or environment.get("DEPLOYMENT_GIT_SHA") != manifest.git_sha
            or environment.get("CORE_PLAN_PROGRAM_SHA256") != program_hash):
        raise ValueError("fresh diagnostic identity/Git/packaged program binding required")
    return {"packaged_program_sha256": program_hash,
            "manifest_image": manifest.image, "manifest_git_sha": manifest.git_sha}


def sealed_projection(conn):
    """Stored configuration alone is insufficient: inspect the active facade."""
    captured = current.capture(conn)
    if captured is None or captured["projection_active"] is not True:
        raise ValueError("active sealed source4 projection required; no baseline fallback")
    return {"complete_capture": captured,
            "active_core_view_hash": fingerprint(current.view_definition(conn, "mf.current_fund_facts"))}


def checked_context(conn):
    row = dict(conn.execute(CONTEXT_SQL).fetchone())
    expected = {
        "effective": "mfdata_verified_reader",
        "authenticated": "mfdata_verified_reader",
        "database": "mfdataindia",
        "read_only": "on",
        "isolation": "repeatable read",
        "statement_timeout": "5s",
        "lock_timeout": "5s",
        "transaction_timeout": "15s",
    }
    if set(row) != set(expected) | {"snapshot"} or not row["snapshot"]:
        raise ValueError("complete diagnostic transaction context required")
    if {key: row[key] for key in expected} != expected:
        raise ValueError("diagnostic identity or transaction bounds differ")
    return row


def own_facts_hash(value):
    """Lossless typed diagnostic seal, not a replacement financial proof."""
    def typed(item):
        if isinstance(item, Decimal):
            return {"diagnostic_type": "Decimal", "value": str(item)}
        if isinstance(item, (date, datetime)):
            return {"diagnostic_type": type(item).__name__, "value": item.isoformat()}
        raise TypeError("unexpected facts value type")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False,
                                     default=typed).encode()).hexdigest()


def diagnose(conn):
    statements = fixed_queries()
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    conn.execute("SET LOCAL statement_timeout='5s'")
    conn.execute("SET LOCAL lock_timeout='5s'")
    conn.execute("SET LOCAL transaction_timeout='15s'")
    before = checked_context(conn)
    verify_runtime(conn, "api")
    configuration = dict(current.configuration(conn))
    if configuration["policy_hash"] != fingerprint(book_sources.current_policy(4)):
        raise ValueError("accepted recovered source4 policy required; no state restoration")
    projection = sealed_projection(conn)
    plans = []
    for label, statement, bindings in statements:
        rows = conn.execute("EXPLAIN (FORMAT JSON) " + statement, bindings).fetchall()
        if len(rows) != 1 or set(rows[0]) != {"QUERY PLAN"}:
            raise ValueError("complete original EXPLAIN row required")
        plan = rows[0]["QUERY PLAN"]
        if (not isinstance(plan, list) or len(plan) != 1
                or not isinstance(plan[0], dict) or "Plan" not in plan[0]
                or "Execution Time" in plan[0]):
            raise ValueError("non-executing complete original plan required")
        plans.append({
            "query": label,
            "SQL_sha256": hashlib.sha256(statement.encode()).hexdigest(),
            "bindings": bindings,
            "complete_plan": plan,
            "complete_plan_hash": fingerprint(plan),
            "ANALYZE": False,
        })
    # Exactly one actual application facts SELECT. No actual sibling join,
    # endpoint replay, candidate SQL or retry on failure/timeout is permitted.
    own_rows = conn.execute(statements[0][1], statements[0][2]).fetchall()
    if len(own_rows) > 1:
        raise ValueError("duplicate own facts; diagnostic cannot guess")
    own = dict(own_rows[0]) if own_rows else None
    after = checked_context(conn)
    if (before != after or dict(current.configuration(conn)) != configuration
            or sealed_projection(conn) != projection):
        raise ValueError("diagnostic snapshot/configuration context differs")
    return {
        "level": "CURRENT_RECOVERED_STATE_READONLY_PLAN_NOT_HISTORICAL_OR_ACCEPTANCE",
        "code": CODE,
        "query_file_sha256": QUERY_FILE_SHA256,
        "context": before,
        "configuration_generation": configuration["generation"],
        "complete_configuration_hash": fingerprint(configuration),
        "policy_hash": configuration["policy_hash"],
        "sealed_active_projection": projection,
        "plans": plans,
        "original_own_facts_rows": len(own_rows),
        "original_own_facts_typed_diagnostic_hash": own_facts_hash(own),
        "current_own_facts_trigger_fallback": not queries._facts_are_usable(own),
        "actual_sibling_queries_executed": 0,
        "historical_K_plan_or_core_role_proved": False,
        "performance_acceptance": False,
        "data_writes": 0,
        "source_requests": 0,
        "human_events_created": 0,
        "rollout_acceptance": False,
    }


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    fixed_queries()
    manifest = Manifest.model_validate_json(os.environ["REVIEW_MANIFEST"])
    binding = worker_binding(manifest, os.environ)
    dsn = pinned_dsn(manifest, os.environ, "mfdata_verified_reader")
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as conn:
        result = {**diagnose(conn), "worker_binding": binding}
    print(render_evidence(seal_evidence(manifest, STAGE, result)), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not disclose DSN, exception text, query parameters or server output.
        print("core plan diagnostic failed: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
