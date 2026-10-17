"""Pinned execution-only projection; original data and selected values unchanged."""

import hashlib

SOURCE = "amc_book_core_projection_v1.sql.inc"
SHA256 = "222f81c25672e66a35bbe3c17be6723ca65495d55a6af571c15bafa00f5aa4f8"
VALUES_MARKER = "CREATE OR REPLACE VIEW mf_live_facts.current_values AS\n"
RESOLVED_MARKER = "\n\nDO $$ DECLARE baseline_projection text;"


def source(root):
    raw = (root / "sql/optional" / SOURCE).read_text()
    if (
        hashlib.sha256(raw.encode()).hexdigest() != SHA256
        or raw.count(VALUES_MARKER) != 1
        or raw.count(RESOLVED_MARKER) != 1
    ):
        raise ValueError("reviewed single-pass core projection differs")
    return raw


def install(conn, root):
    conn.execute(source(root))
    from mfdataindia.enrichment import current_facts_store as current

    conn.execute(
        "CREATE OR REPLACE VIEW mf.current_fund_facts AS "
        + current.view_definition(conn, "mf_live_facts.resolved")
    )


def replay_values(conn, root, expected_definition):
    raw = source(root)
    conn.execute(VALUES_MARKER + raw.split(VALUES_MARKER, 1)[1].split(RESOLVED_MARKER, 1)[0])
    from mfdataindia.enrichment import current_facts_store as current

    if current.view_definition(conn, "mf_live_facts.current_values") != expected_definition:
        raise ValueError("single-pass current-values differs from captured seal")
