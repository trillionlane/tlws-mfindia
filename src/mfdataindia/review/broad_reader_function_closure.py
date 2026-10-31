"""Fixed metadata-only witness for the reviewed helper and its one callee.

No function is invoked. Full body, definition and settings values are sealed
inside PostgreSQL; no private source or setting value leaves the connection.
This check is necessary but not a live grant admission on its own.
"""

from copy import deepcopy
import re

from mfdataindia.review import broad_reader_policy as policy


FUNCTION_SQL = """SELECT p.oid::bigint AS oid,n.oid::bigint AS namespace_oid,
n.nspname AS schema,p.proname AS name,p.proowner::bigint AS owner_oid,
p.prokind::text AS kind,p.proargtypes::text AS argument_oids,
p.prorettype::bigint AS return_oid,l.lanname AS language,
l.lanpltrusted AS language_trusted,p.procost::text AS cost,p.prorows::text AS rows,
p.provariadic::bigint AS variadic_oid,p.prosupport::bigint AS support_oid,
p.proleakproof AS leakproof,p.proallargtypes IS NULL AS all_arguments_null,
p.proargmodes IS NULL AS argument_modes_null,
p.protrftypes IS NULL AS transform_types_null,
p.proargdefaults IS NULL AS argument_defaults_null,
p.probin IS NULL AS binary_null,p.prosqlbody IS NULL AS sql_body_null,
jsonb_build_object('is_null',p.proargnames IS NULL,
 'sha256',CASE WHEN p.proargnames IS NULL THEN NULL ELSE
 encode(sha256(convert_to(array_to_json(p.proargnames)::text,'UTF8')),'hex') END,
 'utf8_bytes',CASE WHEN p.proargnames IS NULL THEN 0 ELSE
 octet_length(convert_to(array_to_json(p.proargnames)::text,'UTF8')) END) AS argument_names,
p.prosecdef AS security_definer,p.provolatile::text AS volatility,
p.proisstrict AS strict,p.proparallel::text AS parallel,
p.proretset AS returns_set,p.pronargs::int AS argument_count,
p.pronargdefaults::int AS argument_default_count,
jsonb_build_object('is_null',false,
 'sha256',encode(sha256(convert_to(p.prosrc,'UTF8')),'hex'),
 'utf8_bytes',octet_length(convert_to(p.prosrc,'UTF8'))) AS body,
jsonb_build_object('is_null',false,
 'sha256',encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex'),
 'utf8_bytes',octet_length(convert_to(pg_get_functiondef(p.oid),'UTF8'))) AS definition,
jsonb_build_object('is_null',p.proconfig IS NULL,
 'sha256',CASE WHEN p.proconfig IS NULL THEN NULL ELSE
 encode(sha256(convert_to(array_to_json(p.proconfig)::text,'UTF8')),'hex') END,
 'utf8_bytes',CASE WHEN p.proconfig IS NULL THEN 0 ELSE
 octet_length(convert_to(array_to_json(p.proconfig)::text,'UTF8')) END) AS configuration,
COALESCE((SELECT jsonb_agg(jsonb_build_array(a.grantor::bigint,a.grantee::bigint,
 a.privilege_type,a.is_grantable) ORDER BY a.grantor,a.grantee,a.privilege_type,a.is_grantable)
 FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a),
 '[]'::jsonb) AS acl,
has_function_privilege('mfdata_verified_reader',p.oid,'EXECUTE') AS reader_execute,
has_schema_privilege('mfdata_verified_reader',n.oid,'USAGE') AS reader_schema_usage
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
JOIN pg_catalog.pg_language l ON l.oid=p.prolang
WHERE (n.nspname='mf_fact_review' AND p.proname='dependencies')
   OR (n.nspname='mf_review' AND p.proname='canonical_json')
ORDER BY n.nspname,p.proname,p.oid"""

_FUNCTION_KEYS = frozenset({
    "oid", "namespace_oid", "schema", "name", "owner_oid", "kind", "argument_oids",
    "return_oid", "language", "language_trusted", "cost", "rows", "variadic_oid",
    "support_oid", "leakproof", "all_arguments_null", "argument_modes_null",
    "transform_types_null", "argument_defaults_null", "binary_null", "sql_body_null",
    "argument_names", "security_definer", "volatility", "strict", "parallel",
    "returns_set", "argument_count", "argument_default_count", "body", "definition",
    "configuration", "acl", "reader_execute", "reader_schema_usage",
})
FUNCTION_COLUMNS = (
    "oid namespace_oid schema name owner_oid kind argument_oids return_oid "
    "language language_trusted cost rows variadic_oid support_oid leakproof "
    "all_arguments_null argument_modes_null transform_types_null "
    "argument_defaults_null binary_null sql_body_null argument_names "
    "security_definer volatility strict parallel returns_set argument_count "
    "argument_default_count body definition configuration acl reader_execute "
    "reader_schema_usage"
).split()
_EXPECTED = {
    ("mf_fact_review", "dependencies"): {
        "argument_oids": "23", "return_oid": 3802, "security_definer": True,
        "volatility": "s", "strict": False,
        "body": ("06f4c87653e1aa4423f2183e9f2476c114eec06224b6d166f1d188d7d8452aac", 1824),
        "definition": ("98436cf7fa504178e56372a99c6fc584e31c10447fafb900129901b58e875aaa", 2010),
        "configuration": ("f156afc13bf2dac90d3d3f2965f5e4e3379aaa44d4dacc6ccd902462c63232e1", 26),
        "argument_names": ("4ae5d9d637de6b539d46c0c0df7ad483ce462934bd4a88880fe2a2df331cb4aa", 10),
        "reader_execute": True,
    },
    ("mf_review", "canonical_json"): {
        "argument_oids": "3802", "return_oid": 25, "security_definer": False,
        "volatility": "i", "strict": True,
        "body": ("9f6d6eb2b15070d79ad0e12052218cf9f914335c68c41dd28421b7d5acf444ba", 835),
        "definition": ("bc0e39c23271d0887d446fcb470a469f64006b435acd00e6302f801d29fbf286", 1020),
        "configuration": ("c7db48fe507ed87f844868dba5cb1a12ad5a20481adfb34d288e4e213b54dc25", 37),
        "argument_names": ("7f9c352fcf30fb536f10b56edf3e276c33b828a000d9fcd4bc9ec58d6fa11586", 9),
        "reader_execute": False,
    },
}


class FunctionClosureError(ValueError):
    """A helper, callee, overload, ACL or exact source seal is unreviewed."""


def _require(condition):
    if not condition:
        raise FunctionClosureError("UNREVIEWED_BROAD_READER_FUNCTION_CLOSURE")


def _seal(value, expected):
    _require(type(value) is dict and set(value) == {"is_null", "sha256", "utf8_bytes"})
    _require(value["is_null"] is False and value["sha256"] == expected[0]
             and value["utf8_bytes"] == expected[1])


def verify(rows, state):
    """Bind two full live declarations to dynamic OIDs and unchanged ACLs.

    The exact bodies are source-reviewed for helper -> canonical_json and the
    callee's self-recursion. This does not execute either function or grant.
    """
    return _verify(rows, state)


def verify_profile_helpers(rows, state, *, restricted, callee_execute_allowed):
    """Same complete declarations for trusted profile startup/installation.

    Restricted supports independently owned historical schemas, only by the two
    original installer owners. Actual helper-owner callee EXECUTE must come from
    a fresh catalogue check, never a new grant or an assumed mixed-owner rule.
    Missing reader schema USAGE is handled by the selected grant planner; no
    function is invoked and this result is not operational grant admission.
    """
    _require(type(restricted) is bool and callee_execute_allowed is True)
    value = _verify(rows, state, restricted=restricted, require_usage=False)
    return {**value, "format": "mf-reader-profile-helper-declarations-v1-nonexecutable"}


def _verify(rows, state, *, restricted=False, require_usage=True):
    _require(type(rows) is list and len(rows) == 2 and type(state) is dict)
    namespaces = {r["nspname"]: r for r in state.get("namespaces", [])}
    roles = {r["rolname"]: r for r in state.get("roles", [])}
    _require(policy.READER in roles and (restricted or policy.OWNER in roles))
    _require("mfdata_verified_admin" in roles)
    reader_oid = roles[policy.READER]["oid"]
    owner_names = (policy.OWNER, "postgres") if restricted else (policy.OWNER,)
    owners = {roles[name]["oid"] for name in owner_names if name in roles}
    admin_oid = roles["mfdata_verified_admin"]["oid"]
    actual = {}
    for row in rows:
        _require(type(row) is dict and set(row) == _FUNCTION_KEYS)
        key = (row["schema"], row["name"])
        _require(key in _EXPECTED and key not in actual)
        expected = _EXPECTED[key]
        owner_oid = namespaces[key[0]]["owner"]
        _require(owner_oid in owners)
        _require(type(row["oid"]) is int and row["oid"] > 0
                 and row["namespace_oid"] == namespaces[key[0]]["oid"]
                 and row["owner_oid"] == owner_oid)
        _require(row["kind"] == "f" and row["language"] == "plpgsql"
                 and row["language_trusted"] is True
                 and row["cost"] == "100" and row["rows"] == "0"
                 and row["variadic_oid"] == row["support_oid"] == 0
                 and row["leakproof"] is False
                 and all(row[key] is True for key in (
                     "all_arguments_null", "argument_modes_null", "transform_types_null",
                     "argument_defaults_null", "binary_null", "sql_body_null"))
                 and row["parallel"] == "u" and row["returns_set"] is False
                 and row["argument_count"] == 1 and row["argument_default_count"] == 0)
        _require(all(row[k] == expected[k] for k in (
            "argument_oids", "return_oid", "security_definer", "volatility", "strict",
            "reader_execute")))
        for name in ("body", "definition", "configuration", "argument_names"):
            _seal(row[name], expected[name])
        _require(type(row["reader_schema_usage"]) is bool
                 and row["reader_schema_usage"] is namespaces[key[0]]["usage"])
        if key[1] == "dependencies" and require_usage:
            _require(row["reader_schema_usage"] is True)
        _require(type(row["acl"]) is list)
        if key[1] == "canonical_json":
            _require(row["acl"] == [[owner_oid, owner_oid, "EXECUTE", False]])
        else:
            _require(row["acl"] == sorted([
                [owner_oid, owner_oid, "EXECUTE", False],
                [owner_oid, reader_oid, "EXECUTE", False],
                [owner_oid, admin_oid, "EXECUTE", False]]))
        actual[key] = row
    _require(set(actual) == set(_EXPECTED))
    state_rows = {f["oid"]: f for f in state.get("functions", [])}
    _require(all(row["oid"] in state_rows for row in actual.values()))
    _require(len({row["oid"] for row in actual.values()}) == 2)
    for row in actual.values():
        broad = state_rows[row["oid"]]
        expected = _EXPECTED[(row["schema"], row["name"])]
        _require(all((broad[k] == row[v]) for k, v in (
            ("pronamespace", "namespace_oid"), ("proname", "name"),
            ("proowner", "owner_oid"), ("prokind", "kind"),
            ("proargtypes", "argument_oids"), ("prorettype", "return_oid"),
            ("prosecdef", "security_definer"), ("acl", "acl"),
            ("execute_allowed", "reader_execute"))))
        _require(broad["private_body_sha256"] == row["body"]["sha256"])
        if row["name"] == "canonical_json":
            _require(broad["proconfig"] == {"is_null": False,
                     "sha256": expected["configuration"][0],
                     "utf8_bytes": expected["configuration"][1]})
        else:
            _require(broad["proconfig"] == row["configuration"])
    _require(not any(f.get("proname") in ("dependencies", "canonical_json")
                     and f["oid"] not in {row["oid"] for row in actual.values()}
                     for f in state["functions"]))
    _require(not any(f.get("prosecdef") is True and f.get("execute_allowed") is True
                     and f["oid"] != actual[("mf_fact_review", "dependencies")]["oid"]
                     for f in state["functions"]))
    _require(all(re.fullmatch(r"[0-9a-f]{64}", row["body"]["sha256"])
                 for row in actual.values()))
    return {"format": "mf-broad-reader-function-closure-v1-nonexecutable",
            "functions": deepcopy(rows), "source_reviewed_call_graph": [
                "mf_fact_review.dependencies(integer)->mf_review.canonical_json(jsonb)",
                "mf_review.canonical_json(jsonb)->self"],
            "function_invoked": False, "grant_authorized": False}
