"""Replay the exact reviewed current-values definition, never reseal deparser drift."""

import hashlib
from pathlib import Path
from mfdataindia.enrichment import current_facts, current_facts_store as current, monthly_current, multi_facts, book_sources
from mfdataindia.review.policy import fingerprint


def replay_current_values(conn, root: Path, *, policy_hash: str, expected_definition: str):
    who = conn.execute(
        "SELECT session_user AS actor,current_user=session_user AS direct"
    ).fetchone()
    if not who["direct"] or who["actor"] not in ("postgres", "mfdata_admin"):
        raise ValueError("direct current-view owner required")
    if "selected_field_once." in expected_definition:
        from mfdataindia.enrichment import book_core_projection

        if policy_hash not in [
            fingerprint(document)
            for document in book_sources.current_policies()
            if document["version"] >= 5
        ]:
            raise ValueError("single-pass graph requires a recognized book policy")
        book_core_projection.replay_values(conn, root, expected_definition)
        return
    if policy_hash == fingerprint(current_facts.policy()):
        filename, pin, marker = (
            current.MIGRATION,
            current.SCHEMA_SHA256,
            "CREATE VIEW mf_live_facts.current_values AS\n",
        )
    elif (policy_hash in (fingerprint(monthly_current.policy()), fingerprint(multi_facts.current_policy()))
          or policy_hash in [fingerprint(p) for p in book_sources.current_policies()]):
        from mfdataindia.enrichment import refresh_store

        filename, pin, marker = (
            refresh_store.PROJECTION,
            refresh_store.PROJECTION_SHA256,
            "CREATE OR REPLACE VIEW mf_live_facts.current_values AS\n",
        )
    else:
        raise ValueError("unknown current-values policy cannot be replayed")
    raw = (root / "sql/optional" / filename).read_text()
    if hashlib.sha256(raw.encode()).hexdigest() != pin or raw.count(marker) != 1:
        raise ValueError("current-values source differs from reviewed original SQL")
    tail = raw.split(marker, 1)[1]
    separator = "\n\nDO $$ DECLARE projection text;"
    if tail.count(separator) != 1:
        raise ValueError("one exact current-values statement boundary required")
    conn.execute(
        "CREATE OR REPLACE VIEW mf_live_facts.current_values AS\n" + tail.split(separator, 1)[0]
    )
    if current.view_definition(conn, "mf_live_facts.current_values") != expected_definition:
        raise ValueError("replayed original current-values does not match captured seal")
