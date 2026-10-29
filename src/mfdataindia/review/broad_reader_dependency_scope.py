"""Pure classification of broad-reader grant roots and relation dependencies.

This is not a grant path.  In particular, an outside relation may need a
read lock to stabilize a view, but it can never become a SELECT target.
"""

from copy import deepcopy
import re

from psycopg import sql

from mfdataindia.review import broad_reader_policy as policy


SYSTEM_LEAF_NAMES = frozenset({"pg_class", "pg_inherits", "pg_namespace"})
SYSTEM_NAMESPACE = "pg_catalog"
SYSTEM_LEAVES_SQL = """SELECT c.oid::bigint AS oid,n.nspname,c.relname,
c.relkind::text AS relkind,c.relowner::bigint AS owner,
jsonb_build_object('is_null',true,'sha256',null,'utf8_bytes',0) AS definition
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='pg_catalog'
AND c.relname IN ('pg_class','pg_inherits','pg_namespace')
ORDER BY c.oid"""
_CANDIDATE_KEYS = frozenset({"oid", "nspname", "relname", "relkind", "owner", "definition"})


class DependencyScopeError(ValueError):
    """An incomplete or unreviewed relation closure."""


def _require(condition):
    if not condition:
        raise DependencyScopeError("UNREVIEWED_READER_DEPENDENCY")


def _definition(value, *, is_null):
    _require(type(value) is dict and set(value) == {"is_null", "sha256", "utf8_bytes"})
    _require(type(value["is_null"]) is bool and value["is_null"] is is_null)
    _require(type(value["utf8_bytes"]) is int)
    if is_null:
        _require(value["sha256"] is None and value["utf8_bytes"] == 0)
    else:
        _require(type(value["sha256"]) is str
                 and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None
                 and value["utf8_bytes"] > 0)


def classify(state, candidates, *, system_leaves):
    """Return separate grant roots and lock-only leaves with required modes.

    ``system_leaves`` must be an exact, independently reviewed identity map.
    It is deliberately not inferred from a candidate's self-reported name.
    The caller must separately attest the dependency edges and actual locks.
    """
    _require(type(state) is dict and type(candidates) is list)
    _require(type(system_leaves) is dict and set(system_leaves) == SYSTEM_LEAF_NAMES)
    namespaces = state.get("namespaces")
    relations = state.get("relations")
    _require(type(namespaces) is list and type(relations) is list)
    _require({n.get("nspname") for n in namespaces if type(n) is dict} >= set(policy.SCHEMAS))

    roots = {}
    for row in relations:
        _require(type(row) is dict)
        if row.get("relkind") not in ("r", "p", "v", "m"):
            continue
        _require(row.get("nspname") in policy.SCHEMAS)
        oid = row.get("oid")
        _require(type(oid) is int and oid > 0 and oid not in roots)
        roots[oid] = row

    expected_leaves = {}
    for name, identity in system_leaves.items():
        _require(type(identity) is dict and set(identity) == _CANDIDATE_KEYS)
        _require(identity["nspname"] == SYSTEM_NAMESPACE and identity["relname"] == name)
        _require(identity["relkind"] == "r")
        _require(type(identity["oid"]) is int and identity["oid"] > 0)
        _require(type(identity["owner"]) is int and identity["owner"] > 0)
        _definition(identity["definition"], is_null=True)
        _require(identity["oid"] not in expected_leaves and identity["oid"] not in roots)
        expected_leaves[identity["oid"]] = identity

    observed = {}
    for row in candidates:
        _require(type(row) is dict and set(row) == _CANDIDATE_KEYS)
        oid = row["oid"]
        _require(type(oid) is int and oid > 0 and oid not in observed)
        _require(type(row["nspname"]) is str and type(row["relname"]) is str)
        _require(type(row["owner"]) is int and row["owner"] > 0)
        if oid in roots:
            root = roots[oid]
            _require(all(row[k] == root[k] for k in ("oid", "nspname", "relname", "relkind", "owner")))
            if row["relkind"] != "m":
                _definition(row["definition"], is_null=row["relkind"] not in ("v", "m"))
                _require(row["definition"] == root["definition"])
            else:
                _definition(row["definition"], is_null=False)
        else:
            _definition(row["definition"], is_null=True)
            _require(oid in expected_leaves and row == expected_leaves[oid])
        observed[oid] = row

    _require(set(observed) == set(roots) | set(expected_leaves))
    locks = []
    for oid, row in sorted(observed.items()):
        mode = ("AccessExclusiveLock" if row["relkind"] in ("r", "p") and oid in roots
                else "AccessShareLock")
        if oid in roots and row["relkind"] == "m":
            continue  # Explicitly unlocked; the caller must record this fact.
        locks.append({"oid": oid, "mode": mode})
    return {"grant_roots": deepcopy([observed[oid] for oid in sorted(roots)]),
            "lock_only_leaves": deepcopy([observed[oid] for oid in sorted(expected_leaves)]),
            "materialized_unlocked": deepcopy([observed[oid] for oid in sorted(roots)
                                                if observed[oid]["relkind"] == "m"]),
            "required_locks": locks}


def lock_phases(classification):
    """Render the exact ordered table phase, then ordinary-view phase.

    Catalog relations may appear in the actual recursive view-lock witness,
    but are never explicit LOCK targets or SELECT grant targets.
    """
    _require(type(classification) is dict)
    roots = classification.get("grant_roots")
    leaves = classification.get("lock_only_leaves")
    required = classification.get("required_locks")
    _require(type(roots) is list and type(leaves) is list and type(required) is list)
    by_oid = {r["oid"]: r for r in roots + leaves}
    _require(len(by_oid) == len(roots) + len(leaves))
    root_oids = {row["oid"] for row in roots}
    tables, views = [], []
    for lock in required:
        _require(type(lock) is dict and set(lock) == {"oid", "mode"})
        row = by_oid.get(lock["oid"])
        _require(row is not None)
        if lock["oid"] not in root_oids:
            _require(row["nspname"] == SYSTEM_NAMESPACE
                     and row["relname"] in SYSTEM_LEAF_NAMES
                     and lock["mode"] == "AccessShareLock")
            continue
        mode = ("ACCESS EXCLUSIVE" if lock["mode"] == "AccessExclusiveLock"
                else "ACCESS SHARE" if lock["mode"] == "AccessShareLock" else None)
        _require(mode is not None)
        statement = sql.SQL("LOCK TABLE ONLY {}.{} IN " + mode + " MODE NOWAIT").format(
            sql.Identifier(row["nspname"]), sql.Identifier(row["relname"])).as_string()
        if lock["mode"] == "AccessExclusiveLock":
            _require(row["relkind"] in ("r", "p"))
            tables.append(statement)
        else:
            _require(row["relkind"] == "v")
            views.append(statement)
    _require(tables and views)
    return tables, views


def lock_statements(classification):
    """Compatibility projection of the exact two-phase target SQL only."""
    tables, views = lock_phases(classification)
    return tables + views
