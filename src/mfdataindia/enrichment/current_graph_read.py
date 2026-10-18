"""Statement-local reuse of the sealed projection, without changing its views.

Only dependency names are substituted. Selection, conflicts, provenance and
fallbacks execute the installed view definitions, not a second Python policy.
"""

import re

from psycopg import sql

ORDER = ("field_versions", "field_heads", "selected_fields", "current_values", "resolved")
DEPENDENCIES = {
    "field_heads": ("field_versions",),
    "selected_fields": ("field_versions",),
    "current_values": ("selected_fields",),
    "resolved": ("current_values",),
}
# Do not interpret relation-looking text inside source literals or quoted names.
QUOTED = re.compile(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")")


def reuse(definition, dependencies):
    pieces = QUOTED.split(definition.rstrip().removesuffix(";"))
    for name in dependencies:
        pattern = re.compile(r"(?<![\w.])mf_live_facts\." + name + r"(?![\w.])")
        count = 0
        for index in range(0, len(pieces), 2):
            pieces[index], changed = pattern.subn(name, pieces[index])
            count += changed
        if not count:
            raise ValueError("sealed projection dependency is not reusable: " + name)
    return "".join(pieces)


def graph(conn, codes, *, complete=False):
    """Caller must first run current.verify_acl in the same snapshot/transaction.

    Every assertion row for every requested code is retained. MATERIALIZED is a
    statement-local read result, never a persistent cache, table or policy change.
    The definer views must have the same owner and default execution context;
    callers keep their existing privileges on every underlying relation.
    """
    from mfdataindia.enrichment import current_facts_store as current

    if (
        not isinstance(codes, list)
        or not 1 <= len(codes) <= 4096
        or any(type(code) is not int or code <= 0 for code in codes)
        or len(codes) != len(set(codes))
    ):
        raise ValueError("complete bounded distinct scheme codes required")
    names = ORDER if complete else ORDER[:3]
    contexts = conn.execute(
        "SELECT relname,relowner,reloptions FROM pg_class "
        "WHERE relnamespace='mf_live_facts'::regnamespace AND relkind='v' "
        "AND relname=ANY(%s)",
        (list(names),),
    ).fetchall()
    if (
        {r["relname"] for r in contexts} != set(names)
        or len({r["relowner"] for r in contexts}) != 1
        or any(r["reloptions"] for r in contexts)
    ):
        raise ValueError("sealed projection read execution contexts differ")
    definitions = current.configuration(conn)["view_definitions"]
    ctes = [
        sql.SQL("field_versions AS MATERIALIZED ("
                "SELECT * FROM mf_live_facts.field_versions WHERE code=ANY(%s))")
    ]
    for name in names[1:]:
        body = reuse(definitions[name], DEPENDENCIES[name])
        ctes.append(sql.SQL("{} AS MATERIALIZED ({})").format(sql.Identifier(name), sql.SQL(body)))
    return sql.SQL("WITH ") + sql.SQL(",").join(ctes) + sql.SQL(" ")


def fields(conn, code):
    return conn.execute(
        graph(conn, [code])
        + sql.SQL(
            "SELECT h.field,h.as_of,h.distinct_values,h.usable,h.sources AS latest_sources,"
            "s.as_of AS selected_as_of,s.value,s.sources FROM field_heads h "
            "LEFT JOIN selected_fields s USING(code,field) ORDER BY h.field"
        ),
        ([code],),
    ).fetchall()
