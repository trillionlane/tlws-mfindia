"""Only the executable policy is registered; simulations remain ephemeral."""

from mfdataindia.calculators import historical
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_operations import capture_review, compare_review


def installed_policy(conn):
    document = historical.policy()
    row = conn.execute(
        "SELECT document FROM mf_review.policies WHERE policy_hash=%s", (fingerprint(document),)
    ).fetchone()
    if not row or fingerprint(row["document"]) != fingerprint(document):
        raise ValueError("historical calculator policy not installed or differs")
    historical.validate_policy(row["document"])
    return row["document"]


def install_policy(conn):
    identity = conn.execute(
        "SELECT session_user AS principal,current_user=session_user AS direct"
    ).fetchone()
    if not identity["direct"] or identity["principal"] not in ("postgres", "mfdata_admin"):
        raise ValueError("direct migration owner required for historical policy")
    if (
        conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
        != "repeatable read"
    ):
        raise ValueError("consistent historical policy installation required")
    before = capture_review(conn)
    doc = historical.policy()
    ph = fingerprint(doc)
    review._insert_exact(
        conn,
        "policies",
        "policy_hash",
        ph,
        doc,
        {"policy_id": doc["id"], "policy_version": doc["version"]},
    )
    compare_review(before, capture_review(conn), added_policy=ph)
    installed_policy(conn)
    return {
        "policy_hash": ph,
        "source_writes": 0,
        "schema_changes": 0,
        "personal_scenarios_persisted": 0,
        "prior_review_preserved": True,
    }
