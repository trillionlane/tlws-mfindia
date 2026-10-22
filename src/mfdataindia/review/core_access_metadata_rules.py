"""Dormant pure metadata rules, not a collector, ACL profile or runtime admission.

Inputs are caller-supplied metadata, never authenticated evidence. No SQL,
connection, source, grant, controller or application-query execution occurs.
The complete fixed collector and its independently sealed evidence are separate.
"""

from mfdataindia.review.policy import fingerprint

ROOTS = ("mf.current_fund_facts", "mf.fund_variants")
VARIANT_COLUMNS = ("amfi_scheme_code", "group_key")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _oid(value):
    _require(type(value) is int and 0 < value <= 4294967295, "exact positive OID required")
    return value


def _boolean(value):
    _require(type(value) is bool, "exact metadata boolean required")
    return value


def _boundary():
    return {"metadata_authenticity_established": False, "permission_change_authorized": False,
            "runtime_execution_performed": False, "operational_acceptance": False}


def visible_columns(columns, attribute_count):
    """Require every physical attnum, including dropped slots; omit no tail."""
    _require(type(attribute_count) is int and attribute_count >= 0, "exact attribute count required")
    _require(isinstance(columns, list) and len(columns) == attribute_count, "complete column metadata required")
    names, live = set(), []
    for number, column in enumerate(columns, 1):
        _require(isinstance(column, dict) and set(column) == {"attnum", "name", "dropped"}, "exact column metadata required")
        _require(type(column["attnum"]) is int and column["attnum"] == number, "complete physical column order required")
        name = column["name"]
        _require(isinstance(name, str) and bool(name) and name not in names, "unique nonempty column name required")
        names.add(name)
        if not _boolean(column["dropped"]):
            live.append(name)
    return tuple(live)


def required_root_columns(relation, columns, attribute_count):
    _require(relation in ROOTS, "only the two fixed original-query roots are modeled")
    live = visible_columns(columns, attribute_count)
    _require("amfi_scheme_code" in live, "original predicate column required")
    if relation == ROOTS[0]:
        return live  # Frozen original batch statement uses SELECT *, not 44 assumed fields.
    _require(set(VARIANT_COLUMNS) <= set(live), "both exact variant join columns required")
    return VARIANT_COLUMNS


def underlying_relation_principal(*, root_query_user, view_owner, security_invoker):
    """An invoker boundary resets to ORIGINAL query user, not outer view owner."""
    user, owner = _oid(root_query_user), _oid(view_owner)
    return user if _boolean(security_invoker) else owner


def select_requirement(*, columns, attribute_count, table_select, column_select,
                       mode, required=()):
    """Model explicit/whole-row/any-column requirements, never infer sub-ID0."""
    live = visible_columns(columns, attribute_count)
    table = _boolean(table_select)
    _require(isinstance(column_select, dict) and set(column_select) == set(live), "each visible column privilege required")
    for value in column_select.values():
        _boolean(value)
    _require(isinstance(required, (list, tuple)) and all(isinstance(v, str) for v in required), "explicit column names required")
    _require(len(required) == len(set(required)), "duplicate required column")
    if mode == "explicit":
        _require(bool(required) and set(required) <= set(live), "complete referenced live columns required")
        wanted = tuple(name for name in live if name in required)
        allowed = table or all(column_select[name] for name in wanted)
    elif mode == "whole_row":
        _require(not required and bool(live), "whole-row reference requires known nonempty visible columns")
        wanted, allowed = live, table or all(column_select.values())
    elif mode == "any_column":
        _require(not required, "any-column and explicit requirements must stay distinct")
        wanted, allowed = (), table or any(column_select.values())
    else:
        raise ValueError("unknown dependency column requirement; pg_depend sub-ID0 is insufficient")
    return {"mode": mode, "required_columns": list(wanted), "metadata_select_allowed": allowed,
            "column_metadata_hash": fingerprint(columns), **_boundary()}


def function_context(*, root_query_user, function_owner, security_definer, caller_execute):
    """View ownership never substitutes for the query user's function EXECUTE."""
    user, owner = _oid(root_query_user), _oid(function_owner)
    return {"execute_check_principal": user, "caller_execute": _boolean(caller_execute),
            "function_body_principal": owner if _boolean(security_definer) else user,
            "function_body_safety_established": False, **_boundary()}


def rls_context(*, principal, relation_owner, owner_usage, superuser, bypass_rls, row_security,
                force_row_security, policies, role_usage):
    """Describe SELECT policy context, never evaluate expressions or returned rows.

    role_usage is independently captured effective USAGE membership for this
    exact principal, not inferred from admin/set options or parent count alone.
    """
    principal, owner = _oid(principal), _oid(relation_owner)
    su, bypass, enabled, force = map(_boolean, (superuser, bypass_rls, row_security, force_row_security))
    ownership = _boolean(owner_usage)
    _require(not (principal == owner or su) or ownership, "effective ownership metadata is inconsistent")
    _require(isinstance(policies, list) and isinstance(role_usage, dict), "complete policy and effective-membership metadata required")
    seen, policy_roles, applicable, permissive = set(), set(), [], []
    for policy in policies:
        _require(isinstance(policy, dict) and set(policy) == {"oid", "command", "roles", "permissive"}, "exact policy context required")
        oid = _oid(policy["oid"])
        _require(oid not in seen and isinstance(policy["command"], str)
                 and policy["command"] in {"*", "r", "a", "w", "d"}, "distinct recognized policy required")
        seen.add(oid)
        _boolean(policy["permissive"])
        roles = policy["roles"]
        _require(isinstance(roles, list) and bool(roles) and all(type(r) is int and 0 <= r <= 4294967295 for r in roles), "complete policy roles required")
        _require(len(roles) == len(set(roles)), "duplicate policy role")
        policy_roles.update(r for r in roles if r != 0)
    _require(set(role_usage) == policy_roles, "all and only applicable role-usage targets required")
    for role, allowed in role_usage.items():
        _oid(role)
        _boolean(allowed)
    for policy in policies:
        if policy["command"] in {"*", "r"} and any(r == 0 or role_usage[r] for r in policy["roles"]):
            applicable.append(policy["oid"])
            if policy["permissive"]:
                permissive.append(policy["oid"])
    reason = ("row_security_disabled" if not enabled else "superuser" if su else
              "bypass_rls" if bypass else "owner_without_force" if ownership and not force else None)
    enforced = reason is None
    return {"principal": principal, "relation_owner": owner, "rls_enforced": enforced,
            "owner_role_usage": ownership,
            "bypass_reason": reason, "applicable_SELECT_policy_oids": sorted(applicable),
            "applicable_permissive_SELECT_policy_oids": sorted(permissive),
            "no_permissive_SELECT_policy": enforced and not permissive,
            "policy_expression_safety_established": False, "returned_rows_established": False,
            **_boundary()}
