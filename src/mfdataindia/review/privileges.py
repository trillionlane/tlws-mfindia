"""Fail closed on elevated identity or source DML, including column grants."""
def verify_runtime(conn, purpose: str) -> None:
    allowed = {"generator": {"mfdata_verified_generator"},
               "api": {"mfdata_verified_reader", "mfdata_verified_admin"}}
    role = conn.execute("SELECT current_user AS name,r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolbypassrls "
                        "FROM pg_roles r WHERE rolname=current_user").fetchone()
    if role["name"] not in allowed[purpose] or any(role[k] for k in role if k != "name"):
        raise ValueError("unexpected or elevated verified-review runtime principal")
    if not conn.execute("SELECT session_user=current_user AS dedicated").fetchone()["dedicated"]:
        raise ValueError("require direct login as dedicated runtime principal, not SET ROLE from administrator")
    if conn.execute("SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=current_user").fetchone():
        raise ValueError("verified-review principal must not retain role memberships")
    rows = conn.execute("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='mf' AND c.relkind IN('r','p','v','m')").fetchall()
    for row in rows:
        if conn.execute("SELECT has_table_privilege(current_user,%s::oid,'INSERT,UPDATE,DELETE,TRUNCATE') OR "
                        "has_any_column_privilege(current_user,%s::oid,'INSERT,UPDATE') AS source_write",
                        (row["oid"], row["oid"])).fetchone()["source_write"]:
            raise ValueError("verified-review principal retains effective source-write privileges")
    if conn.execute("SELECT has_schema_privilege(current_user,'mf','CREATE') OR "
                    "has_schema_privilege(current_user,'mf_review','CREATE') AS ddl").fetchone()["ddl"]:
        raise ValueError("verified-review principal retains schema DDL privileges")
    # No PUBLIC/grant access to owner-executed source functions.
    if conn.execute("SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='mf' AND p.prosecdef AND has_function_privilege(current_user,p.oid,'EXECUTE') LIMIT 1").fetchone():
        raise ValueError("verified-review principal can execute privileged source functions")
    for table in ("policies", "snapshots", "results", "events"):
        permissions = conn.execute(
            "SELECT has_table_privilege(current_user,%s,'UPDATE,DELETE,TRUNCATE') OR "
            "has_any_column_privilege(current_user,%s,'UPDATE') AS rewrite, "
            "has_table_privilege(current_user,%s,'INSERT') OR "
            "has_any_column_privilege(current_user,%s,'INSERT') AS append",
            ("mf_review."+table,)*4).fetchone()
        if permissions["rewrite"] or (permissions["append"] and (purpose=="api" or table=="events")):
            raise ValueError("runtime principal retains unintended artifact-write privileges")
    can_review = conn.execute(
        "SELECT has_function_privilege(current_user,'mf_review.append_event(integer,text,text,text,text,text,jsonb)','EXECUTE') AS allowed").fetchone()["allowed"]
    if can_review != (role["name"] == "mfdata_verified_admin"):
        raise ValueError("runtime principal has an unexpected review-execution capability")
