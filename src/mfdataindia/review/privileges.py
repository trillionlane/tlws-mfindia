"""Fail closed on elevated identity or source DML, including column grants."""
def verify_artifact_acl(conn) -> None:
    namespace = conn.execute("SELECT nspowner,pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname='mf_review'").fetchone()
    if not namespace or namespace['owner'] not in ('mfdata_admin', 'postgres'):
        raise ValueError('unexpected review namespace owner')
    grants = conn.execute("""
        WITH objects AS (
          SELECT CASE WHEN c.relkind='S' THEN 'sequence' ELSE 'table' END AS kind,c.relname AS name,c.relowner AS owner,c.relacl AS acl
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_review'
          UNION ALL SELECT 'table',c.relname,c.relowner,a.attacl FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf_review' AND a.attnum>0 AND NOT a.attisdropped
          UNION ALL SELECT 'function',p.oid::regprocedure::text,p.proowner,COALESCE(p.proacl,acldefault('f'::"char",p.proowner))
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mf_review'
          UNION ALL SELECT 'schema',nspname,nspowner,nspacl FROM pg_namespace WHERE nspname='mf_review'
        ) SELECT kind,name,owner,COALESCE(r.rolname,'PUBLIC') AS grantee,x.privilege_type AS privilege
          FROM objects CROSS JOIN LATERAL aclexplode(acl) x LEFT JOIN pg_roles r ON r.oid=x.grantee
          WHERE x.grantee<>owner
    """).fetchall()
    for grant in grants:
        role, kind, name, permission = (grant[k] for k in ('grantee', 'kind', 'name', 'privilege'))
        allowed = set()
        if role in ('mfdata_verified_generator', 'mfdata_verified_reader', 'mfdata_verified_admin'):
            if kind == 'schema':
                allowed = {'USAGE'}
            elif kind == 'table' and name in ('policies', 'snapshots', 'results', 'events'):
                allowed = {'SELECT'} | ({'INSERT'} if role == 'mfdata_verified_generator' and name != 'events' else set())
            elif kind == 'function' and name == 'mf_review.append_event(integer,text,text,text,text,text,jsonb)' and role == 'mfdata_verified_admin':
                allowed = {'EXECUTE'}
        if grant['owner'] != namespace['nspowner'] or permission not in allowed:
            if kind == 'function' and name == 'mf_review.append_event(integer,text,text,text,text,text,jsonb)':
                raise ValueError('runtime principal has an unexpected review-execution capability')
            raise ValueError('review artifact grants escape the declared role/capability boundary')


def verify_runtime(conn, purpose: str) -> None:
    identity = conn.execute('SELECT current_user AS name,session_user=current_user AS dedicated').fetchone()
    if not identity['dedicated']:
        raise ValueError('require direct login as dedicated runtime principal, not SET ROLE from administrator')
    verify_principal(conn, purpose, identity['name'])


def verify_principal(conn, purpose: str, principal: str) -> None:
    """Inspect effective privileges before provisioning commits; no impersonation.

    Runtime still requires verify_runtime's actual dedicated-session check.
    """
    allowed = {"generator": {"mfdata_verified_generator"},
               "api": {"mfdata_verified_reader", "mfdata_verified_admin"}}
    verify_artifact_acl(conn)
    role = conn.execute("SELECT r.rolname AS name,r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolbypassrls "
                        "FROM pg_roles r WHERE rolname=%s", (principal,)).fetchone()
    if not role or role["name"] not in allowed[purpose] or any(role[k] for k in role if k != "name"):
        raise ValueError("unexpected or elevated verified-review runtime principal")
    if conn.execute("SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=%s", (principal,)).fetchone():
        raise ValueError("verified-review principal must not retain role memberships")
    rows = conn.execute("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='mf' AND c.relkind IN('r','p','v','m')").fetchall()
    for row in rows:
        if conn.execute("SELECT has_table_privilege(%s,%s::oid,'INSERT,UPDATE,DELETE,TRUNCATE') OR "
                        "has_any_column_privilege(%s,%s::oid,'INSERT,UPDATE') AS source_write",
                        (principal, row["oid"], principal, row["oid"])).fetchone()["source_write"]:
            raise ValueError("verified-review principal retains effective source-write privileges")
    if conn.execute("SELECT has_schema_privilege(%s,'mf','CREATE') OR "
                    "has_schema_privilege(%s,'mf_review','CREATE') OR has_schema_privilege(%s,'public','CREATE') OR "
                    "has_database_privilege(%s,current_database(),'CREATE') AS ddl", (principal, principal, principal, principal)).fetchone()["ddl"]:
        raise ValueError("verified-review principal retains schema DDL privileges")
    if conn.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='mf' AND CASE WHEN c.relkind='S' THEN has_sequence_privilege(%s,c.oid,'USAGE,UPDATE') ELSE false END LIMIT 1", (principal,)).fetchone():
        raise ValueError('verified-review principal can mutate source sequences')
    # No PUBLIC/grant access to owner-executed source functions.
    if conn.execute("SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='mf' AND p.prosecdef AND has_function_privilege(%s,p.oid,'EXECUTE') LIMIT 1", (principal,)).fetchone():
        raise ValueError("verified-review principal can execute privileged source functions")
    for table in ("policies", "snapshots", "results", "events"):
        permissions = conn.execute(
            "SELECT has_table_privilege(%s,%s,'UPDATE,DELETE,TRUNCATE') OR "
            "has_any_column_privilege(%s,%s,'UPDATE') AS rewrite, "
            "has_table_privilege(%s,%s,'INSERT') OR "
            "has_any_column_privilege(%s,%s,'INSERT') AS append",
            (principal, "mf_review."+table)*4).fetchone()
        if permissions["rewrite"] or (permissions["append"] and (purpose=="api" or table=="events")):
            raise ValueError("runtime principal retains unintended artifact-write privileges")
    can_review = conn.execute(
        "SELECT has_function_privilege(%s,'mf_review.append_event(integer,text,text,text,text,text,jsonb)','EXECUTE') AS allowed", (principal,)).fetchone()["allowed"]
    if can_review != (role["name"] == "mfdata_verified_admin"):
        raise ValueError("runtime principal has an unexpected review-execution capability")
