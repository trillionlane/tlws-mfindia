"""Owner-only reversible book overlay; recovery uses sealed prior intent only."""

import hashlib

from psycopg import sql

from mfdataindia.enrichment import (
    book_sources,
    book_store,
    current_facts_store as current,
    multi_facts,
)
from mfdataindia.enrichment.current_view_ddl import replay_current_values
from mfdataindia.enrichment.monthly_projection import ORDER, owner, seal_configuration
from mfdataindia.review.policy import fingerprint
from mfdataindia.store.locks import acquire_nav_data_operation_lock

PROJECTION = "amc_book_current_projection.sql.inc"
PROJECTION_SHA256 = "b6d98a57b40dbbcda400b26adf87218b697b8a00aaa3f013a52b3853bcb9aa82"
PROJECTIONS = {
    1: (PROJECTION, PROJECTION_SHA256),
    2: (
        "amc_book_current_projection_v2.sql.inc",
        "1713c2de7427f4badfcdafffc8b78384877d18b40e65e2971083fe6493895376",
    ),
    # Same accepted-only projection semantics; only sealed source authority grows.
    3: (
        "amc_book_current_projection_v2.sql.inc",
        "1713c2de7427f4badfcdafffc8b78384877d18b40e65e2971083fe6493895376",
    ),
    4: (
        "amc_book_current_projection_v2.sql.inc",
        "1713c2de7427f4badfcdafffc8b78384877d18b40e65e2971083fe6493895376",
    ),
    5: (
        "amc_book_current_projection_v2.sql.inc",
        "1713c2de7427f4badfcdafffc8b78384877d18b40e65e2971083fe6493895376",
    ),
}


def transition(conn, body):
    doc = {**body, "transition_id": fingerprint(body)}
    return book_store.append_exact(conn, "transitions", "transition_id", doc, {})


def check(conn, reviewed, *, preview=None):
    version = book_store.installed_configuration(conn)["document"]["registry"]["version"]
    if (current.policy_document(conn) if preview is None else preview.policy) != book_sources.current_policy(
        version
    ) or not current.projection_state(conn):
        raise ValueError("registered book projection required")
    if not isinstance(reviewed, list) or not 1 <= len(reviewed) <= 10:
        raise ValueError("bounded independent original-field references required")
    if len({r["code"] for r in reviewed}) != len(reviewed):
        raise ValueError("distinct reviewed fund samples required")
    for sample in reviewed:
        keys = {"code", "batch_id", "as_of", "values"}
        if version > 1:
            keys.add("source_values")
        if set(sample) != keys or not sample["values"]:
            raise ValueError("nonempty reviewed fields and dated batch reference required")
        if version > 1:
            if not isinstance(sample["source_values"], dict) or set(sample["source_values"]) != set(
                sample["values"]
            ):
                raise ValueError("separate original-token and canonical-value references required")
            matches = [
                r
                for r in book_store.fund(conn, sample["code"])["observations"]
                if r["batch_id"] == sample["batch_id"]
            ]
            if (
                len(matches) != 1
                or not matches[0]["current_identity_matches"]
                or matches[0]["disclosure_as_of"] != sample["as_of"]
            ):
                raise ValueError("exact original source sample required")
            for key, value in sample["source_values"].items():
                actual = matches[0]["result"]["fields"].get(key)
                if not actual or actual["status"] != "accepted" or actual["value"] != value:
                    raise ValueError(
                        "original source disagrees with independent raw-token reference"
                    )
        doc = current.get(conn, sample["code"]) if preview is None else {"fields": preview.fields(conn, sample["code"])}
        for field, value in sample["values"].items():
            actual = doc["fields"].get(field)
            if (
                not actual
                or actual["state"] != "available"
                or actual["value"] != value
                or actual["disclosure_as_of"] != sample["as_of"]
                or sample["batch_id"] not in {s.get("batch_id") for s in actual["sources"]}
            ):
                raise ValueError("current field disagrees with independent book reference")
    return {"samples_checked": len(reviewed), "source_rows_changed": 0}


def activate(conn, root, reviewed, *, expected_configuration_hash):
    if book_sources.CURRENT_VERSION >= 5:
        from mfdataindia.enrichment import book_activation

        return book_activation.activate(conn, root, reviewed, expected_configuration_hash=expected_configuration_hash)
    with conn.transaction():
        owner(conn)
        if book_sources.CURRENT_VERSION >= 5:
            acquire_nav_data_operation_lock(conn)
        book_store.verify_acl(conn, principal="mfdata_verified_reader")
        version = book_store.installed_configuration(conn)["document"]["registry"]["version"]
        if version != book_sources.CURRENT_VERSION:
            raise ValueError("latest reviewed source authority must be installed before activation")
        prior = current.configuration(conn)
        if fingerprint(dict(prior)) != expected_configuration_hash:
            raise ValueError("approved prior configuration changed before book activation")
        if prior["policy_hash"] == fingerprint(book_sources.current_policy()):
            check(conn, reviewed)
            return {"status": "ALREADY_ACTIVE", "source_rows_changed": 0}
        if prior["policy_hash"] != fingerprint(
            multi_facts.current_policy()
            if version == 1
            else book_sources.current_policy(version - 1)
        ) or not current.projection_state(conn):
            raise ValueError("accepted previous active book baseline required")
        status = book_store.status(conn)
        if any(
            not s["population_matches_latest_success"] or len(s["periods"]) != 2
            for s in status["sources"]
        ):
            raise ValueError("complete current two-period source receipts required")
        projection, projection_hash = PROJECTIONS[version]
        raw = (root / "sql/optional" / projection).read_text()
        if hashlib.sha256(raw.encode()).hexdigest() != projection_hash:
            raise ValueError("book projection differs from reviewed pin")
        source_upgrade = None
        target_preservation = {}
        if version >= 5:
            from mfdataindia.enrichment import book_preservation_v2

            target_preservation = {
                key: book_preservation_v2.capture_target(conn, key)
                for key in book_preservation_v2.revised_keys(version)
            }
        if version > 1:
            from mfdataindia.enrichment import book_upgrade

            source_upgrade = book_upgrade.prior_upgrade(conn, version)
            if source_upgrade["prior_current_configuration_hash"] != fingerprint(dict(prior)):
                raise ValueError("source upgrade was reviewed against a different prior graph")
        intent = transition(
            conn,
            {
                "format": "official-amc-book-transition-v1",
                "mode": "activate",
                "prior_configuration": dict(prior),
                "prior_core_definition": current.view_definition(conn, "mf.current_fund_facts"),
                "to_generation": prior["generation"] + 1,
                **({"source_upgrade_intent": source_upgrade} if source_upgrade else {}),
                **({"target_value_preservation": target_preservation} if target_preservation else {}),
            },
        )
        conn.execute(raw)
        if version >= 2:
            from mfdataindia.enrichment import book_core_projection

            book_core_projection.install(conn, root)
        definitions = {
            name: current.view_definition(conn, "mf_live_facts." + name) for name in ORDER
        }
        seal_configuration(
            conn,
            intent["to_generation"],
            prior["baseline_definition"],
            fingerprint(book_sources.current_policy()),
            definitions,
        )
        current.verify_acl(conn)
        check(conn, reviewed)
        target_checked = {}
        if target_preservation:
            target_checked = {
                key: book_preservation_v2.compare_target(before, book_preservation_v2.capture_target(conn, key))
                for key, before in target_preservation.items()
            }
        return {
            "status": "ACTIVATED",
            "transition_id": intent["transition_id"],
            "generation": intent["to_generation"],
            "source_rows_changed": 0,
            **({"target_value_preservation": target_checked} if target_checked else {}),
        }


def recover(conn, root, activation_id):
    with conn.transaction():
        owner(conn)
        row = conn.execute(
            "SELECT document FROM mf_book_facts.transitions WHERE transition_id=%s",
            (activation_id,),
        ).fetchone()
        if not row:
            raise ValueError("sealed book recovery intent required")
        doc = row["document"]
        if (
            doc.get("mode") != "activate"
            or fingerprint({k: v for k, v in doc.items() if k != "transition_id"}) != activation_id
        ):
            raise ValueError("book recovery intent differs")
        prior = doc["prior_configuration"]
        if (
            prior["policy_hash"]
            not in {
                fingerprint(multi_facts.current_policy()),
                *(fingerprint(p) for p in book_sources.current_policies()),
            }
            or doc["prior_core_definition"] != prior["view_definitions"]["resolved"]
        ):
            raise ValueError("sealed recognized historical prior graph required")
        latest = conn.execute(
            "SELECT generation FROM mf_live_facts.configuration ORDER BY generation DESC LIMIT 1"
        ).fetchone()["generation"]
        recovered = conn.execute(
            "SELECT document FROM mf_book_facts.transitions WHERE document->>'mode'='recover' AND document->>'activation_id'=%s AND (document->>'to_generation')::integer=%s",
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
            return {"status": "ALREADY_RECOVERED", "generation": latest, "source_rows_changed": 0}
        if latest != doc["to_generation"]:
            raise ValueError("stale book intent cannot replace a later approved graph")
        # No candidate inventory, source configuration or image lookup can block
        # restoring the already-captured traffic-independent database graph.
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
                raise ValueError("recovered prior graph seal differs")
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
        if doc.get("source_upgrade_intent"):
            from mfdataindia.enrichment import book_upgrade

            book_upgrade.restore(conn, doc["source_upgrade_intent"])
        result = transition(
            conn,
            {
                "format": "official-amc-book-transition-v1",
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
