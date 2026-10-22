"""Fixed collector protocol and disposable-CI catalogue proofs; never DEV."""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from mfdataindia.review import core_access_metadata_inventory as frozen
from mfdataindia.review import core_access_metadata_spec as spec

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts/collect_core_access_metadata_dev.py"
loader = importlib.util.spec_from_file_location("metadata_collector", WORKER)
worker = importlib.util.module_from_spec(loader)
loader.loader.exec_module(worker)
DEFINITIONS = frozen.inventory()["statements"]


def fixture():
    context = {"database_name": "mfdataindia", "session_user": "mfdata_verified_reader",
               "current_user": "mfdata_verified_reader", "reader_oid": 11,
               **{k: False for k in ("superuser", "create_role", "create_db", "replication", "bypass_rls")},
               "parent_membership_count": 0, "server_version_num": 180006, "isolation": "repeatable read",
               "read_only": "on", "statement_timeout": "5s", "lock_timeout": "5s", "transaction_timeout": "15s",
               "search_path": "pg_catalog", "row_security": "on", "timezone": "UTC", "snapshot": "100:101:"}
    groups = {d["id"]: [] for d in DEFINITIONS}
    groups["M01_targets"] = [dict(ord=i, nspname="mf", relname=name, expected_kind=kind, nspoid=10, reloid=oid)
                             for i, name, kind, oid in ((1, "current_fund_facts", "v", 20), (2, "fund_variants", "r", 21))]
    groups["M02_relation_graph"] = [
        dict(relation_oid=oid, namespace_oid=10, namespace_name="mf", relation_name=name, relation_kind=kind,
             owner_oid=12, attribute_count=count, row_security=False, force_row_security=False, relation_options=None,
             private_view_definition="PRIVATE_VIEW_SQL" if kind == "v" else None)
        for oid, name, kind, count in ((20, "current_fund_facts", "v", 1), (21, "fund_variants", "r", 3))]
    groups["M03_physical_columns"] = [
        dict(relation_oid=oid, attnum=n, name=name, dropped=dropped, type_oid=0 if dropped else 23,
             type_modifier=-1, collation_oid=0, not_null=False)
        for oid, n, name, dropped in ((20, 1, "amfi_scheme_code", False), (21, 1, "amfi_scheme_code", False),
                                      (21, 2, "group_key", False), (21, 3, "........pg.dropped.3........", True))]
    groups["M04_root_and_mf_ACLs"] = sorted([
        dict(object_kind=kind, object_oid=oid, sub_id=sub, owner_oid=12, raw_acl=None, raw_acl_is_null=True,
             normalized_acl=[]) for kind, oid, sub in
        (("relation", 20, 0), ("relation", 21, 0), ("schema", 10, 0),
         ("column", 20, 1), ("column", 21, 1), ("column", 21, 2), ("column", 21, 3))
    ], key=lambda r: (r["object_kind"], r["object_oid"], r["sub_id"]))
    groups["M05_declared_edges"] = [
        dict(source_relation=20, source_kind="view_return", classid=spec.PG_REWRITE, objid=30, objsubid=0,
             refclassid=spec.PG_CLASS, refobjid=oid, refobjsubid=sub, deptype=dep)
        for oid, sub, dep in ((20, 0, "i"), (21, 1, "n"))]
    groups["M06_relevant_role_flags"] = [
        dict(role_oid=oid, name=name, superuser=False, inherit=True, create_role=False, create_db=False,
             can_login=True, replication=False, bypass_rls=False, is_relation_access_principal=True,
             is_function_body_principal=False, is_policy_role_target=False, is_original_query_user=oid == 11)
        for oid, name in ((11, "mfdata_verified_reader"), (12, "owner"))]
    caps = []
    # Explicit expected pairs: roots as reader, inner base as default view owner.
    for rel, principal, live in ((20, 11, [1]), (21, 11, [1, 2]), (21, 12, [1, 2])):
        for kind, obj, sub in [("relation", rel, 0), ("schema", 10, 0)] + [("column", rel, n) for n in live]:
            for privilege in spec.BASE_PRIVILEGES[kind]:
                for suffix in ("", " WITH GRANT OPTION"):
                    caps.append(dict(object_kind=kind, principal_oid=principal, object_oid=obj, sub_id=sub,
                                     privilege=privilege + suffix, allowed=False))
        caps.append(dict(object_kind="role_usage", principal_oid=principal, object_oid=12, sub_id=0,
                         privilege="USAGE", allowed=principal == 12))
    groups["M08_effective_capabilities"] = sorted(
        {tuple(r.values()): r for r in caps}.values(),
        key=lambda r: tuple(r[k] for k in DEFINITIONS[7]["unique_order_key"]))
    return context, groups


class Cursor:
    def __init__(self, rows, columns, status=None):
        self.rows = deepcopy(rows)
        self.description = [SimpleNamespace(name=k) for k in columns]
        self.statusmessage = status or "SELECT " + str(len(rows))
        self.rowcount = len(rows)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def fetchall(self):
        return self.rows


class Connection:
    autocommit = True

    def __init__(self, *, transform=None, fail=None):
        self.context, self.groups = fixture()
        self.calls, self.selects = [], 0
        self.transform, self.fail = transform, fail

    def execute(self, sql, *, prepare):
        assert prepare is False
        self.calls.append(sql)
        if sql == self.fail:
            raise psycopg.errors.InsufficientPrivilege("PRIVATE_SQL_DSN_SERVER_MESSAGE")
        doc = frozen.inventory()
        if sql in doc["transaction"]["control_statements"] or sql == "ROLLBACK":
            return Cursor([], [], "BEGIN" if sql.startswith("BEGIN") else "ROLLBACK" if sql == "ROLLBACK" else "SET")
        self.selects += 1
        definition = next(d for d in [doc["context"], *doc["statements"]] if d["sql"] == sql)
        rows = [self.context] if definition["id"] == "C00" else self.groups[definition["id"]]
        result = Cursor(rows, definition["columns"])
        if self.transform:
            self.transform(result, definition, self.selects)
        return result


def result_fixture():
    return {**worker.collect(Connection()), "worker_binding": {}}


def test_exact_frozen_inventory_sha_and_no_mutable_inventory_or_sql_options():
    doc = frozen.inventory()
    raw = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode()
    assert hashlib.sha256(raw).hexdigest() == spec.INVENTORY_SHA256
    assert len(doc["statements"]) == 10
    assert all(d["bindings"] == [] for d in [doc["context"], *doc["statements"]])
    doc["statements"][0]["sql"] = "SELECT application_data"
    assert frozen.inventory()["statements"][0]["sql"] != doc["statements"][0]["sql"]


def test_29_exact_statements_full_counts_private_hashes_and_no_acceptance():
    conn = Connection()
    value = worker.collect(conn)
    spec.validate_result({**value, "worker_binding": {}})
    doc = frozen.inventory()
    definitions = {d["id"]: d["sql"] for d in [doc["context"], *doc["statements"]]}
    assert conn.calls == [*doc["transaction"]["control_statements"],
                          *[definitions[k] for k in doc["transaction"]["success_query_sequence"]], "ROLLBACK"]
    assert len(conn.calls) == 29 and len(value["completions"]) == 22
    assert "PRIVATE_VIEW_SQL" not in json.dumps(value)
    private = value["groups"]["M02_relation_graph"][0]["private_view_definition"]
    assert private == spec.seal_private("PRIVATE_VIEW_SQL")
    assert value["permission_authority"] is value["runtime_authority"] is value["rollout_acceptance"] is False
    assert len(value["groups"]["M03_physical_columns"]) == 4  # Dropped tail retained.


@pytest.mark.parametrize("name", ["amfi_scheme_code", "group_key"])
@pytest.mark.parametrize("defect", ["missing", "renamed", "dropped"])
def test_required_variant_access_field_cannot_be_missing_renamed_or_dropped(name, defect):
    conn = Connection()
    cols = conn.groups["M03_physical_columns"]
    col = next(c for c in cols if c["relation_oid"] == 21 and c["name"] == name)
    num = col["attnum"]
    if defect == "renamed":
        col["name"] = "renamed_" + name
    else:
        caps = conn.groups["M08_effective_capabilities"]
        caps[:] = [c for c in caps if not (c["object_kind"] == "column" and c["object_oid"] == 21 and c["sub_id"] == num)]
        if defect == "dropped":
            col.update(name="........pg.dropped........", dropped=True, type_oid=0)
        else:
            cols.remove(col)
            conn.groups["M02_relation_graph"][1]["attribute_count"] -= 1
            for other in cols:
                if other["relation_oid"] == 21 and other["attnum"] > num:
                    other["attnum"] -= 1
            acls = conn.groups["M04_root_and_mf_ACLs"]
            acls[:] = [a for a in acls if not (a["object_kind"] == "column" and a["object_oid"] == 21 and a["sub_id"] == num)]
            for row in [*acls, *caps]:
                if row["object_kind"] == "column" and row["object_oid"] == 21 and row["sub_id"] > num:
                    row["sub_id"] -= 1
    with pytest.raises(worker.ProbeFailure):
        worker.collect(conn)
    assert conn.calls.count("ROLLBACK") == 1


def test_additional_variant_live_columns_and_dropped_slots_are_retained_not_rejected():
    conn = Connection()
    conn.groups["M02_relation_graph"][1]["attribute_count"] = 4
    conn.groups["M03_physical_columns"].append(dict(relation_oid=21, attnum=4, name="additional_live_field",
        dropped=False, type_oid=25, type_modifier=-1, collation_oid=0, not_null=False))
    conn.groups["M04_root_and_mf_ACLs"].append(dict(object_kind="column", object_oid=21, sub_id=4,
        owner_oid=12, raw_acl=None, raw_acl_is_null=True, normalized_acl=[]))
    caps = conn.groups["M08_effective_capabilities"]
    caps.extend([{**c, "sub_id": 4} for c in caps if c["object_kind"] == "column" and c["object_oid"] == 21 and c["sub_id"] == 2])
    for definition in DEFINITIONS:
        conn.groups[definition["id"]].sort(key=lambda r: tuple(r[k] for k in definition["unique_order_key"]))
    value = worker.collect(conn)
    spec.validate_result({**value, "worker_binding": {}})
    columns = value["groups"]["M03_physical_columns"]
    assert any(c["name"] == "additional_live_field" for c in columns)
    assert any(c["dropped"] for c in columns)


@pytest.mark.parametrize("field,value", [("current_user", "postgres"), ("database_name", "wrong"),
    ("parent_membership_count", True), ("parent_membership_count", 1), ("reader_oid", True),
    ("statement_timeout", "30s"), ("row_security", "off"), ("server_version_num", 190000)]
    + [(k, True) for k in ("superuser", "create_role", "create_db", "replication", "bypass_rls")])
def test_context_failure_precedes_all_targets_and_rolls_back_once(field, value):
    conn = Connection()
    conn.context[field] = value
    with pytest.raises(worker.ProbeFailure):
        worker.collect(conn)
    assert conn.selects == 1 and conn.calls[-1] == "ROLLBACK" and conn.calls.count("ROLLBACK") == 1


@pytest.mark.parametrize("label", [d["id"] for d in DEFINITIONS])
def test_each_query_failure_redacts_stops_once_and_cleanup_without_retry(label):
    conn = Connection(fail=next(d["sql"] for d in DEFINITIONS if d["id"] == label))
    with pytest.raises(worker.ProbeFailure) as error:
        worker.collect(conn)
    assert str(error.value) == label + ":InsufficientPrivilege"
    assert "PRIVATE" not in str(error.value)
    assert conn.calls.count(conn.fail) == 1 and conn.calls.count("ROLLBACK") == 1


@pytest.mark.parametrize("tamper", ["command", "driver", "fetched", "columns", "unknown_key", "bool_oid", "missing_tail", "duplicate"])
def test_complete_driver_counts_shapes_types_order_and_columns_fail_closed(tamper):
    def transform(cursor, definition, _):
        if definition["id"] != "M03_physical_columns":
            return
        if tamper == "command":
            cursor.statusmessage = "SELECT 999"
        elif tamper == "driver":
            cursor.rowcount = True
        elif tamper == "fetched":
            cursor.rows.pop()
        elif tamper == "columns":
            cursor.description.reverse()
        else:
            if tamper == "unknown_key":
                cursor.rows[0]["extra"] = "private"
            elif tamper == "bool_oid":
                cursor.rows[0]["relation_oid"] = True
            elif tamper == "missing_tail":
                cursor.rows.pop()
            else:
                cursor.rows.append(cursor.rows[0])
            cursor.rowcount = len(cursor.rows)
            cursor.statusmessage = "SELECT " + str(len(cursor.rows))
    conn = Connection(transform=transform)
    with pytest.raises(worker.ProbeFailure):
        worker.collect(conn)
    assert conn.calls[-1] == "ROLLBACK" and conn.calls.count("ROLLBACK") == 1


@pytest.mark.parametrize("late", [False, True])
def test_before_after_mismatch_is_not_partial_acceptance(late):
    def transform(cursor, definition, selects):
        if late and selects == 22:
            cursor.rows[0]["snapshot"] = "101:102:"
        elif not late and selects == 12 and definition["id"] == "M01_targets":
            cursor.rows[0]["reloid"] = 999
    with pytest.raises(worker.ProbeFailure):
        worker.collect(Connection(transform=transform))


def test_app_wall_clock_deadline_has_one_cleanup_no_fallback_or_capraise():
    conn = Connection()
    ticks = iter([0, *([0] * 20), 16])
    with pytest.raises((TimeoutError, worker.ProbeFailure)):
        worker.collect(conn, clock=lambda: next(ticks, 16))
    assert conn.calls.count("ROLLBACK") == 1 and len(conn.calls) < 29


@pytest.mark.parametrize("value", [None, "", "private\N{SNOWMAN}\nSQL"])
def test_private_expression_null_empty_and_utf8_exact_hash(value):
    result = spec.seal_private(value)
    spec.validate_private(result)
    assert result["is_null"] is (value is None)
    assert result["utf8_bytes"] == (0 if value is None else len(value.encode()))
    if value is not None:
        assert result["sha256"] == hashlib.sha256(value.encode()).hexdigest()


@pytest.mark.parametrize("sentinels,kind,left,valid", [((0, 0), "b", 23, True), ((31, 23), "b", 23, True),
    ((31, 23), "l", 0, True), ((0, 23), "b", 23, False), ((31, 0), "b", 23, False),
    ((0, 0), "r", 23, False), ((0, 0), "b", 0, False), ((0, 0), "l", 23, False)])
def test_exact_operator_shell_sentinels_and_pg18_kinds(sentinels, kind, left, valid):
    metadata = dict(namespace_oid=10, name="##", kind=kind, owner_oid=12, function_oid=sentinels[0],
                    left_type_oid=left, right_type_oid=23, result_type_oid=sentinels[1])
    if valid:
        spec.validate_operator(metadata)
    else:
        with pytest.raises(ValueError):
            spec.validate_operator(metadata)


def binding_fixture():
    manifest = SimpleNamespace(rollout_id=spec.IDENTITY, cohort=[154117], canary_code=154117,
                               git_sha="a" * 40, image="SYNTHETIC_NO_RUNTIME_AUTHORITY")
    env = {"DEPLOYMENT_GIT_SHA": manifest.git_sha}
    for key, path in (("PROGRAM", WORKER), ("SPEC", Path(spec.__file__)), ("INVENTORY", Path(frozen.__file__))):
        env["CORE_ACCESS_METADATA_" + key + "_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest, env


@pytest.mark.parametrize("key", ["DEPLOYMENT_GIT_SHA", "CORE_ACCESS_METADATA_PROGRAM_SHA256",
    "CORE_ACCESS_METADATA_SPEC_SHA256", "CORE_ACCESS_METADATA_INVENTORY_SHA256"])
def test_bad_binding_never_reaches_dsn_or_connection(monkeypatch, key):
    manifest, env = binding_fixture()
    worker.worker_binding(manifest, env)
    env[key] = "0" * 64
    monkeypatch.setattr(worker.Manifest, "model_validate_json", lambda _: manifest)
    monkeypatch.setattr(worker, "pinned_dsn", lambda *_: pytest.fail("DSN reached"))
    monkeypatch.setattr(worker.sys, "argv", ["worker"])
    monkeypatch.setenv("REVIEW_MANIFEST", "{}")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(worker.ProbeFailure):
        worker.main()


@pytest.mark.parametrize("key", list(spec.SOURCE_HASHES))
def test_installed_package_no_src_and_each_attribution_tamper_stops(tmp_path, monkeypatch, key):
    app = tmp_path / "app"
    package = tmp_path / "site-packages/mfdataindia"
    locations = {}
    for name in spec.SOURCE_HASHES:
        path = package / name.removeprefix("src/mfdataindia/") if name.startswith("src/") else app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
        locations[name] = path
    new_worker = app / "scripts/collect_core_access_metadata_dev.py"
    new_worker.write_bytes(WORKER.read_bytes())
    for module, name in ((spec, "core_access_metadata_spec.py"), (frozen, "core_access_metadata_inventory.py")):
        path = package / "review" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Path(module.__file__).read_bytes())
        monkeypatch.setattr(module, "__file__", str(path))
    monkeypatch.setattr(worker, "__file__", str(new_worker))
    manifest, env = binding_fixture()
    env["CORE_ACCESS_METADATA_PROGRAM_SHA256"] = hashlib.sha256(new_worker.read_bytes()).hexdigest()
    assert not (app / "src").exists()
    worker.worker_binding(manifest, env)
    locations[key].write_bytes(b"unreviewed")
    with pytest.raises(ValueError):
        worker.worker_binding(manifest, env)


@pytest.fixture
def metadata_db(pg_dsn):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    # CI's disposable cluster only; collision is failure, never modification or skip.
    roles = ("mfdata_verified_reader", "aclmeta_owner", "aclmeta_inner", "aclmeta_policy", "aclmeta_child")
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        assert not admin.execute("SELECT 1 FROM pg_database WHERE datname='mfdataindia'").fetchone()
        assert not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (list(roles),)).fetchone()
        admin.execute("CREATE DATABASE mfdataindia")
        created = []
        try:
            for role in roles:
                admin.execute(psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'metadata-fixture-only'").format(psycopg.sql.Identifier(role)))
                created.append(role)
            dsn = make_conninfo(pg_dsn, dbname="mfdataindia")
            with psycopg.connect(dsn) as setup:
                setup.execute("CREATE SCHEMA mf AUTHORIZATION aclmeta_owner")
                setup.execute("GRANT USAGE ON SCHEMA mf TO mfdata_verified_reader,aclmeta_inner,aclmeta_child")
                setup.execute("CREATE TABLE mf.fund_variants(amfi_scheme_code integer,group_key text,old_column text)")
                setup.execute("INSERT INTO mf.fund_variants VALUES(154117,'FIXTURE','preserve')")
                setup.execute("ALTER TABLE mf.fund_variants DROP COLUMN old_column")
                setup.execute("ALTER TABLE mf.fund_variants OWNER TO aclmeta_inner")
                setup.execute("CREATE VIEW mf.inner_metadata_view AS SELECT amfi_scheme_code,group_key FROM mf.fund_variants")
                setup.execute("ALTER VIEW mf.inner_metadata_view OWNER TO aclmeta_inner")
                setup.execute("CREATE VIEW mf.current_fund_facts AS SELECT * FROM mf.inner_metadata_view")
                setup.execute("ALTER VIEW mf.current_fund_facts OWNER TO aclmeta_owner")
                setup.execute("GRANT SELECT(amfi_scheme_code) ON mf.fund_variants TO mfdata_verified_reader")
                setup.execute("GRANT SELECT ON mf.inner_metadata_view TO aclmeta_owner")
                setup.execute("GRANT mfdata_verified_reader TO aclmeta_child WITH INHERIT FALSE,SET TRUE")
            yield dsn
        finally:
            admin.execute("DROP DATABASE mfdataindia")
            for role in reversed(created):
                admin.execute(psycopg.sql.SQL("DROP ROLE {}").format(psycopg.sql.Identifier(role)))


def native_state(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as admin:
        return [admin.execute(sql).fetchall() for sql in (
            "SELECT to_jsonb(v)::text FROM mf.fund_variants v ORDER BY to_jsonb(v)::text",
            "SELECT c.oid,c.relowner,c.relacl::text,c.reloptions,c.relrowsecurity,c.relforcerowsecurity,CASE WHEN c.relkind='v' THEN pg_get_viewdef(c.oid,false) END AS body FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf' ORDER BY c.oid",
            "SELECT a.attrelid,a.attnum,a.attacl::text FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='mf' AND a.attnum>0 ORDER BY a.attrelid,a.attnum",
            "SELECT roleid,member,grantor,admin_option,inherit_option,set_option FROM pg_auth_members ORDER BY roleid,member,grantor",
            "SELECT oid,polrelid,polname,polcmd,polpermissive,polroles::text,polqual::text,polwithcheck::text FROM pg_policy ORDER BY oid" )]


@pytest.mark.postgres
@pytest.mark.parametrize("outer,inner", [(False, False), (False, True), (True, False), (True, True)])
def test_native_frozen_sql_complete_columns_acl_membership_nested_context_and_preservation(metadata_db, outer, inner):
    with psycopg.connect(metadata_db) as admin:
        admin.execute("ALTER VIEW mf.current_fund_facts SET(security_invoker=" + str(outer).lower() + ")")
        admin.execute("ALTER VIEW mf.inner_metadata_view SET(security_invoker=" + str(inner).lower() + ")")
    before = native_state(metadata_db)
    direct = make_conninfo(metadata_db, user="mfdata_verified_reader", password="metadata-fixture-only")
    with psycopg.connect(direct, row_factory=dict_row, autocommit=True, prepare_threshold=None) as conn:
        value = worker.collect(conn)
    spec.validate_result({**value, "worker_binding": {}})
    assert native_state(metadata_db) == before
    assert any(c["dropped"] and c["type_oid"] == 0 for c in value["groups"]["M03_physical_columns"])
    assert any(r["raw_acl_is_null"] for r in value["groups"]["M04_root_and_mf_ACLs"])
    assert any(r["object_kind"] == "column" and not r["raw_acl_is_null"] for r in value["groups"]["M04_root_and_mf_ACLs"])
    assert any(not m["inherit_option"] and m["set_option"] for m in value["groups"]["M07_membership_edges"])
    assert value["context"]["parent_membership_count"] == 0
    assert len(value["completions"]) == 22


@pytest.mark.postgres
@pytest.mark.parametrize("force,inherited", [(False, False), (True, False), (False, True), (True, True)])
def test_native_rls_policy_role_usage_inherited_owner_and_force_are_metadata_not_rows(metadata_db, force, inherited):
    with psycopg.connect(metadata_db) as admin:
        admin.execute("ALTER TABLE mf.fund_variants ENABLE ROW LEVEL SECURITY")
        if force:
            admin.execute("ALTER TABLE mf.fund_variants FORCE ROW LEVEL SECURITY")
        admin.execute("CREATE POLICY metadata_policy ON mf.fund_variants FOR SELECT TO aclmeta_policy USING(amfi_scheme_code>0)")
        if inherited:
            admin.execute("GRANT aclmeta_policy TO aclmeta_inner WITH INHERIT TRUE,SET FALSE")
    before = native_state(metadata_db)
    direct = make_conninfo(metadata_db, user="mfdata_verified_reader", password="metadata-fixture-only")
    with psycopg.connect(direct, row_factory=dict_row, autocommit=True, prepare_threshold=None) as conn:
        value = worker.collect(conn)
    spec.validate_result({**value, "worker_binding": {}})
    assert native_state(metadata_db) == before
    graph = value["groups"]["M02_relation_graph"]
    relation = next(r for r in graph if r["relation_name"] == "fund_variants")
    assert relation["row_security"] is True and relation["force_row_security"] is force
    policy = value["groups"]["M10_RLS_policies"][0]
    assert policy["private_qual"]["is_null"] is False
    assert policy["private_with_check"]["is_null"] is True
    usage = next(r for r in value["groups"]["M08_effective_capabilities"]
                 if r["object_kind"] == "role_usage" and r["principal_oid"] == relation["owner_oid"]
                 and r["object_oid"] == policy["role_oids"][0])
    assert usage["allowed"] is inherited
    assert "POLICY_EXPRESSION_SECURITY_UNMODELED" in value["access_admission_blockers"]


@pytest.mark.postgres
def test_native_function_execute_original_reader_only_body_identity_and_shell_operator_schema(metadata_db):
    with psycopg.connect(metadata_db) as admin:
        admin.execute("CREATE FUNCTION mf.metadata_fn(integer) RETURNS integer LANGUAGE sql SECURITY DEFINER AS 'SELECT $1'")
        admin.execute("ALTER FUNCTION mf.metadata_fn(integer) OWNER TO aclmeta_policy")
        admin.execute("CREATE OR REPLACE VIEW mf.inner_metadata_view AS SELECT amfi_scheme_code,mf.metadata_fn(amfi_scheme_code)::text AS group_key FROM mf.fund_variants")
        admin.execute("CREATE OPERATOR mf.=== (LEFTARG=integer,RIGHTARG=integer,FUNCTION=pg_catalog.int4eq,COMMUTATOR=OPERATOR(mf.===!))")
        shell = admin.execute("SELECT oprnamespace::bigint,oprname::text,oprkind::text,oprowner::bigint,oprcode::oid::bigint,oprleft::bigint,oprright::bigint,oprresult::bigint FROM pg_operator WHERE oprnamespace='mf'::regnamespace AND oprname='===!'").fetchone()
        metadata = dict(zip(spec.inventory()["nested_result_schemas"]["M09_operator"]["exact_keys"], shell, strict=True))
        spec.validate_operator(metadata)
        assert metadata["function_oid"] == metadata["result_type_oid"] == 0
    before = native_state(metadata_db)
    direct = make_conninfo(metadata_db, user="mfdata_verified_reader", password="metadata-fixture-only")
    with psycopg.connect(direct, row_factory=dict_row, autocommit=True, prepare_threshold=None) as conn:
        value = worker.collect(conn)
    spec.validate_result({**value, "worker_binding": {}})
    assert native_state(metadata_db) == before
    reader = value["context"]["reader_oid"]
    function = next(r["metadata"] for r in value["groups"]["M09_functions_operators"]
                    if r["object_kind"] == "function" and r["metadata"]["name"] == "metadata_fn")
    assert function["security_definer"] is True and len(function["execute"]) == 1
    assert function["execute"][0]["principal_oid"] == reader
    role = next(r for r in value["groups"]["M06_relevant_role_flags"] if r["role_oid"] == function["owner_oid"])
    assert role["is_function_body_principal"] is True and role["is_relation_access_principal"] is False
    assert "FUNCTION_BODY_OR_OPERATOR_SAFETY_UNMODELED" in value["access_admission_blockers"]


@pytest.mark.postgres
@pytest.mark.parametrize("name", ["amfi_scheme_code", "group_key"])
def test_native_renamed_required_variant_field_fails_without_second_capture_or_row_changes(metadata_db, name):
    with psycopg.connect(metadata_db) as admin:
        admin.execute(psycopg.sql.SQL("ALTER TABLE mf.fund_variants RENAME COLUMN {} TO {}").format(
            psycopg.sql.Identifier(name), psycopg.sql.Identifier("renamed_" + name)))
    before = native_state(metadata_db)
    direct = make_conninfo(metadata_db, user="mfdata_verified_reader", password="metadata-fixture-only")
    with psycopg.connect(direct, row_factory=dict_row, autocommit=True, prepare_threshold=None) as conn:
        with pytest.raises(worker.ProbeFailure) as failure:
            worker.collect(conn)
        assert failure.value.phase == "seal"
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    assert native_state(metadata_db) == before
