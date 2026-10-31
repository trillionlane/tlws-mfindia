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
from .reader_function_attestation_inventory import ATTESTATION_SQL

RESTRICTED = "mf-review-restricted-read-v1"
BROAD = "mf-fund-research-read-v1"


class ReaderAccessError(ValueError):
    """Stable, value-free profile/configuration failure."""


def contract(selected=RESTRICTED):
    if type(selected) is not str or selected not in (RESTRICTED, BROAD):
        raise ReaderAccessError("INVALID_READER_ACCESS_PROFILE")
    if selected == BROAD:
        return policy.profile()
    return {"format": RESTRICTED, "reader": policy.READER,
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


def _existing_helper(state):
    """Retain the reviewed helper only; never propose additional EXECUTE.

    This checks real catalogue OIDs and the committed body/declaration seals.
    It does not manufacture a live capture receipt or invoke the helper.
    """
    privileged = [f for f in state["functions"]
                  if f["prosecdef"] and f["execute_allowed"]]
    if not privileged:
        return None
    roles = {r["rolname"]: r["oid"] for r in state["roles"]}
    namespaces = {n["nspname"]: n["oid"] for n in state["namespaces"]}
    expected_roles = (policy.OWNER, policy.READER, "mfdata_verified_admin")
    if len(privileged) != 1 or any(name not in roles for name in expected_roles):
        raise ReaderAccessError("UNREVIEWED_READER_FUNCTION")
    row = privileged[0]
    expected = {
        "pronamespace": namespaces.get("mf_fact_review"), "proname": "dependencies",
        "proowner": roles[policy.OWNER], "prosecdef": True, "prokind": "f",
        "proargtypes": "23", "prorettype": 3802,
        "private_body_sha256": helper_reference.BODY_SHA256,
        "proconfig": {"is_null": False,
                      "sha256": helper_reference.EXPECTED_DECLARATION["configuration_sha256"],
                      "utf8_bytes": helper_reference.EXPECTED_DECLARATION["configuration_bytes"]},
        "execute_allowed": True,
        "acl": sorted([[roles[policy.OWNER], roles[name], "EXECUTE", False]
                       for name in expected_roles]),
    }
    if any(row.get(key) != value for key, value in expected.items()):
        raise ReaderAccessError("UNREVIEWED_READER_FUNCTION")
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


def _catalogue(conn):
    """Fresh effective state plus complete existing-helper declaration seals.

    Reuse the committed metadata-only SQL, not a worker's private executor or
    a fabricated live attestation. OIDs remain the actual database values.
    """
    state = conn.execute(policy.STATE_SQL).fetchone()["value"]
    helper = _existing_helper(state)
    if helper is not None:
        functions = conn.execute(ATTESTATION_SQL).fetchone()["value"]["functions"]
        if (len(functions) != 1 or functions[0]["oid"] != helper["oid"]
                or not helper_reference.comparison(functions[0])[
                    "declaration_matches_committed_reference"]):
            raise ReaderAccessError("UNREVIEWED_READER_FUNCTION")
    return state


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
        for relation in ("mf_monthly_facts.sources", "mf_book_facts.sources",
                         "mf_multi_facts.sources", "mf_fact_refresh.catalogs",
                         "mf_portfolios.sources", "mf_distributions.sources",
                         "mf_idcw.sources", "mf_enrichment.blobs"):
            if conn.execute("SELECT to_regclass(%s) AS relation", (relation,)).fetchone()["relation"]:
                if conn.execute("SELECT has_column_privilege(%s,%s,'content','SELECT') AS allowed",
                                (policy.READER, relation)).fetchone()["allowed"]:
                    raise ReaderAccessError("READER_PROFILE_SELECTION_CONFLICT")
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
    for role in roles:
        verify_principal(conn, "generator" if role == roles[0] else "api", role,
                         reader_profile=selected if role == policy.READER else RESTRICTED)
    return {"profile": selected, "verified_before_commit": True,
            "commit_performed": False, "global_defaults": False,
            "future_schemas_or_other_creators_covered": False}
