"""Read-only full prior-AMC projection proof, not just a handpicked canary."""

from psycopg import sql

from mfdataindia.enrichment import book_sources, current_facts_store as current
from mfdataindia.enrichment.current_graph_read import graph
from mfdataindia.review.policy import fingerprint


def sealed_core_read_relation(conn):
    """Use the already-granted resolved view, without granting the core facade.

    Current-facts installation grants this view to the restricted reader. Its
    complete row is equivalent to the active core facade only after both the
    exact query, default definer context and ordered columns are checked. No projection,
    privilege or source record is changed by certification.
    """
    current.verify_acl(conn)
    if not current.projection_state(conn):
        raise ValueError("active sealed core facade required for preservation proof")
    columns, contexts = {}, {}
    for relation in ("mf.current_fund_facts", "mf_live_facts.resolved"):
        contexts[relation] = conn.execute(
            "SELECT relowner,reloptions FROM pg_class WHERE oid=%s::regclass AND relkind='v'",
            (relation,),
        ).fetchone()
        columns[relation] = conn.execute(
            "SELECT attnum,attname,atttypid,atttypmod,attcollation "
            "FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 "
            "AND NOT attisdropped ORDER BY attnum",
            (relation,),
        ).fetchall()
    if (
        not contexts["mf.current_fund_facts"]
        or contexts["mf.current_fund_facts"] != contexts["mf_live_facts.resolved"]
        or contexts["mf.current_fund_facts"]["reloptions"]
    ):
        raise ValueError("resolved/core facade execution contexts differ or have unsealed options")
    if (
        not columns["mf.current_fund_facts"]
        or columns["mf.current_fund_facts"] != columns["mf_live_facts.resolved"]
    ):
        raise ValueError("resolved/core facade column signatures differ")
    return "mf_live_facts.resolved"


def capture(conn, previous_version):
    if type(previous_version) is not int or previous_version not in book_sources.REGISTRIES:
        raise ValueError("reviewed historical book authority required")
    core_read_relation = sealed_core_read_relation(conn)
    registry = book_sources.registry(previous_version)
    names = sorted({s["amc"] for s in registry["sources"]})
    protected = sorted({c for s in registry["sources"] for c in s["preserved_projection_codes"]})
    codes = [
        r["code"]
        for r in conn.execute(
            "SELECT f.amfi_scheme_code AS code FROM mf.funds f JOIN mf.amcs a USING(amc_id) WHERE a.amfi_amc_name=ANY(%s) OR f.amfi_scheme_code=ANY(%s) ORDER BY f.amfi_scheme_code",
            (names, protected),
        ).fetchall()
    ]
    if not 1 <= len(codes) <= 4096:
        raise ValueError("complete bounded historical AMC population required")
    relations = (
        ("mf_live_facts.field_versions", "field_versions", "code,field,as_of"),
        ("mf_live_facts.selected_fields", "selected_fields", "code,field"),
        ("mf_live_facts.current_values", "current_values", "code"),
        ("mf.current_fund_facts", "resolved", "amfi_scheme_code"),
    )
    sections = []
    for index, (relation, local, order) in enumerate(relations):
        condition = sql.SQL(" WHERE amfi_scheme_code=ANY(%s)") if local == "resolved" else sql.SQL("")
        sections.append(
            sql.SQL(
                "SELECT {} AS section,row_number() OVER (ORDER BY {}) AS ordinal,"
                "encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS sha256 "
                "FROM {} t{}"
            ).format(
                sql.Literal(index), sql.SQL(order), sql.Identifier(local), condition,
            )
        )
    # The paired core-facade validation above is still mandatory. Reuse the
    # exact sealed resolved definition, not an independently reconstructed row.
    assert core_read_relation == "mf_live_facts.resolved"
    all_rows = conn.execute(
        graph(conn, codes, complete=True)
        + sql.SQL(" UNION ALL ").join(sections)
        + sql.SQL(" ORDER BY section,ordinal"),
        (codes, codes),
    ).fetchall()
    hashes = {}
    for index, (relation, _, _) in enumerate(relations):
        rows = [{"sha256": row["sha256"]} for row in all_rows if row["section"] == index]
        hashes[relation] = {"rows": len(rows), "sha256": fingerprint(rows)}
    return {
        "format": "official-prior-amc-projection-v1",
        "previous_source_version": previous_version,
        "codes": codes,
        "hashes": hashes,
        "source_rows_changed": 0,
    }
