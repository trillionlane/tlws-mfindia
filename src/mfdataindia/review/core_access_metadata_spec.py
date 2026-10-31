"""Strict complete metadata capture, never permission or operational authority."""

import hashlib
import re

from mfdataindia.review.core_access_metadata_inventory import inventory
from mfdataindia.review.policy import fingerprint
from mfdataindia.review.reader_policy_source_version import current_attribution

IDENTITY = "dev-core-aclmeta-261022a"
STAGE = "core-access-metadata-not-acceptance"
INVENTORY_SHA256 = "8452e93a483f8e78155a892e1966294fc461fbe732dcacffe4d7410c35d73396"
HISTORICAL_SOURCE_HASHES = {
    "scripts/diagnose_core_fund_plan_dev.py": "7ef6110a5032566b8ab42418134c9bbdd45780bb0978624c336ebac5ae4484ab",
    "src/mfdataindia/api/queries.py": "c2043667ab261be1ae02eb9e5f32658c142e325739487582a555530f0e3f691e",
    "src/mfdataindia/review/privileges.py": "4929964f90292e48f9d81c054e848ed835d190c71a1c54230f82d0e4eb049c33",
    "src/mfdataindia/enrichment/current_facts_store.py": "380f656f5d70a4870c9c86d2f821cc14c418128a1ffb51cf9124c5e3b8b5afc0",
}
SOURCE_HASHES = {**current_attribution(HISTORICAL_SOURCE_HASHES),
    'src/mfdataindia/review/reader_access_profile.py': '0e2c4e2fd151a93e50c2428d6dd9eac405760e3b658781b5fed6ab2a7418a3e6',
    'src/mfdataindia/review/reader_policy_source_version.py': '4119daef42299025fea29180b65bc80b75ee666433dad9fbc18a5cb46c3c0900',
}
# These are PostgreSQL 18 bootstrap catalogue identities, not caller input.
PG_CLASS, PG_PROC, PG_OPERATOR = 1259, 1255, 2617
PG_REWRITE, PG_POLICY = 2618, 3256
BASE_PRIVILEGES = {
    "schema": ("USAGE", "CREATE"),
    "relation": ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"),
    "column": ("SELECT", "INSERT", "UPDATE", "REFERENCES"),
}


def require(condition):
    if not condition:
        raise ValueError("incomplete or invalid fixed metadata capture")


def exact_keys(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def oid(value, *, zero=False):
    require(type(value) is int and (0 if zero else 1) <= value <= 4294967295)


def scalar(value, kind):
    if kind == "bool":
        require(type(value) is bool)
    elif kind in ("int", "nonnegative_int", "positive_int"):
        require(type(value) is int)
        require(kind == "int" or value >= (1 if kind == "positive_int" else 0))
    elif kind == "positive_oid":
        oid(value)
    elif kind == "positive_oid_or_null":
        if value is not None:
            oid(value)
    elif kind.startswith("oid_including_zero"):
        oid(value, zero=True)
    elif kind in ("text", "text_or_null"):
        require((kind == "text_or_null" and value is None) or type(value) is str)
    elif kind == "text_array_or_null":
        require(value is None or (type(value) is list and all(type(v) is str for v in value)))
    elif kind == "oid_array_including_zero_PUBLIC":
        require(type(value) is list and bool(value) and len(set(value)) == len(value))
        for item in value:
            oid(item, zero=True)
    elif kind.startswith("enum_"):
        enums = {
            "enum_relation_schema_column": {"relation", "schema", "column"},
            "enum_view_return_policy": {"view_return", "policy"},
            "enum_schema_relation_column_role_usage": {"schema", "relation", "column", "role_usage"},
            "enum_function_operator": {"function", "operator"},
            "enum_star_r_a_w_d": {"*", "r", "a", "w", "d"},
        }
        require(type(value) is str and value in enums[kind])
    elif kind == "fixed_enum":
        require(type(value) is str and value in {
            p + suffix for ps in BASE_PRIVILEGES.values() for p in ps
            for suffix in ("", " WITH GRANT OPTION")
        })
    else:
        # Nested records have their own exact schemas below, never any-object.
        require(kind in ("array_of_exact_acl_records", "exact_function_or_operator_schema_below"))


def ordered(rows, keys):
    markers = [tuple(r[k] for k in keys) for r in rows]
    require(markers == sorted(set(markers)))


def validate_private(value):
    exact_keys(value, ("is_null", "sha256", "utf8_bytes"))
    scalar(value["is_null"], "bool")
    scalar(value["utf8_bytes"], "nonnegative_int")
    if value["is_null"]:
        require(value["sha256"] is None and value["utf8_bytes"] == 0)
    else:
        require(type(value["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]))


def seal_private(value):
    scalar(value, "text_or_null")
    raw = value.encode("utf-8") if value is not None else None
    return {"is_null": raw is None,
            "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "utf8_bytes": len(raw) if raw is not None else 0}


def validate_acl(row):
    require(row["raw_acl_is_null"] is (row["raw_acl"] is None))
    require(type(row["normalized_acl"]) is list)
    for acl in row["normalized_acl"]:
        exact_keys(acl, ("grantor", "grantee", "privilege", "grantable"))
        oid(acl["grantor"])
        oid(acl["grantee"], zero=True)
        require(type(acl["privilege"]) is str and acl["privilege"] in BASE_PRIVILEGES[row["object_kind"]])
        scalar(acl["grantable"], "bool")
    ordered(row["normalized_acl"], ("grantor", "grantee", "privilege", "grantable"))


def validate_function(metadata):
    schema = inventory()["nested_result_schemas"]["M09_function"]["exact_keys"]
    exact_keys(metadata, schema)
    for key in ("namespace_oid", "owner_oid", "language_oid", "return_type_oid"):
        oid(metadata[key])
    require(type(metadata["name"]) is str and bool(metadata["name"]))
    require(metadata["kind"] in ("f", "p", "a", "w"))
    require(metadata["volatility"] in ("i", "s", "v"))
    for key in ("security_definer", "leakproof"):
        scalar(metadata[key], "bool")
    scalar(metadata["argument_count"], "nonnegative_int")
    args, all_args = metadata["argument_type_oids"], metadata["all_argument_type_oids"]
    require(type(args) is list and len(args) == metadata["argument_count"])
    require(all_args is None or (type(all_args) is list and bool(all_args) and len(all_args) >= len(args)))
    for item in args + (all_args or []):
        oid(item)
    execute = metadata["execute"]
    require(type(execute) is list and len(execute) == 1)
    exact_keys(execute[0], ("principal_oid", "allowed", "grantable"))
    oid(execute[0]["principal_oid"])
    scalar(execute[0]["allowed"], "bool")
    scalar(execute[0]["grantable"], "bool")


def validate_operator(metadata):
    exact_keys(metadata, inventory()["nested_result_schemas"]["M09_operator"]["exact_keys"])
    for key in ("namespace_oid", "owner_oid", "right_type_oid"):
        oid(metadata[key])
    for key in ("function_oid", "left_type_oid", "result_type_oid"):
        oid(metadata[key], zero=True)
    require(type(metadata["name"]) is str and bool(metadata["name"]))
    require(metadata["kind"] in ("b", "l"))
    require((metadata["left_type_oid"] == 0) == (metadata["kind"] == "l"))
    require((metadata["function_oid"] == 0) == (metadata["result_type_oid"] == 0))


def validate_rows(definition, rows, *, sealed=False):
    require(type(rows) is list)
    for row in rows:
        exact_keys(row, definition["columns"])
        for key, kind in definition["columns"].items():
            if sealed and key in definition.get("private_memory_only_fields", []):
                validate_private(row[key])
            else:
                scalar(row[key], kind)
        if definition["id"] == "M04_root_and_mf_ACLs":
            validate_acl(row)
        if definition["id"] == "M09_functions_operators":
            (validate_function if row["object_kind"] == "function" else validate_operator)(row["metadata"])
    if definition.get("unique_order_key"):
        ordered(rows, definition["unique_order_key"])
    return rows


def sealed_rows(definition, rows):
    validate_rows(definition, rows)
    # Never retain a raw SQL body in the returned evidence graph.
    result = [{key: seal_private(value) if key in definition["private_memory_only_fields"] else value
               for key, value in row.items()} for row in rows]
    validate_rows(definition, result, sealed=True)
    return result


def validate_context(rows):
    definition = inventory()["context"]
    validate_rows(definition, rows)
    require(len(rows) == 1)
    row = rows[0]
    fixed = {"database_name": "mfdataindia", "session_user": "mfdata_verified_reader",
             "current_user": "mfdata_verified_reader", "parent_membership_count": 0,
             "isolation": "repeatable read", "read_only": "on", "statement_timeout": "5s",
             "lock_timeout": "5s", "transaction_timeout": "15s", "search_path": "pg_catalog",
             "row_security": "on", "timezone": "UTC"}
    require(all(row[k] == v for k, v in fixed.items()))
    require(all(row[k] is False for k in ("superuser", "create_role", "create_db", "replication", "bypass_rls")))
    require(180000 <= row["server_version_num"] < 190000)
    require(bool(re.fullmatch(r"[0-9]+:[0-9]+:(?:[0-9]+(?:,[0-9]+)*)?", row["snapshot"])))
    return row


def relation_access(roots, relations, edges, reader):
    pairs = {(r["reloid"], reader) for r in roots}
    while True:
        expanded = set(pairs)
        for source, _ in pairs:
            relation = relations[source]
            if relation["relation_kind"] != "v":
                continue
            principal = reader if "security_invoker=true" in (relation["relation_options"] or []) else relation["owner_oid"]
            for edge in edges:
                if (edge["source_relation"] == source and edge["source_kind"] == "view_return"
                        and edge["refclassid"] == PG_CLASS and edge["refobjid"] != source):
                    expanded.add((edge["refobjid"], principal))
        if pairs == expanded:
            return pairs
        pairs = expanded


def validate_variant_fields(columns, relation_oid):
    # Exactly two requested fields, NOT exactly two table columns. Extra live
    # columns and dropped physical slots remain in the complete witness.
    live = {c["name"] for c in columns if c["relation_oid"] == relation_oid and not c["dropped"]}
    require({"amfi_scheme_code", "group_key"} <= live)


def validate_snapshot(groups, context):
    definitions = inventory()["statements"]
    exact_keys(groups, [d["id"] for d in definitions])
    for definition in definitions:
        validate_rows(definition, groups[definition["id"]], sealed=True)
    targets, graph, columns, acls, edges, roles, members, caps, functions, policies = [
        groups[d["id"]] for d in definitions]
    require(len(targets) == 2)
    require([(t["ord"], t["nspname"], t["relname"], t["expected_kind"]) for t in targets]
            == [(1, "mf", "current_fund_facts", "v"), (2, "mf", "fund_variants", "r")])
    require(all(t["reloid"] is not None and t["nspoid"] is not None for t in targets))
    require(len({t["reloid"] for t in targets}) == 2 and targets[0]["nspoid"] == targets[1]["nspoid"])
    relations = {r["relation_oid"]: r for r in graph}
    role_map = {r["role_oid"]: r for r in roles}
    reader = context["reader_oid"]
    require(reader in role_map and role_map[reader]["name"] == "mfdata_verified_reader")
    for key in ("superuser", "create_role", "create_db", "replication", "bypass_rls"):
        require(role_map[reader][key] is False)
    for t in targets:
        require(t["reloid"] in relations)
        r = relations[t["reloid"]]
        require((r["namespace_oid"], r["namespace_name"], r["relation_name"], r["relation_kind"])
                == (t["nspoid"], t["nspname"], t["relname"], t["expected_kind"]))
    col_map = {(c["relation_oid"], c["attnum"]): c for c in columns}
    require(set(col_map) == {(r["relation_oid"], n) for r in graph for n in range(1, r["attribute_count"] + 1)})
    validate_variant_fields(columns, targets[1]["reloid"])
    for r in graph:
        require(r["owner_oid"] in role_map)
        require(r["private_view_definition"]["is_null"] is (r["relation_kind"] != "v"))
        live = [c["name"] for c in columns if c["relation_oid"] == r["relation_oid"] and not c["dropped"]]
        require(all(live) and len(live) == len(set(live)))
    for c in columns:
        require((c["type_oid"] == 0) is c["dropped"])
    root_ids = {t["reloid"] for t in targets}
    expected_acls = {("relation", r, 0) for r in root_ids} | {("schema", targets[0]["nspoid"], 0)}
    expected_acls |= {("column", r, n) for r, n in col_map if r in root_ids}
    require({(a["object_kind"], a["object_oid"], a["sub_id"]) for a in acls} == expected_acls)
    for acl in acls:
        if acl["object_kind"] != "schema":
            require(acl["owner_oid"] == relations[acl["object_oid"]]["owner_oid"])
        for entry in acl["normalized_acl"]:
            require(entry["grantor"] in role_map and (entry["grantee"] == 0 or entry["grantee"] in role_map))
    reached = set(root_ids)
    while True:
        next_set = reached | {e["refobjid"] for e in edges if e["source_relation"] in reached and e["refclassid"] == PG_CLASS}
        if next_set == reached:
            break
        reached = next_set
    require(reached == set(relations))
    for e in edges:
        require(e["source_relation"] in relations)
        require(e["classid"] == (PG_REWRITE if e["source_kind"] == "view_return" else PG_POLICY))
        if e["refclassid"] == PG_CLASS:
            require(e["refobjid"] in relations)
            if e["refobjsubid"]:
                require((e["refobjid"], e["refobjsubid"]) in col_map)
    policy_targets = {p for row in policies for p in row["role_oids"] if p}
    require(len({p["policy_oid"] for p in policies}) == len(policies))
    for p in policies:
        require(p["relation_oid"] in relations and bool(p["name"]))
    f_map = {f["object_oid"]: f["metadata"] for f in functions if f["object_kind"] == "function"}
    o_map = {f["object_oid"]: f["metadata"] for f in functions if f["object_kind"] == "operator"}
    require(set(o_map) == {e["refobjid"] for e in edges if e["refclassid"] == PG_OPERATOR})
    require(set(f_map) == {e["refobjid"] for e in edges if e["refclassid"] == PG_PROC}
            | {o["function_oid"] for o in o_map.values() if o["function_oid"]})
    for f in f_map.values():
        require(f["execute"][0]["principal_oid"] == reader and f["owner_oid"] in role_map)
    for m in members:
        require(all(m[k] in role_map for k in ("role_oid", "member_oid", "grantor_oid")))
        require(m["member_oid"] != reader)
    pairs = relation_access(targets, relations, edges, reader)
    relation_principals = {p for _, p in pairs}
    body_principals = {f["owner_oid"] if f["security_definer"] else reader for f in f_map.values()}
    context_sets = {"is_relation_access_principal": relation_principals,
                    "is_function_body_principal": body_principals,
                    "is_policy_role_target": policy_targets, "is_original_query_user": {reader}}
    for key, population in context_sets.items():
        require(population <= set(role_map))
        require({r["role_oid"] for r in roles if r[key]} == population)
    expected = set()
    for rel, principal in pairs:
        for kind, obj, sub in [("relation", rel, 0), ("schema", relations[rel]["namespace_oid"], 0)]:
            expected |= {(kind, principal, obj, sub, p + suffix) for p in BASE_PRIVILEGES[kind]
                         for suffix in ("", " WITH GRANT OPTION")}
        for (rid, num), col in col_map.items():
            if rid == rel and not col["dropped"]:
                expected |= {("column", principal, rel, num, p + suffix) for p in BASE_PRIVILEGES["column"]
                             for suffix in ("", " WITH GRANT OPTION")}
        usage_targets = {relations[rel]["owner_oid"]} | {
            p for policy in policies if policy["relation_oid"] == rel for p in policy["role_oids"] if p}
        expected |= {("role_usage", principal, target, 0, "USAGE") for target in usage_targets}
    require({tuple(c[k] for k in ("object_kind", "principal_oid", "object_oid", "sub_id", "privilege"))
             for c in caps} == expected)
    # No all-role catalogue or all-seeds x all-objects capability expansion.
    seeds = relation_principals | {reader} | {f["owner_oid"] for f in f_map.values() if f["security_definer"]}
    ancestors, descendants = set(seeds), {reader}
    while True:
        above = ancestors | {m["role_oid"] for m in members if m["member_oid"] in ancestors}
        below = descendants | {m["member_oid"] for m in members if m["role_oid"] in descendants}
        if (above, below) == (ancestors, descendants):
            break
        ancestors, descendants = above, below
    require(all(m["member_oid"] in ancestors or m["role_oid"] in descendants for m in members))
    identities = ancestors | descendants | policy_targets | {r["owner_oid"] for r in graph}
    identities |= {f["owner_oid"] for f in f_map.values()} | {m["grantor_oid"] for m in members}
    identities |= {a[k] for row in acls for a in row["normalized_acl"] for k in ("grantor", "grantee") if a[k]}
    require(set(role_map) == identities)
    return groups


def admission_blockers(groups):
    """Conservative finite markers, never dynamic expression/body safety claims."""
    blockers = {"NO_PERMISSION_WRITE_OR_LIVE_APPROVAL"}
    graph = groups["M02_relation_graph"]
    edges = groups["M05_declared_edges"]
    if any(r["relation_kind"] not in ("r", "v") for r in graph):
        blockers.add("UNMODELED_RELATION_KIND")
    for edge in edges:
        if edge["refclassid"] not in (PG_CLASS, PG_PROC, PG_OPERATOR):
            blockers.add("UNMODELED_DEPENDENCY_CLASS")
        if edge["source_kind"] == "policy":
            blockers.add("POLICY_EXPRESSION_SECURITY_UNMODELED")
        if edge["refclassid"] == PG_CLASS and edge["refobjsubid"] == 0:
            if not (edge["refobjid"] == edge["source_relation"] and edge["deptype"] == "i"):
                blockers.add("AMBIGUOUS_WHOLE_ROW_OR_SECURITY_PATH")
    if groups["M09_functions_operators"]:
        blockers.add("FUNCTION_BODY_OR_OPERATOR_SAFETY_UNMODELED")
    if any(r["object_kind"] == "operator" and r["metadata"]["function_oid"] == 0
           for r in groups["M09_functions_operators"]):
        blockers.add("SHELL_OPERATOR")
    # Graph cycles need separate security-path modelling, even when capture terminates.
    adjacency = {r["relation_oid"]: set() for r in graph}
    for e in edges:
        if e["refclassid"] == PG_CLASS and not (e["source_relation"] == e["refobjid"] and e["deptype"] == "i"):
            adjacency[e["source_relation"]].add(e["refobjid"])
    def cycle(node, trail):
        return node in trail or any(cycle(child, trail | {node}) for child in adjacency[node])
    if any(cycle(n, set()) for n in adjacency):
        blockers.add("CYCLIC_SECURITY_PATH")
    return sorted(blockers)


def specification():
    value = inventory()
    return {"inventory_sha256": INVENTORY_SHA256, "context": value["context"],
            "statements": value["statements"], "transaction": value["transaction"],
            "nested_result_schemas": value["nested_result_schemas"], "source_hashes": SOURCE_HASHES}


def validate_result(value):
    exact_keys(value, ("level", "context", "groups", "completions", "specification_hash", "metadata_hash",
                       "worker_binding", "source_hashes", "access_admission_blockers", "permission_authority",
                       "runtime_authority", "performance_acceptance", "rollout_acceptance"))
    require(value["level"] == "CURRENT_METADATA_ONLY_NOT_HISTORICAL_CAUSALITY")
    validate_context([value["context"]])
    validate_snapshot(value["groups"], value["context"])
    require(value["specification_hash"] == fingerprint(specification()) and value["source_hashes"] == SOURCE_HASHES)
    require(value["metadata_hash"] == fingerprint({"context": value["context"], "groups": value["groups"]}))
    require(value["access_admission_blockers"] == admission_blockers(value["groups"]))
    require(all(value[k] is False for k in ("permission_authority", "runtime_authority", "performance_acceptance", "rollout_acceptance")))
    sequence = inventory()["transaction"]["success_query_sequence"]
    require(type(value["completions"]) is list and len(value["completions"]) == 22)
    for completion, label in zip(value["completions"], sequence, strict=True):
        exact_keys(completion, ("id", "command_rows", "driver_rows", "fetched_rows"))
        count = 1 if label == "C00" else len(value["groups"][label])
        require(completion["id"] == label)
        for key in ("command_rows", "driver_rows", "fetched_rows"):
            require(type(completion[key]) is int and completion[key] == count)
    return value
