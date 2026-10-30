"""Pure phase-bound verification of a DEV broad-reader relation lock witness.

The target backend must perform no catalogue SELECT between a pristine
post-rollback baseline and the two reviewed LOCK phases. A separate read-only
observer obtains these snapshots by target PID. This module neither connects
to PostgreSQL nor authorizes a grant.
"""

from copy import deepcopy

from mfdataindia.review import broad_reader_policy as policy


LOCK_WITNESS_SQL = """SELECT l.pid::bigint AS pid,l.database::bigint AS database,
l.relation::bigint AS oid,n.nspname,c.relname,c.relkind::text AS relkind,
l.mode,l.granted
FROM pg_catalog.pg_locks l
LEFT JOIN pg_catalog.pg_class c ON c.oid=l.relation
LEFT JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE l.pid=%s AND l.locktype='relation'
ORDER BY l.relation,l.mode,l.granted"""

_ROW_KEYS = frozenset({"pid", "database", "oid", "nspname", "relname",
                       "relkind", "mode", "granted"})
_MODES = {"AccessExclusiveLock": 2, "AccessShareLock": 1}


class LockWitnessError(ValueError):
    """A missing, excess, weaker, ambiguous, or unbound relation lock."""


def _require(condition):
    if not condition:
        raise LockWitnessError("UNPROVEN_BROAD_READER_LOCK_CLOSURE")


def _snapshot(rows, *, target_pid, database_oid):
    _require(type(rows) is list)
    result = {}
    for row in rows:
        _require(type(row) is dict and set(row) == _ROW_KEYS)
        _require(type(row["pid"]) is int and row["pid"] == target_pid)
        _require(type(row["database"]) is int and row["database"] == database_oid)
        _require(type(row["oid"]) is int and row["oid"] > 0)
        _require(type(row["nspname"]) is str and type(row["relname"]) is str)
        _require(row["relkind"] in ("r", "p", "v"))
        _require(row["mode"] in _MODES and row["granted"] is True)
        key = (row["oid"], row["mode"])
        _require(key not in result)
        result[key] = row
    _require(rows == sorted(rows, key=lambda row: (row["oid"], row["mode"])))
    return result


def verify_phases(classification, *, baseline, tables, views,
                  target_pid, observer_pid, database_oid):
    """Require the exact phase growth and strongest complete final protection.

    This proves aggregate phase additions, not per-view provenance. Separate
    source and live dependency checks must establish the proposed closure.
    """
    _require(type(target_pid) is int and target_pid > 0)
    _require(type(observer_pid) is int and observer_pid > 0 and observer_pid != target_pid)
    _require(type(database_oid) is int and database_oid > 0)
    _require(type(classification) is dict)
    roots = classification.get("grant_roots")
    leaves = classification.get("lock_only_leaves")
    unlocked = classification.get("materialized_unlocked")
    _require(type(roots) is list and type(leaves) is list and type(unlocked) is list)
    identities = {}
    for row in roots + leaves:
        _require(type(row) is dict)
        oid = row.get("oid")
        _require(type(oid) is int and oid > 0 and oid not in identities)
        _require(type(row.get("nspname")) is str
                 and type(row.get("relname")) is str
                 and row.get("relkind") in ("r", "p", "v", "m"))
        identities[oid] = row
    _require(all(r["nspname"] in policy.SCHEMAS for r in roots))
    _require({r["relname"] for r in leaves} == {"pg_class", "pg_inherits", "pg_namespace"}
             and all(r["nspname"] == "pg_catalog" and r["relkind"] == "r" for r in leaves))
    _require({r["oid"] for r in unlocked} ==
             {r["oid"] for r in roots if r["relkind"] == "m"})
    phase1_expected = {(r["oid"], "AccessExclusiveLock")
                       for r in roots if r["relkind"] in ("r", "p")}
    phase2_new = {(r["oid"], "AccessShareLock")
                  for r in roots if r["relkind"] == "v"}
    phase2_new |= {(r["oid"], "AccessShareLock") for r in leaves}
    _require(phase1_expected and phase2_new)
    required = classification.get("required_locks")
    _require(required == [{"oid": oid, "mode": mode}
                          for oid, mode in sorted(phase1_expected | phase2_new)])
    first = _snapshot(baseline, target_pid=target_pid, database_oid=database_oid)
    second = _snapshot(tables, target_pid=target_pid, database_oid=database_oid)
    third = _snapshot(views, target_pid=target_pid, database_oid=database_oid)
    _require(not first and set(second) == phase1_expected)
    overlap = {(r["oid"], "AccessShareLock")
               for r in roots if r["relkind"] in ("r", "p")}
    _require(phase1_expected | phase2_new <= set(third)
             and set(third) <= phase1_expected | phase2_new | overlap)
    _require(set(third) - set(second) >= phase2_new)
    for row in tables + views:
        identity = identities.get(row["oid"])
        _require(identity is not None)
        _require(all(row[k] == identity[k] for k in ("nspname", "relname", "relkind")))
    effective = {}
    for oid, mode in third:
        if oid not in effective or _MODES[mode] > _MODES[effective[oid]]:
            effective[oid] = mode
    expected_effective = {oid: mode for oid, mode in phase1_expected | phase2_new}
    _require(effective == expected_effective)
    return {"format": "mf-broad-reader-lock-phases-v1-nonexecutable",
            "target_pid": target_pid, "observer_pid": observer_pid,
            "database_oid": database_oid,
            "baseline": deepcopy(baseline), "tables": deepcopy(tables), "views": deepcopy(views),
            "phase_2_added": deepcopy([row for key, row in third.items() if key not in second]),
            "effective": [{"oid": oid, "mode": mode}
                          for oid, mode in sorted(effective.items())],
            "per_view_provenance_claim": False, "grant_authorized": False}
