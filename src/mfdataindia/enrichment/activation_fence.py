"""Ephemeral MVCC dependency fence, not a source policy or persistent cache.

The prepared snapshot is optimistic. A short READ COMMITTED transaction must
lock and rediscover the complete graph before comparing these row versions.
Physical changes may reject a safe snapshot; they must never accept a stale one.
"""

from psycopg import sql

from mfdataindia.review.policy import fingerprint

ROOTS = (
    "mf.current_fund_facts", "mf_live_facts.baseline", "mf_live_facts.field_heads",
    "mf_live_facts.field_versions", "mf_live_facts.selected_fields",
    "mf_live_facts.current_values", "mf_live_facts.resolved", "mf_live_facts.configuration",
    "mf_book_facts.configuration", "mf_book_facts.sources", "mf_book_facts.batches",
    "mf_book_facts.runs", "mf_book_facts.transitions", "mf_review.policies",
    "mf.funds", "mf.amcs", "mf.dataset_summary",
)


def instance(conn):
    return conn.execute("""SELECT current_database() AS database,current_user AS principal,
        (SELECT oid::text FROM pg_database WHERE datname=current_database()) AS database_oid,
        inet_server_addr()::text AS address,inet_server_port() AS port,
        pg_postmaster_start_time()::text AS started""").fetchone()


def inventory(conn):
    rows = conn.execute("""WITH RECURSIVE dependencies(oid) AS (
        SELECT unnest(%s::regclass[])::oid
        UNION
        SELECT d.refobjid FROM dependencies p JOIN pg_rewrite w ON w.ev_class=p.oid
        JOIN pg_depend d ON d.classid='pg_rewrite'::regclass AND d.objid=w.oid
          AND d.refclassid='pg_class'::regclass AND d.deptype='n'
    ) SELECT c.oid::bigint AS oid,n.nspname AS schema,c.relname AS name,c.relkind AS kind,
        c.relowner::bigint AS owner,c.relacl::text AS acl,c.reloptions AS options,
        c.xmin::text AS catalog_revision,n.xmin::text AS schema_revision,
        n.nspowner::bigint AS schema_owner,n.nspacl::text AS schema_acl,
        c.relfilenode::text AS storage,c.relrowsecurity AS rls,c.relforcerowsecurity AS force_rls,
        c.relispartition AS partition,
        EXISTS(SELECT 1 FROM pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid) AS inherited,
        CASE WHEN c.relkind='v' THEN pg_get_viewdef(c.oid,true) END AS definition,
        (SELECT jsonb_agg(jsonb_build_array(a.attnum,a.attname,a.atttypid,a.atttypmod,
            a.attcollation,a.attnotnull,a.attgenerated,a.attidentity,a.attacl::text,a.xmin::text) ORDER BY a.attnum)
         FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns,
        (SELECT jsonb_agg(jsonb_build_array(w.oid::text,w.xmin::text) ORDER BY w.oid)
         FROM pg_rewrite w WHERE w.ev_class=c.oid) AS rewrite_revisions,
        EXISTS(SELECT 1 FROM pg_rewrite w JOIN pg_depend d
          ON d.classid='pg_rewrite'::regclass AND d.objid=w.oid
          AND d.refclassid='pg_proc'::regclass JOIN pg_proc f ON f.oid=d.refobjid
          WHERE w.ev_class=c.oid AND f.pronamespace<>'pg_catalog'::regnamespace) AS custom_function
      FROM dependencies p JOIN pg_class c ON c.oid=p.oid
      JOIN pg_namespace n ON n.oid=c.relnamespace ORDER BY c.oid""", (list(ROOTS),)).fetchall()
    if (not rows or len(rows) > 100
            or any(r["kind"] not in ("r", "v") or r["rls"] or r["force_rls"]
                   or r["partition"] or r["inherited"] or r["custom_function"]
                   or (r["kind"] == "v" and r["options"]) for r in rows)):
        raise ValueError("unsupported activation dependency graph/context")
    return rows


def row_versions(conn, relations):
    result = {}
    for row in relations:
        if row["kind"] != "r":
            continue
        # No JSON/TOAST expansion and no reliance on planner statistics, max IDs,
        # timestamps or global dataset_version. Include every visible tuple.
        result[str(row["oid"])] = conn.execute(sql.SQL("""SELECT count(*) AS rows,
            encode(sha256(convert_to(COALESCE(string_agg(
                ctid::text||':'||xmin::text,',' ORDER BY ctid),''),'UTF8')),'hex') AS sha256
            FROM ONLY {}.{}""").format(sql.Identifier(row["schema"]), sql.Identifier(row["name"]))).fetchone()
    return result


def capture(conn):
    relations = inventory(conn)
    return {"instance": instance(conn), "relations": relations,
            "row_versions": row_versions(conn, relations)}


def lock_and_verify(conn, expected):
    if instance(conn) != expected["instance"]:
        raise ValueError("activation database instance changed")
    # Lock by names, then compare OIDs and full catalog metadata. Rename/drop/
    # recreate between snapshot and acquisition cannot silently change the target.
    for kind, mode in (("v", "ACCESS SHARE"), ("r", "SHARE")):
        names = [sql.Identifier(r["schema"], r["name"])
                 for r in expected["relations"] if r["kind"] == kind]
        if names:
            conn.execute(sql.SQL("LOCK TABLE {} IN " + mode + " MODE NOWAIT").format(sql.SQL(",").join(names)))
    actual = capture(conn)
    if actual != expected:
        raise ValueError("activation inputs or dependency graph changed after preparation")
    return fingerprint(actual)
