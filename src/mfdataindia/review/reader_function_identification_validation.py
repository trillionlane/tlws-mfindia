"""Pure diagnostic-output checks. No connection, secret, provider or live entrypoint."""
import json
from copy import deepcopy

SCHEMAS = (
    "mf", "mf_analytics", "mf_book_facts", "mf_distributions", "mf_enrichment",
    "mf_fact_refresh", "mf_fact_review", "mf_idcw", "mf_live_facts",
    "mf_monthly_facts", "mf_multi_facts", "mf_official_facts", "mf_overlap",
    "mf_portfolios", "mf_review", "mf_rolling",
)
FORMAT = "mf-reader-privileged-function-identification-v1"
MAX_RAW_BYTES = 131000
FUNCTION_KEYS = (
    "oid", "namespace_oid", "schema", "name", "kind", "owner_oid", "owner_name",
    "argument_type_oids", "return_type_oid", "security_definer",
    "effective_execute", "schema_usage", "acl_is_null", "execute_acl_atoms",
)
ATOM_KEYS = (
    "grantor_oid", "grantee_oid", "grantee_name", "is_grantable",
    "reader_is_grantee", "role_privileges_immediately_available",
    "reader_is_member", "reader_can_set_role",
)


def require(ok):
    if not ok:
        raise ValueError("INVALID_FUNCTION_IDENTIFICATION")


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected))


def oid(value, *, public=False):
    require(type(value) is int and (0 if public else 1) <= value <= 4294967295)


def boolean(value):
    require(type(value) is bool)


def identifier(value):
    require(type(value) is str and 0 < len(value.encode("utf-8")) <= 63
            and "\x00" not in value)


def ordered_unique(values):
    require(values == sorted(set(values)))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def parse_document(raw):
    require(type(raw) is bytes and len(raw) <= MAX_RAW_BYTES)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result

    def constant(_):
        raise ValueError("INVALID_FUNCTION_IDENTIFICATION")

    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("INVALID_FUNCTION_IDENTIFICATION") from None
    return validate_document(value)


def validate_document(value):
    keys(value, ("format", "reader", "schemas", "functions"))
    require(value["format"] == FORMAT)
    require(type(value["reader"]) is list and len(value["reader"]) == 1)
    reader = value["reader"][0]
    keys(reader, ("oid", "rolname", "rolsuper", "rolinherit"))
    oid(reader["oid"])
    require(reader["oid"] == 40032 and reader["rolname"] == "mfdata_verified_reader")
    boolean(reader["rolsuper"])
    boolean(reader["rolinherit"])
    require(type(value["schemas"]) is list and len(value["schemas"]) == len(SCHEMAS))
    namespaces = {}
    names = []
    for ns in value["schemas"]:
        keys(ns, ("oid", "nspname"))
        oid(ns["oid"])
        identifier(ns["nspname"])
        require(ns["nspname"] in SCHEMAS and ns["oid"] not in namespaces)
        namespaces[ns["oid"]] = ns["nspname"]
        names.append(ns["nspname"])
    ordered_unique([ns["oid"] for ns in value["schemas"]])
    require(set(names) == set(SCHEMAS))
    require(type(value["functions"]) is list)
    function_oids = []
    for func in value["functions"]:
        keys(func, FUNCTION_KEYS)
        for key in ("oid", "namespace_oid", "owner_oid", "return_type_oid"):
            oid(func[key])
        require(namespaces.get(func["namespace_oid"]) == func["schema"])
        identifier(func["name"])
        identifier(func["owner_name"])
        require(func["kind"] in ("f", "p", "a", "w"))
        require(type(func["argument_type_oids"]) is list)
        for arg in func["argument_type_oids"]:
            oid(arg)
        for key in ("security_definer", "effective_execute", "schema_usage", "acl_is_null"):
            boolean(func[key])
        require(func["security_definer"] and func["effective_execute"])
        require(type(func["execute_acl_atoms"]) is list)
        atom_order = []
        has_available_grant = False
        for atom in func["execute_acl_atoms"]:
            keys(atom, ATOM_KEYS)
            oid(atom["grantor_oid"])
            oid(atom["grantee_oid"], public=True)
            identifier(atom["grantee_name"])
            for key in ATOM_KEYS[3:]:
                boolean(atom[key])
            require(atom["reader_is_grantee"] is (atom["grantee_oid"] == 40032))
            if atom["grantee_oid"] == 0:
                require(atom["grantee_name"] == "PUBLIC"
                        and atom["role_privileges_immediately_available"]
                        and not atom["reader_is_member"] and not atom["reader_can_set_role"])
            elif atom["grantee_oid"] == 40032:
                require(atom["grantee_name"] == reader["rolname"]
                        and atom["role_privileges_immediately_available"]
                        and atom["reader_is_member"] and atom["reader_can_set_role"])
            else:
                require(atom["grantee_name"] != reader["rolname"])
                if not reader["rolsuper"]:
                    require(not atom["role_privileges_immediately_available"]
                            or atom["reader_is_member"])
            has_available_grant |= atom["role_privileges_immediately_available"]
            atom_order.append((atom["grantor_oid"], atom["grantee_oid"], atom["is_grantable"]))
        ordered_unique(atom_order)
        require(len({(a["grantor_oid"], a["grantee_oid"])
                     for a in func["execute_acl_atoms"]}) == len(atom_order))
        # Ownership alone is not treated as a current EXECUTE grant.
        require(reader["rolsuper"] or has_available_grant)
        function_oids.append(func["oid"])
    ordered_unique(function_oids)
    try:
        require(len(canonical(value).encode("utf-8")) <= MAX_RAW_BYTES)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("INVALID_FUNCTION_IDENTIFICATION") from None
    return deepcopy(value)


def route_summary(value):
    """Summarize several possible routes; never assert invocation or write safety."""
    value = validate_document(value)
    summaries = []
    for func in value["functions"]:
        routes = set()
        if value["reader"][0]["rolsuper"]:
            routes.add("SUPERUSER_BYPASS")
        for atom in func["execute_acl_atoms"]:
            if atom["grantee_oid"] == 0:
                routes.add("PUBLIC_DEFAULT" if func["acl_is_null"] else "PUBLIC_STORED")
            elif atom["reader_is_grantee"]:
                routes.add("DIRECT_READER")
            elif atom["role_privileges_immediately_available"]:
                routes.add("ROLE_PRIVILEGES_AVAILABLE_SUPERUSER_NOT_CAUSAL"
                           if value["reader"][0]["rolsuper"] else "INHERITED_CURRENT")
            elif atom["reader_can_set_role"]:
                routes.add("SET_ROLE_POSSIBLE_NOT_CURRENT_EXECUTE_ROUTE")
            elif atom["reader_is_member"]:
                routes.add("MEMBER_ONLY_NOT_CURRENT_EXECUTE_ROUTE")
        summaries.append({"oid": func["oid"], "routes": sorted(routes),
                          "callability_proved": False, "function_write_safety_proved": False})
    return summaries
