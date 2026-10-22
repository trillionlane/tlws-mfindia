"""Pure preflight rules and disposable CI-only view semantics; not DEV evidence."""

import ast
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from mfdataindia.review import core_access_metadata_rules as rules
from test_verified_review_store import ROLES, connection, source_state
from test_verified_review_store import verified_db as verified_db


def columns():
    return [{"attnum": 1, "name": "amfi_scheme_code", "dropped": False},
            {"attnum": 2, "name": "retained_drop", "dropped": True},
            {"attnum": 3, "name": "group_key", "dropped": False},
            {"attnum": 4, "name": "future_visible_field", "dropped": False}]


def test_all_current_view_columns_not_static_fields_and_exact_variant_columns():
    value = columns()
    before = deepcopy(value)
    assert rules.visible_columns(value, 4) == ("amfi_scheme_code", "group_key", "future_visible_field")
    assert rules.required_root_columns("mf.current_fund_facts", value, 4) == rules.visible_columns(value, 4)
    assert rules.required_root_columns("mf.fund_variants", value, 4) == ("amfi_scheme_code", "group_key")
    assert value == before


@pytest.mark.parametrize("defect", ["tail", "hole", "duplicate", "order", "bool_attnum", "bool_count", "bool_drop", "empty_name", "extra", "unknown_root", "missing_predicate", "missing_join"])
def test_incomplete_or_ambiguous_root_column_metadata_rejected(defect):
    value, count, root = columns(), 4, "mf.fund_variants"
    if defect == "tail":
        value.pop()
    elif defect == "hole":
        del value[1]
    elif defect == "duplicate":
        value[2]["name"] = value[0]["name"]
    elif defect == "order":
        value.reverse()
    elif defect == "bool_attnum":
        value[0]["attnum"] = True
    elif defect == "bool_count":
        count = True
    elif defect == "bool_drop":
        value[0]["dropped"] = 0
    elif defect == "empty_name":
        value[0]["name"] = ""
    elif defect == "extra":
        value[0]["value"] = "not metadata"
    elif defect == "unknown_root":
        root = "mf.other"
    elif defect == "missing_predicate":
        value[0]["dropped"] = True
    else:
        value[2]["dropped"] = True
    with pytest.raises(ValueError):
        rules.required_root_columns(root, value, count)


@pytest.mark.parametrize("outer_invoker,inner_invoker", [(False, False), (False, True), (True, False), (True, True)])
def test_nested_boundaries_reset_invoker_to_original_user(outer_invoker, inner_invoker):
    user, outer_owner, inner_owner = 10, 20, 30
    outer = rules.underlying_relation_principal(root_query_user=user, view_owner=outer_owner, security_invoker=outer_invoker)
    inner = rules.underlying_relation_principal(root_query_user=user, view_owner=inner_owner, security_invoker=inner_invoker)
    assert outer == (user if outer_invoker else outer_owner)
    assert inner == (user if inner_invoker else inner_owner)
    if not outer_invoker and inner_invoker:
        assert inner != outer  # Critical: never carry default outer owner's identity into an invoker view.


@pytest.mark.parametrize("field,value", [("root_query_user", 0), ("root_query_user", True), ("view_owner", -1),
                                         ("view_owner", 2**32), ("view_owner", "2"), ("security_invoker", 1),
                                         ("security_invoker", None)])
def test_principal_or_security_mode_ambiguity_rejected(field, value):
    args = {"root_query_user": 1, "view_owner": 2, "security_invoker": False}
    args[field] = value
    with pytest.raises(ValueError):
        rules.underlying_relation_principal(**args)


def selection(**changes):
    args = {"columns": columns(), "attribute_count": 4, "table_select": False,
            "column_select": {"amfi_scheme_code": True, "group_key": True, "future_visible_field": False},
            "mode": "explicit", "required": ("group_key", "amfi_scheme_code")}
    args.update(changes)
    return rules.select_requirement(**args)


def test_table_column_whole_row_any_and_unknown_requirements_remain_distinct():
    assert selection()["metadata_select_allowed"] is True
    assert selection()["required_columns"] == ["amfi_scheme_code", "group_key"]
    assert selection(mode="whole_row", required=())["metadata_select_allowed"] is False
    assert selection(mode="any_column", required=())["metadata_select_allowed"] is True
    assert selection(mode="whole_row", required=(), table_select=True)["metadata_select_allowed"] is True
    assert selection(required=("future_visible_field",))["metadata_select_allowed"] is False
    for mode in ("unknown", "relation_subid_0", 0, None):
        with pytest.raises(ValueError):
            selection(mode=mode, required=())


@pytest.mark.parametrize("change", [
    {"required": ()}, {"required": ("group_key", "group_key")}, {"required": ("retained_drop",)},
    {"required": (0,)}, {"table_select": 1},
    {"column_select": {"amfi_scheme_code": True}},
    {"column_select": {"amfi_scheme_code": True, "group_key": True, "future_visible_field": 0}},
    {"mode": "any_column"}, {"mode": "whole_row"},
])
def test_select_requirement_rejects_partial_flags_and_invented_columns(change):
    with pytest.raises(ValueError):
        selection(**change)


@pytest.mark.parametrize("definer,allowed", [(False, False), (False, True), (True, False), (True, True)])
def test_function_execute_check_stays_query_user_not_view_or_function_owner(definer, allowed):
    result = rules.function_context(root_query_user=10, function_owner=30, security_definer=definer, caller_execute=allowed)
    assert result["execute_check_principal"] == 10
    assert result["caller_execute"] is allowed
    assert result["function_body_principal"] == (30 if definer else 10)
    assert result["function_body_safety_established"] is False
    assert result["permission_change_authorized"] is False


def rls(**changes):
    args = {"principal": 10, "relation_owner": 20, "owner_usage": False, "superuser": False, "bypass_rls": False,
            "row_security": True, "force_row_security": False,
            "policies": [{"oid": 100, "command": "r", "roles": [30], "permissive": True}],
            "role_usage": {30: True}}
    args.update(changes)
    return rules.rls_context(**args)


@pytest.mark.parametrize("change,reason,enforced", [
    ({}, None, True), ({"principal": 20, "owner_usage": True}, "owner_without_force", False),
    ({"principal": 20, "owner_usage": True, "force_row_security": True}, None, True),
    ({"owner_usage": True}, "owner_without_force", False),
    ({"owner_usage": True, "force_row_security": True}, None, True),
    ({"superuser": True, "owner_usage": True, "force_row_security": True}, "superuser", False),
    ({"bypass_rls": True, "force_row_security": True}, "bypass_rls", False),
    ({"row_security": False, "force_row_security": True}, "row_security_disabled", False),
])
def test_owner_force_and_elevated_rls_context_is_not_row_visibility(change, reason, enforced):
    result = rls(**change)
    assert result["bypass_reason"] == reason
    assert result["rls_enforced"] is enforced
    assert result["returned_rows_established"] is False
    assert result["policy_expression_safety_established"] is False


def test_public_inherited_nonmember_restrictive_and_nonselect_policy_roles():
    assert rls()["applicable_SELECT_policy_oids"] == [100]
    assert rls(role_usage={30: False})["no_permissive_SELECT_policy"] is True
    policies = [{"oid": 101, "command": "*", "roles": [0], "permissive": True},
                {"oid": 102, "command": "w", "roles": [30], "permissive": True}]
    assert rls(policies=policies, role_usage={30: False})["applicable_SELECT_policy_oids"] == [101]
    policies[0]["permissive"] = False
    assert rls(policies=policies, role_usage={30: False})["no_permissive_SELECT_policy"] is True
    assert rls(policies=[], role_usage={})["no_permissive_SELECT_policy"] is True


@pytest.mark.parametrize("change", [{"role_usage": {}}, {"role_usage": {30: True, 40: True}},
                                     {"role_usage": {30: 1}}, {"superuser": 0},
                                     {"owner_usage": None}, {"principal": 20}, {"superuser": True},
                                     {"policies": [{"oid": 100, "command": "r", "roles": [], "permissive": True}]},
                                     {"policies": [{"oid": 100, "command": "r", "roles": [30, 30], "permissive": True}]},
                                     {"policies": [{"oid": 100, "command": "unknown", "roles": [30], "permissive": True}]}])
def test_missing_or_ambiguous_policy_and_membership_metadata_blocks_conclusions(change):
    with pytest.raises(ValueError):
        rls(**change)


def test_inputs_preserved_outputs_never_operational_admission_and_no_io_imports():
    metadata = {"principal": 10, "relation_owner": 20, "owner_usage": False, "superuser": False, "bypass_rls": False,
                "row_security": True, "force_row_security": False,
                "policies": [{"oid": 100, "command": "r", "roles": [0], "permissive": True}], "role_usage": {}}
    before = deepcopy(metadata)
    result = rules.rls_context(**metadata)
    assert metadata == before and result == rules.rls_context(**metadata)
    for output in (result, selection(), rules.function_context(root_query_user=1, function_owner=2, security_definer=False, caller_execute=True)):
        assert all(output[key] is False for key in ("metadata_authenticity_established", "permission_change_authorized",
                                                    "runtime_execution_performed", "operational_acceptance"))
    tree = ast.parse(Path(rules.__file__).read_text())
    assert not any(isinstance(n, ast.Import) for n in ast.walk(tree))
    assert [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] == ["mfdataindia.review.policy"]


@pytest.mark.postgres
@pytest.mark.parametrize("outer_invoker,inner_invoker", [(False, False), (False, True), (True, False), (True, True)])
def test_native_postgresql18_nested_view_principals_and_missing_select(verified_db, outer_invoker, inner_invoker):
    """CI fixture only, unique schema and owners; never existing DEV resources."""
    tag = uuid4().hex[:12]
    namespace, outer_owner, inner_owner = "meta_" + tag, "meta_outer_" + tag, "meta_inner_" + tag
    def qualified(name):
        return sql.SQL("{}.{}").format(sql.Identifier(namespace), sql.Identifier(name))
    with psycopg.connect(verified_db, row_factory=dict_row) as admin:
        before = source_state(admin)
        for role in (outer_owner, inner_owner):
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        try:
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(namespace)))
            for role in (outer_owner, inner_owner):
                admin.execute(sql.SQL("GRANT USAGE,CREATE ON SCHEMA {} TO {}").format(sql.Identifier(namespace), sql.Identifier(role)))
            admin.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(namespace), sql.Identifier(ROLES[1])))
            admin.execute(sql.SQL("CREATE TABLE {} (value integer NOT NULL)").format(qualified("base")))
            admin.execute(sql.SQL("INSERT INTO {} VALUES(7)").format(qualified("base")))
            for name, target, owner, invoker in (("inner_view", "base", inner_owner, inner_invoker),
                                                  ("outer_view", "inner_view", outer_owner, outer_invoker)):
                admin.execute(sql.SQL("CREATE VIEW {} WITH(security_invoker={}) AS SELECT value FROM {}").format(
                    qualified(name), sql.Literal(invoker), qualified(target)))
                admin.execute(sql.SQL("ALTER VIEW {} OWNER TO {}").format(qualified(name), sql.Identifier(owner)))
            principals = {r["rolname"]: r["oid"] for r in admin.execute(
                "SELECT oid,rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)", ([ROLES[1], outer_owner, inner_owner],)).fetchall()}
            reverse = {v: k for k, v in principals.items()}
            inner_check = rules.underlying_relation_principal(root_query_user=principals[ROLES[1]], view_owner=principals[outer_owner], security_invoker=outer_invoker)
            base_check = rules.underlying_relation_principal(root_query_user=principals[ROLES[1]], view_owner=principals[inner_owner], security_invoker=inner_invoker)
            for target, grantee in (("outer_view", ROLES[1]), ("inner_view", reverse[inner_check])):
                admin.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(qualified(target), sql.Identifier(grantee)))
            admin.execute(sql.SQL("GRANT SELECT(value) ON {} TO {}").format(qualified("base"), sql.Identifier(reverse[base_check])))
            admin.commit()
            with connection(verified_db, ROLES[1], readonly=True) as reader:
                assert reader.execute(sql.SQL("SELECT value FROM {}").format(qualified("outer_view"))).fetchall() == [{"value": 7}]
            admin.execute(sql.SQL("REVOKE SELECT(value) ON {} FROM {}").format(qualified("base"), sql.Identifier(reverse[base_check])))
            admin.commit()
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection(verified_db, ROLES[1], readonly=True) as reader:
                reader.execute(sql.SQL("SELECT value FROM {}").format(qualified("outer_view"))).fetchall()
            assert source_state(admin) == before
        finally:
            admin.rollback()
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(namespace)))
            for role in (inner_owner, outer_owner):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@pytest.mark.postgres
@pytest.mark.parametrize("force", [False, True])
def test_native_postgresql18_inherited_owner_rls_and_force(verified_db, force):
    tag = uuid4().hex[:12]
    namespace, owner, member = "rls_" + tag, "rls_owner_" + tag, "rls_member_" + tag
    table = sql.SQL("{}.sample").format(sql.Identifier(namespace))
    with psycopg.connect(verified_db, row_factory=dict_row) as admin:
        before = source_state(admin)
        for role in (owner, member):
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        try:
            admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(owner), sql.Identifier(member)))
            admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(namespace)))
            for role in (owner, member):
                admin.execute(sql.SQL("GRANT USAGE,CREATE ON SCHEMA {} TO {}").format(sql.Identifier(namespace), sql.Identifier(role)))
            admin.execute(sql.SQL("CREATE TABLE {} (value integer)").format(table))
            admin.execute(sql.SQL("INSERT INTO {} VALUES(7)").format(table))
            admin.execute(sql.SQL("ALTER TABLE {} OWNER TO {}").format(table, sql.Identifier(owner)))
            admin.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(table))
            if force:
                admin.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(table))
            admin.execute(sql.SQL("CREATE POLICY deny_rows ON {} FOR SELECT TO PUBLIC USING(false)").format(table))
            principals = {r["rolname"]: r["oid"] for r in admin.execute(
                "SELECT oid,rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)", ([owner, member],)).fetchall()}
            ownership = admin.execute("SELECT pg_catalog.pg_has_role(%s::oid,%s::oid,'USAGE') AS allowed",
                                      (principals[member], principals[owner])).fetchone()["allowed"]
            assert ownership is True and principals[member] != principals[owner]
            policy_oid = admin.execute("SELECT oid FROM pg_catalog.pg_policy WHERE polrelid=%s::regclass AND polname='deny_rows'",
                                       (namespace + '.sample',)).fetchone()['oid']
            result = rules.rls_context(principal=principals[member], relation_owner=principals[owner], owner_usage=ownership,
                                       superuser=False, bypass_rls=False, row_security=True, force_row_security=force,
                                       policies=[{"oid": policy_oid, "command": "r", "roles": [0], "permissive": True}], role_usage={})
            assert result["rls_enforced"] is force and result["returned_rows_established"] is False
            admin.commit()
            with connection(verified_db, member, readonly=True) as reader:
                assert reader.execute(sql.SQL("SELECT value FROM {}").format(table)).fetchall() == ([] if force else [{"value": 7}])
            assert source_state(admin) == before
        finally:
            admin.rollback()
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(namespace)))
            for role in (member, owner):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
