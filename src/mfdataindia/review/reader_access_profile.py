"""Versioned reader composition, derived from the existing broad grant policy.

Profiles are selected by trusted Python factories/installers. They never grant
an HTTP caller a capability or replace effective database-state verification.
No connection, secret lookup, commit or live operation occurs on import.
"""

from copy import deepcopy

from psycopg import sql
from psycopg.pq import TransactionStatus

from . import broad_reader_policy as policy
from . import broad_reader_function_scope as helper_scope
from . import reader_function_attestation_reference as helper_reference
from . import broad_reader_function_closure as helper_closure

RESTRICTED = "mf-review-restricted-read-v1"
BROAD = "mf-fund-research-read-v1"

# Upper bounds from the existing restricted installation recipes, not grants.
# Unknown/new relations require explicit adoption, not a silent broad default.
RESTRICTED_TABLES = {
    "mf": frozenset("funds fund_facts nav_history nav_source_revisions dataset_summary source_snapshot_runs quality_flags nav_quality_assessments current_fund_facts".split()),
    "mf_review": frozenset("policies snapshots results events certifications".split()),
    "mf_analytics": frozenset("calendar_results calendar_certifications".split()),
    "mf_rolling": frozenset("rolling_results rolling_certifications".split()),
    "mf_enrichment": frozenset("documents mappings".split()),
    "mf_official_facts": frozenset({"snapshots"}),
    "mf_portfolios": frozenset({"snapshots"}),
    "mf_overlap": frozenset({"results"}),
    "mf_distributions": frozenset({"snapshots"}),
    "mf_idcw": frozenset({"snapshots"}),
    "mf_live_facts": frozenset("configuration baseline field_versions field_heads selected_fields current_values resolved".split()),
    "mf_monthly_facts": frozenset({"snapshots"}),
    "mf_fact_refresh": frozenset("runs publications transitions".split()),
    "mf_fact_review": frozenset({"events"}),
    "mf_multi_facts": frozenset("configuration snapshots runs transitions prior_field_versions".split()),
    "mf_book_facts": frozenset("configuration batches runs transitions prior_field_versions".split()),
}
RESTRICTED_COLUMNS = {
    ("mf", "amcs"): frozenset({"amc_id", "amfi_amc_name"}),
    ("mf", "fund_variants"): frozenset({"amfi_scheme_code", "group_key"}),
    ("mf_portfolios", "sources"): frozenset({"source_id", "content_hash", "intake"}),
    ("mf_distributions", "sources"): frozenset({"source_id", "metadata"}),
    ("mf_idcw", "sources"): frozenset({"source_id", "metadata"}),
    ("mf_monthly_facts", "sources"): frozenset({"content_hash", "byte_length", "intake"}),
    ("mf_fact_refresh", "catalogs"): frozenset({"catalog_id", "source_url", "media_type", "content_hash"}),
    ("mf_multi_facts", "sources"): frozenset({"content_hash", "source_url", "byte_length"}),
    ("mf_book_facts", "sources"): frozenset({"source_key", "content_hash", "source_url", "media_type", "byte_length"}),
}


class ReaderAccessError(ValueError):
    """Stable, value-free profile/configuration failure."""


def contract(selected=RESTRICTED):
    if type(selected) is not str or selected not in (RESTRICTED, BROAD):
        raise ReaderAccessError("INVALID_READER_ACCESS_PROFILE")
    if selected == BROAD:
        return policy.profile()
    return {"format": RESTRICTED, "reader": policy.READER,
            "schemas": list(policy.SCHEMAS),
            "relation_read_upper_bounds": {k: sorted(v) for k, v in RESTRICTED_TABLES.items()},
            "column_read_upper_bounds": {".".join(k): sorted(v) for k, v in RESTRICTED_COLUMNS.items()},
            "source_content_select": False, "writes": False,
            "administrative_access": False}


def source_read_expected(principal, selected=RESTRICTED):
    contract(selected)
    if selected == BROAD:
        if principal != policy.READER:
            raise ReaderAccessError("READER_PROFILE_IDENTITY_MISMATCH")
        return True
    return principal == "mfdata_verified_generator"


def is_broad_reader(principal, selected=RESTRICTED):
    contract(selected)
    return selected == BROAD and principal == policy.READER


def guard_options(selected):
    contract(selected)
    return {} if selected == RESTRICTED else {"reader_profile": selected}


def _existing_helper(state, *, restricted=False):
    """Retain the reviewed privileged helper only; never add EXECUTE.

    Existing non-privileged invoker helpers are not revoked or expanded.

    This checks real catalogue OIDs and the committed body/declaration seals.
    It does not manufacture a live capture receipt or invoke the helper.
    """
    privileged = [f for f in state["functions"]
                  if f["prosecdef"] and f["execute_allowed"]]
    if not privileged:
        return None
    roles = {r["rolname"]: r["oid"] for r in state["roles"]}
    namespaces = {n["nspname"]: n["oid"] for n in state["namespaces"]}
    namespace = next((n for n in state["namespaces"] if n["nspname"] == "mf_fact_review"), None)
    owners = (policy.OWNER, "postgres") if restricted else (policy.OWNER,)
    owner_name = next((name for name in owners if namespace and roles.get(name) == namespace["owner"]), None)
    expected_roles = (owner_name, policy.READER, "mfdata_verified_admin")
    if len(privileged) != 1 or any(name not in roles for name in expected_roles):
        raise ReaderAccessError("UNREVIEWED_READER_FUNCTION")
    row = privileged[0]
    expected = {
        "pronamespace": namespaces.get("mf_fact_review"), "proname": "dependencies",
        "proowner": roles[owner_name], "prosecdef": True, "prokind": "f",
        "proargtypes": "23", "prorettype": 3802,
        "private_body_sha256": helper_reference.BODY_SHA256,
        "proconfig": {"is_null": False,
                      "sha256": helper_reference.EXPECTED_DECLARATION["configuration_sha256"],
                      "utf8_bytes": helper_reference.EXPECTED_DECLARATION["configuration_bytes"]},
        "execute_allowed": True,
        "acl": sorted([[roles[owner_name], roles[name], "EXECUTE", False]
                       for name in expected_roles]),
    }
    if any(row.get(key) != value for key, value in expected.items()):
        raise ReaderAccessError("UNREVIEWED_READER_FUNCTION")
    if restricted:
        callee_namespace = next((n for n in state["namespaces"] if n["nspname"] == "mf_review"), None)
        callee_owner = next((roles[name] for name in owners
                             if callee_namespace and roles.get(name) == callee_namespace["owner"]), None)
        callees = [f for f in state["functions"] if f["pronamespace"] == namespaces.get("mf_review")
                   and f["proname"] == "canonical_json" and f["proargtypes"] == "3802"]
        expected_callee = {"proowner": callee_owner, "prokind": "f", "prosecdef": False,
                           "prorettype": 25, "private_body_sha256": helper_scope.CALLEE_BODY_SHA256,
                           "proconfig": {"is_null": False, "sha256": helper_scope.CALLEE_CONFIG_SHA256,
                                         "utf8_bytes": helper_scope.CALLEE_CONFIG_BYTES},
                           "acl": [[callee_owner, callee_owner, "EXECUTE", False]],
                           "execute_allowed": False}
        if callee_owner is None or len(callees) != 1 or any(callees[0].get(k) != v for k, v in expected_callee.items()):
            raise ReaderAccessError("UNREVIEWED_READER_FUNCTION")
    else:
        helper_scope.require_existing_callee(state)
    return deepcopy(row)


def prepare(state):
    """Use the production planner, preserving the unchanged approved helper row."""
    try:
        if type(state) is not dict or type(state.get("functions")) is not list or any(type(f) is not dict for f in state["functions"]):
            raise ReaderAccessError("INCOMPLETE_READER_ACCESS_PROFILE")
        helper = _existing_helper(state)
        normalized = deepcopy(state)
        if helper is not None:
            normalized["functions"] = [f for f in normalized["functions"]
                                       if f["oid"] != helper["oid"]]
        planned = policy.prepare(normalized)
        reader = next(r["oid"] for r in planned["before"]["roles"]
                      if r["rolname"] == policy.READER)
        for group in ("namespaces", "databases", "functions", "defaults", "relations", "columns"):
            for row in planned["before"][group]:
                if any(atom[1] == reader and atom[3] for atom in row["acl"]):
                    raise ReaderAccessError("UNSAFE_READER_PROFILE")
                if group == "functions" and row["proowner"] == reader:
                    raise ReaderAccessError("UNSAFE_READER_PROFILE")
        if helper is not None:
            for key in ("before", "after"):
                planned[key]["functions"].append(deepcopy(helper))
                planned[key]["functions"].sort(key=lambda row: row["oid"])
            planned["before_sha256"] = policy.fingerprint(planned["before"])
            planned["after_sha256"] = policy.fingerprint(planned["after"])
        if any("FUNCTION" in command for command in planned["sql"]):
            raise ReaderAccessError("UNSAFE_READER_PROFILE")
        return planned
    except (KeyError, TypeError, StopIteration, policy.PolicyError,
            helper_scope.FunctionScopeError):
        raise ReaderAccessError("INCOMPLETE_READER_ACCESS_PROFILE") from None


def verify_selection(conn, selected=RESTRICTED, principal=None):
    """An explicit broad choice is effective only with a matching real database."""
    contract(selected)
    if selected == RESTRICTED:
        return
    identity = conn.execute(
        "SELECT current_database() AS database,current_user AS principal,session_user AS session"
    ).fetchone()
    principal = principal or identity["principal"]
    source_read_expected(principal, selected)
    if (identity["database"] != policy.DATABASE
            or identity["principal"] != identity["session"]
            or identity["principal"] not in (policy.READER, policy.OWNER)):
        raise ReaderAccessError("READER_PROFILE_IDENTITY_MISMATCH")
    planned = prepare(_catalogue(conn))
    if planned["sql"] or any(planned["delta"].values()):
        raise ReaderAccessError("READER_PROFILE_READ_GRANTS_MISSING")


def verify_restricted_catalogue(state):
    """Reject residual broad/current/future capabilities, even core-only state.

    A restricted selection cannot revoke capabilities or pretend broad defaults
    are restricted. Existing invoker helpers are unchanged; no EXECUTE is added.
    """
    try:
        _existing_helper(state, restricted=True)
        reader = next(row for row in state["roles"] if row["rolname"] == policy.READER)
        oid = reader["oid"]
        if any(reader[k] for k in ("rolsuper", "rolinherit", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls")):
            raise ReaderAccessError("READER_PROFILE_SELECTION_CONFLICT")
        if any(row["member"] == oid for row in state["memberships"]):
            raise ReaderAccessError("READER_PROFILE_SELECTION_CONFLICT")
        namespaces = {row["oid"]: row["nspname"] for row in state["namespaces"]}
        for row in state["namespaces"]:
            if row["create_allowed"] or row["owner"] == oid or row["nspname"] not in set(policy.SCHEMAS) | {"public"}:
                raise ReaderAccessError("READER_PROFILE_SELECTION_CONFLICT")
        if any(row["create_allowed"] or row["owner"] == oid for row in state["databases"]):
            raise ReaderAccessError("READER_PROFILE_SELECTION_CONFLICT")
        relations = {row["oid"]: row for row in state["relations"]}
        for row in relations.values():
            full = row["relname"] in RESTRICTED_TABLES.get(row["nspname"], ())
            caps = row["capabilities"]
            if (row["owner"] == oid or (caps is not None and (any(caps[1:3]) or (caps[0] and not full)))
                    or (row["sequence_capabilities"] is not None and any(row["sequence_capabilities"][i] for i in (0, 2)))):
                raise ReaderAccessError("READER_PROFILE_SELECTION_CONFLICT")
        for row in state["columns"]:
            relation = relations[row["oid"]]
            caps = row["capabilities"]
            full = relation["relname"] in RESTRICTED_TABLES.get(relation["nspname"], ())
            column = row["name"] in RESTRICTED_COLUMNS.get((relation["nspname"], relation["relname"]), ())
            if caps is not None and (any(caps[1:]) or (caps[0] and not (full or column))):
                raise ReaderAccessError("READER_PROFILE_SELECTION_CONFLICT")
        for row in state["functions"]:
            helper = (namespaces[row["pronamespace"]], row["proname"], row["proargtypes"]) == ("mf_fact_review", "dependencies", "23")
            if row["proowner"] == oid or (row["prosecdef"] and row["execute_allowed"] and not helper):
                raise ReaderAccessError("READER_PROFILE_SELECTION_CONFLICT")
        for row in state["defaults"]:
            if row["defaclnamespace"] == 0 or row["defaclnamespace"] in namespaces:
                if any(atom[1] in (0, oid) for atom in row["acl"]):
                    raise ReaderAccessError("READER_PROFILE_SELECTION_CONFLICT")
        for group in ("namespaces", "databases", "functions", "relations", "columns"):
            if any(atom[1] == oid and atom[3] for row in state[group] for atom in row["acl"]):
                raise ReaderAccessError("READER_PROFILE_SELECTION_CONFLICT")
    except (KeyError, TypeError, StopIteration):
        raise ReaderAccessError("INCOMPLETE_READER_ACCESS_PROFILE") from None


def _catalogue(conn, *, restricted=False):
    """Fresh effective state plus complete existing-helper declaration seals.

    Reuse the committed metadata-only SQL, not a worker's private executor or
    a fabricated live attestation. OIDs remain the actual database values.
    """
    state = conn.execute(policy.STATE_SQL).fetchone()["value"]
    helper = _existing_helper(state, restricted=restricted)
    if helper is not None:
        functions = [dict(row) for row in conn.execute(helper_closure.FUNCTION_SQL).fetchall()]
        callees = [f for f in functions if f["schema"] == "mf_review" and f["name"] == "canonical_json"]
        if len(callees) != 1:
            raise ReaderAccessError("UNREVIEWED_READER_FUNCTION")
        allowed = conn.execute("SELECT has_function_privilege(%s::oid,%s::oid,'EXECUTE') AS allowed",
                               (helper["proowner"], callees[0]["oid"])).fetchone()["allowed"]
        try:
            helper_closure.verify_profile_helpers(functions, state, restricted=restricted,
                                                   callee_execute_allowed=allowed)
        except helper_closure.FunctionClosureError:
            raise ReaderAccessError("UNREVIEWED_READER_FUNCTION") from None
    return state


def verify_restricted_connection(conn):
    """Complete restricted startup/provisioning gate, with helper attestation.

    Feature stores retain their original local restricted checks. This complete
    catalogue gate runs at the trusted lifespan/composition boundary, not once
    per nested source guard or preservation-table capture.
    """
    verify_restricted_catalogue(_catalogue(conn, restricted=True))


def reconcile(conn, selected=RESTRICTED, *, initialize_roles=()):
    """Additive install composition inside the caller's existing transaction.

    Historical reset SQL is not executed. Unrelated grants are never revoked;
    unsafe pre-existing capabilities fail verification and abort the transaction.
    Defaults cover only present enrolled schemas and objects of mfdata_admin.
    """
    contract(selected)
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise ReaderAccessError("READER_PROFILE_INSTALL_TRANSACTION_REQUIRED")
    identity = conn.execute("SELECT current_user AS principal,session_user AS session").fetchone()
    if (identity["principal"] != identity["session"]
            or identity["principal"] not in ((policy.OWNER,) if selected == BROAD
                                             else (policy.OWNER, "postgres"))):
        raise ReaderAccessError("READER_PROFILE_INSTALL_OWNER_REQUIRED")
    # Validate a broad before-state before proposing any permission mutation.
    if selected == BROAD:
        prepare(_catalogue(conn))
    else:
        verify_restricted_connection(conn)
    # Existing supported principal roles keep their original capabilities. This
    # is additive baseline provisioning, not a role reset or credential rotation.
    from .privileges import verify_principal
    roles = ("mfdata_verified_generator", policy.READER, "mfdata_verified_admin")
    if not set(initialize_roles) <= set(roles):
        raise ReaderAccessError("READER_PROFILE_INSTALL_ROLES_INVALID")
    base_tables = ("funds", "fund_facts", "nav_history", "nav_source_revisions",
                   "dataset_summary", "source_snapshot_runs", "quality_flags",
                   "nav_quality_assessments")
    for role in roles:
        if role != policy.READER and role not in initialize_roles:
            continue
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(conn.info.dbname), sql.Identifier(role)))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA mf,mf_review TO {}").format(sql.Identifier(role)))
        for table in base_tables:
            conn.execute(sql.SQL("GRANT SELECT ON TABLE mf.{} TO {}").format(
                sql.Identifier(table), sql.Identifier(role)))
        conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA mf_review TO {}").format(sql.Identifier(role)))
    if roles[0] in initialize_roles:
        conn.execute("GRANT INSERT ON mf_review.policies,mf_review.snapshots,mf_review.results TO mfdata_verified_generator")
    if roles[2] in initialize_roles:
        conn.execute("GRANT EXECUTE ON FUNCTION mf_review.append_event(integer,text,text,text,text,text,jsonb) TO mfdata_verified_admin")
    if selected == BROAD:
        planned = prepare(_catalogue(conn))
        for statement in planned["sql"]:
            conn.execute(statement)
        actual = prepare(_catalogue(conn))["before"]
        if actual != planned["after"]:
            raise ReaderAccessError("READER_PROFILE_RECONCILIATION_MISMATCH")
        verify_selection(conn, selected, policy.READER)
    else:
        verify_restricted_connection(conn)
    for role in roles:
        verify_principal(conn, "generator" if role == roles[0] else "api", role,
                         reader_profile=selected if role == policy.READER else RESTRICTED)
    return {"profile": selected, "verified_before_commit": True,
            "commit_performed": False, "global_defaults": False,
            "future_schemas_or_other_creators_covered": False}
