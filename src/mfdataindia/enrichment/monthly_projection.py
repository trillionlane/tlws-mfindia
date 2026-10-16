"""Short reversible owner transitions; no source rows or old configurations rewritten."""

import hashlib
from pathlib import Path

from psycopg import sql
from psycopg.types.json import Jsonb

from mfdataindia.api import queries
from mfdataindia.enrichment import current_facts_store as current, monthly_current, refresh_store
from mfdataindia.enrichment import store as evidence
from mfdataindia.review.policy import fingerprint
from mfdataindia.store.locks import acquire_nav_data_operation_lock

ORDER = (
    "baseline",
    "field_versions",
    "field_heads",
    "selected_fields",
    "current_values",
    "resolved",
)


def owner(conn):
    who = conn.execute("SELECT session_user AS name,current_user=session_user AS direct").fetchone()
    if not who["direct"] or who["name"] not in ("postgres", "mfdata_admin"):
        raise ValueError("direct current-projection owner required")
    if (
        conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
        != "read committed"
    ):
        raise ValueError(
            "short current-projection transaction requires READ COMMITTED and shared publication lock"
        )
    conn.execute("SET LOCAL statement_timeout='5s'")
    conn.execute("SET LOCAL lock_timeout='5s'")
    conn.execute("SET LOCAL transaction_timeout='15s'")
    acquire_nav_data_operation_lock(conn)


def check_current(conn, reviewed, *, core_queries=True):
    if set(map(int, reviewed)) != {103173, 103174}:
        raise ValueError("exact current source-reference cohort required")
    from mfdataindia.enrichment import multi_facts
    if current.policy_document(conn) not in (monthly_current.policy(),multi_facts.current_policy()) or not current.projection_state(
        conn
    ):
        raise ValueError("monthly scalar projection is not active")
    core = {}
    for key, expected in reviewed.items():
        code = int(key)
        doc = current.get(conn, code)
        if set(expected["values"]) != set(monthly_current.FIELDS):
            raise ValueError("all five scalar field references required")
        for field, value in expected["values"].items():
            actual = doc["fields"][field]
            if (
                actual["state"] != "available"
                or actual["value"] != value
                or actual["disclosure_as_of"] != expected["as_of"]
                or expected["snapshot_id"] not in {s["snapshot_id"] for s in actual["sources"]}
            ):
                raise ValueError("current field differs from reviewed monthly original")
        if core_queries:
            detail = queries.fund_detail(conn, code)
            batch = queries.funds_batch(conn, [str(code)])
            if not detail or batch["found"] != 1:
                raise ValueError("current core code lookup unavailable")
        values = {}
        for field, aliases in monthly_current.FIELDS.items():
            value = expected["values"][field]
            if field == "fund_managers":
                value = "; ".join(value)
            if field in ("base_expense_ratio", "aum"):
                value = float(value)
            for alias in aliases:
                if core_queries and (
                    detail["facts"][alias] != value
                    or alias != "benchmark"
                    and batch["funds"][0].get(alias) != value
                ):
                    raise ValueError("existing core API disagrees with current monthly source")
                values[alias] = value
        core[key] = values
    return core


def transition(conn, body):
    doc = {**body, "transition_id": fingerprint(body)}
    conn.execute(
        "INSERT INTO mf_fact_refresh.transitions VALUES(%s,%s,%s,%s)",
        (doc["transition_id"], doc["from_generation"], doc["to_generation"], Jsonb(doc)),
    )
    return doc


def seal_configuration(conn, generation, baseline, policy_hash, definitions):
    conn.execute(
        "INSERT INTO mf_live_facts.configuration VALUES(%s,%s,%s,%s,%s,%s)",
        (
            generation,
            baseline,
            hashlib.sha256(baseline.encode()).hexdigest(),
            policy_hash,
            Jsonb({name: fingerprint(value) for name, value in definitions.items()}),
            Jsonb(definitions),
        ),
    )


def activate(conn, root, *, legacy_snapshot_ids, reviewed):
    with conn.transaction():
        owner(conn)
        refresh_store.verify_acl(conn, principal=evidence.ROLES[1])
        current.verify_acl(conn)
        prior = current.configuration(conn)
        if prior["policy_hash"] == fingerprint(monthly_current.policy()):
            return {
                "status": "ALREADY_ACTIVE",
                "core_values": check_current(conn, reviewed),
                "source_rows_changed": 0,
            }
        if len(legacy_snapshot_ids) > 10 or len(set(legacy_snapshot_ids)) != len(
            legacy_snapshot_ids
        ):
            raise ValueError("bounded unique approved legacy inputs required")
        rows = conn.execute(
            "SELECT snapshot_id,amfi_scheme_code,document FROM mf_official_facts.snapshots WHERE snapshot_id=ANY(%s)",
            (legacy_snapshot_ids,),
        ).fetchall()
        if len(rows) != len(legacy_snapshot_ids) or any(
            r["amfi_scheme_code"] not in (103173, 103174)
            or r["document"]["identity_hash"]
            != fingerprint(evidence.identity(conn, r["amfi_scheme_code"]))
            for r in rows
        ):
            raise ValueError("approved legacy source identity differs")
        existing = conn.execute(
            "SELECT DISTINCT s->>'snapshot_id' AS snapshot_id FROM mf_live_facts.selected_fields f CROSS JOIN LATERAL jsonb_array_elements(f.sources) s WHERE f.code IN(103173,103174)"
        ).fetchall()
        if {r["snapshot_id"] for r in existing} != set(legacy_snapshot_ids):
            raise ValueError("legacy current heads differ from explicitly approved inputs")
        types = conn.execute(
            "SELECT attname,format_type(atttypid,atttypmod) AS type FROM pg_attribute WHERE attrelid='mf.current_fund_facts'::regclass AND attname IN('aum','base_expense_ratio')"
        ).fetchall()
        if {r["attname"]: r["type"] for r in types} != {
            "aum": "numeric(20,6)",
            "base_expense_ratio": "numeric(9,4)",
        }:
            raise ValueError("current numeric column types differ from round-trip policy")
        previous_core = current.view_definition(conn, "mf.current_fund_facts")
        doc = transition(
            conn,
            {
                "format": "official-current-projection-transition-v1",
                "mode": "activate_monthly_current",
                "from_generation": prior["generation"],
                "to_generation": prior["generation"] + 1,
                "prior_configuration": dict(prior),
                "prior_core_definition": previous_core,
                "prior_core_hash": fingerprint(previous_core),
                "legacy_snapshot_ids": sorted(legacy_snapshot_ids),
                "target_policy_hash": fingerprint(monthly_current.policy()),
            },
        )
        fragment = (root / "sql/optional" / refresh_store.PROJECTION).read_text()
        if hashlib.sha256(fragment.encode()).hexdigest() != refresh_store.PROJECTION_SHA256:
            raise ValueError("monthly current projection differs from reviewed pin")
        conn.execute(fragment)
        definitions = {
            name: current.view_definition(conn, "mf_live_facts." + name) for name in ORDER
        }
        seal_configuration(
            conn,
            doc["to_generation"],
            prior["baseline_definition"],
            fingerprint(monthly_current.policy()),
            definitions,
        )
        conn.execute(
            "CREATE OR REPLACE VIEW mf.current_fund_facts AS " + current.expected_active(conn)
        )
        current.verify_acl(conn)
        values = check_current(conn, reviewed)
        return {
            "status": "ACTIVATED",
            "transition_id": doc["transition_id"],
            "generation": doc["to_generation"],
            "core_values": values,
            "source_rows_changed": 0,
            "whole_source_preservation_requires_post_activation_certification": True,
        }


def recover(conn, activation_id, *, root=Path("/app")):
    with conn.transaction():
        owner(conn)
        # Do not inspect the failed candidate views or source references first.
        row = conn.execute(
            "SELECT document FROM mf_fact_refresh.transitions WHERE transition_id=%s",
            (activation_id,),
        ).fetchone()
        if not row:
            raise ValueError("exact sealed activation receipt required for recovery")
        doc = row["document"]
        if (
            doc.get("mode") != "activate_monthly_current"
            or fingerprint({k: v for k, v in doc.items() if k != "transition_id"}) != activation_id
            or fingerprint(doc["prior_core_definition"]) != doc["prior_core_hash"]
        ):
            raise ValueError("recovery intent seal differs")
        prior = doc["prior_configuration"]
        if doc["prior_core_definition"] not in (
            prior["baseline_definition"],
            prior["view_definitions"]["resolved"],
        ):
            raise ValueError("recovery target is not a previously approved projection")
        latest = current.configuration(conn)
        previous_recovery = conn.execute(
            "SELECT document FROM mf_fact_refresh.transitions WHERE to_generation=%s AND document->>'mode'='recover_monthly_current' AND document->>'activation_id'=%s",
            (latest["generation"], activation_id),
        ).fetchone()
        already = (
            previous_recovery is not None
            and latest["policy_hash"] == prior["policy_hash"]
            and latest["view_definitions"] == prior["view_definitions"]
        )
        if not already and latest["generation"] != doc["to_generation"]:
            raise ValueError("stale recovery intent cannot replace a later approved transition")
        for name in ORDER:
            if name == "current_values":
                from mfdataindia.enrichment.current_view_ddl import replay_current_values

                replay_current_values(
                    conn,
                    root,
                    policy_hash=prior["policy_hash"],
                    expected_definition=prior["view_definitions"][name],
                )
            else:
                conn.execute(
                    sql.SQL("CREATE OR REPLACE VIEW mf_live_facts.{} AS ").format(
                        sql.Identifier(name)
                    )
                    + sql.SQL(prior["view_definitions"][name])
                )
        if any(
            current.view_definition(conn, "mf_live_facts." + name) != definition
            for name, definition in prior["view_definitions"].items()
        ):
            raise ValueError("recovered graph differs from the original captured seals")
        if already:
            conn.execute(
                "CREATE OR REPLACE VIEW mf.current_fund_facts AS " + doc["prior_core_definition"]
            )
            current.verify_acl(conn)
            return {
                "status": "ALREADY_RECOVERED",
                "source_rows_changed": 0,
                "sealed_target_reapplied": True,
            }
        generation = latest["generation"] + 1
        seal_configuration(
            conn,
            generation,
            prior["baseline_definition"],
            prior["policy_hash"],
            prior["view_definitions"],
        )
        conn.execute(
            "CREATE OR REPLACE VIEW mf.current_fund_facts AS " + doc["prior_core_definition"]
        )
        receipt = transition(
            conn,
            {
                "format": "official-current-projection-transition-v1",
                "mode": "recover_monthly_current",
                "activation_id": activation_id,
                "from_generation": latest["generation"],
                "to_generation": generation,
                "restored_policy_hash": prior["policy_hash"],
                "restored_core_hash": doc["prior_core_hash"],
            },
        )
        if current.view_definition(conn, "mf.current_fund_facts") != doc["prior_core_definition"]:
            raise ValueError("prior current projection recovery unconfirmed")
        current.verify_acl(conn)
        return {
            "status": "RECOVERED",
            "generation": generation,
            "transition_id": receipt["transition_id"],
            "source_rows_changed": 0,
            "whole_source_preservation_requires_post_recovery_certification": True,
        }
