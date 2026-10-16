"""Short owner-only additive activation and sealed independent prior-graph recovery."""

import hashlib
from psycopg import sql
from mfdataindia.enrichment import (
    current_facts_store as current,
    monthly_current,
    multi_facts,
    multi_store,
)
from mfdataindia.enrichment.monthly_projection import owner, seal_configuration, ORDER
from mfdataindia.enrichment.current_view_ddl import replay_current_values
from mfdataindia.review.policy import fingerprint

PROJECTION = "registered_current_projection.sql.inc"
PROJECTION_SHA256 = "027654db174cc571f30ace15ba11493d2dc9a98dd037276e2d17091483a0da64"


def transition(conn, body):
    doc = {**body, "transition_id": fingerprint(body)}
    return multi_store.append_exact(conn, "transitions", "transition_id", doc, {})


def check(conn, reviewed):
    code = multi_facts.source()["identity"]["code"]
    document = current.get(conn, code)
    if current.policy_document(
        conn
    ) != multi_facts.current_policy() or not current.projection_state(conn):
        raise ValueError("registered current projection is not active")
    if set(reviewed) != {"snapshot_id", "as_of", "values"} or set(reviewed["values"]) != {
        "benchmark_name",
        "fund_managers",
        "base_expense_ratio",
        "aum",
    }:
        raise ValueError("independent four-field original reference required")
    for field, value in reviewed["values"].items():
        actual = document["fields"][field]
        if (
            actual["state"] != "available"
            or actual["value"] != value
            or actual["disclosure_as_of"] != reviewed["as_of"]
            or reviewed["snapshot_id"] not in {s["snapshot_id"] for s in actual["sources"]}
        ):
            raise ValueError("registered current field differs from original-source reference")
    return document


def activate(conn, root, reviewed, *, expected_configuration_hash=None):
    with conn.transaction():
        owner(conn)
        multi_store.verify_acl(conn, principal="mfdata_verified_reader")
        current.verify_acl(conn)
        prior = current.configuration(conn)
        if (
            expected_configuration_hash is not None
            and fingerprint(dict(prior)) != expected_configuration_hash
        ):
            raise ValueError("approved prior configuration changed before activation")
        if prior["policy_hash"] == fingerprint(multi_facts.current_policy()):
            check(conn, reviewed)
            return {"status": "ALREADY_ACTIVE", "source_rows_changed": 0}
        if prior["policy_hash"] != fingerprint(
            monthly_current.policy()
        ) or not current.projection_state(conn):
            raise ValueError("accepted active pilot projection required")
        if conn.execute(
            "SELECT 1 FROM mf_multi_facts.prior_field_versions WHERE code=101763 LIMIT 1"
        ).fetchone():
            raise ValueError("registered source cohort must be disjoint from frozen pilot")
        raw = (root / "sql/optional" / PROJECTION).read_text()
        if hashlib.sha256(raw.encode()).hexdigest() != PROJECTION_SHA256:
            raise ValueError("registered projection differs from reviewed pin")
        doc = transition(
            conn,
            {
                "format": "registered-projection-transition-v1",
                "mode": "activate",
                "prior_configuration": dict(prior),
                "prior_core_definition": current.view_definition(conn, "mf.current_fund_facts"),
                "to_generation": prior["generation"] + 1,
            },
        )
        conn.execute(raw)
        definitions = {
            name: current.view_definition(conn, "mf_live_facts." + name) for name in ORDER
        }
        seal_configuration(
            conn,
            doc["to_generation"],
            prior["baseline_definition"],
            fingerprint(multi_facts.current_policy()),
            definitions,
        )
        current.verify_acl(conn)
        check(conn, reviewed)
        return {
            "status": "ACTIVATED",
            "transition_id": doc["transition_id"],
            "generation": doc["to_generation"],
            "source_rows_changed": 0,
        }


def recover(conn, root, activation_id):
    with conn.transaction():
        owner(conn)
        # Consult only the immutable owner intent, not failing candidate/source checks.
        row = conn.execute(
            "SELECT document FROM mf_multi_facts.transitions WHERE transition_id=%s",
            (activation_id,),
        ).fetchone()
        if not row:
            raise ValueError("sealed prior-graph recovery intent required")
        doc = row["document"]
        if (
            doc.get("mode") != "activate"
            or fingerprint({k: v for k, v in doc.items() if k != "transition_id"}) != activation_id
        ):
            raise ValueError("recovery intent differs")
        prior = doc["prior_configuration"]
        if (
            prior["policy_hash"] != fingerprint(monthly_current.policy())
            or doc["prior_core_definition"] != prior["view_definitions"]["resolved"]
        ):
            raise ValueError("known active prior pilot graph required")
        latest = conn.execute(
            "SELECT generation FROM mf_live_facts.configuration ORDER BY generation DESC LIMIT 1"
        ).fetchone()["generation"]
        recovered = conn.execute(
            "SELECT document FROM mf_multi_facts.transitions WHERE document->>'mode'='recover' AND document->>'activation_id'=%s AND (document->>'to_generation')::integer=%s",
            (activation_id, latest),
        ).fetchone()
        if recovered:
            cfg = current.configuration(conn)
            current.verify_acl(conn)
            if (
                cfg["policy_hash"] != prior["policy_hash"]
                or cfg["view_definitions"] != prior["view_definitions"]
                or current.view_definition(conn, "mf.current_fund_facts")
                != doc["prior_core_definition"]
            ):
                raise ValueError("recovery replay target differs")
            return {
                "status": "ALREADY_RECOVERED",
                "transition_id": recovered["document"]["transition_id"],
                "generation": latest,
                "source_rows_changed": 0,
            }
        if latest != doc["to_generation"]:
            raise ValueError("stale recovery cannot replace a later approved transition")
        for name in ORDER:
            if name == "current_values":
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
        for name, definition in prior["view_definitions"].items():
            if current.view_definition(conn, "mf_live_facts." + name) != definition:
                raise ValueError("restored prior graph seal differs")
        conn.execute(
            "CREATE OR REPLACE VIEW mf.current_fund_facts AS " + doc["prior_core_definition"]
        )
        seal_configuration(
            conn,
            latest + 1,
            prior["baseline_definition"],
            prior["policy_hash"],
            prior["view_definitions"],
        )
        result = transition(
            conn,
            {
                "format": "registered-projection-transition-v1",
                "mode": "recover",
                "activation_id": activation_id,
                "to_generation": latest + 1,
            },
        )
        current.verify_acl(conn)
        return {
            "status": "RECOVERED",
            "transition_id": result["transition_id"],
            "generation": latest + 1,
            "source_rows_changed": 0,
        }
