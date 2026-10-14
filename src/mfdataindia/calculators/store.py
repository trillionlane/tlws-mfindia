"""Persist the executable policy only; never persist financial scenarios."""

from mfdataindia.calculators import forward
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.publication_operations import capture_review, compare_review


def installed_policy(conn):
    document = forward.policy()
    row = conn.execute(
        "SELECT document FROM mf_review.policies WHERE policy_hash=%s", (fingerprint(document),)
    ).fetchone()
    if not row or row["document"] != document:
        raise ValueError("forward calculator policy not installed or differs")
    forward.validate_policy(row["document"])
    return row["document"]


def install_policy(conn):
    identity = conn.execute(
        "SELECT session_user AS principal,current_user=session_user AS direct"
    ).fetchone()
    if not identity["direct"] or identity["principal"] not in ("postgres", "mfdata_admin"):
        raise ValueError("direct migration owner required for calculator policy installation")
    if (
        conn.execute("SHOW transaction_isolation").fetchone()["transaction_isolation"]
        != "repeatable read"
    ):
        raise ValueError("consistent calculator policy installation required")
    prior = capture_review(conn)
    document = forward.policy()
    ph = fingerprint(document)
    review._insert_exact(
        conn,
        "policies",
        "policy_hash",
        ph,
        document,
        {"policy_id": document["id"], "policy_version": document["version"]},
    )
    compare_review(prior, capture_review(conn), added_policy=ph)
    installed_policy(conn)
    return {
        "policy_hash": ph,
        "source_writes": 0,
        "schema_changes": 0,
        "personal_scenarios_persisted": 0,
        "prior_review_preserved": True,
    }
