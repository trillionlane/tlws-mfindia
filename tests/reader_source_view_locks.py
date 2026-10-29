"""Disposable-CI view-lock observations; never live grant or lock authority.

The target backend does no catalogue read before LOCK. A second backend
observes only the target PID, so its own pg_locks/catalogue reads cannot mask
the target's implicit recursive locks on pinned system relations.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from mfdataindia.review import broad_reader_policy
import reader_source_reference as legacy
import reader_source_variants as variants


MARKER = "SOURCE_VIEW_LOCK_CANDIDATE_V1:"
FORMAT = "ci-source-view-recursive-lock-candidate-not-live-acceptance-v1"
LOCK_SQL = """SELECT l.database::bigint AS database_oid,
    l.relation::bigint AS oid, l.mode, l.granted,
    n.nspname, c.relname, c.relkind::text AS relkind,
    c.relowner::bigint AS owner_oid
FROM pg_catalog.pg_locks AS l
LEFT JOIN pg_catalog.pg_class AS c ON c.oid=l.relation
LEFT JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace
WHERE l.pid=%s AND l.locktype='relation'
  AND l.database IN (%s::oid, 0::oid)
ORDER BY l.database, l.relation, l.mode, c.relkind"""
LOCK_KEYS = {"database_oid", "oid", "mode", "granted", "nspname", "relname",
             "relkind", "owner_oid"}
REVIEWED_CATALOG_LEAVES = frozenset({
    ("pg_catalog", "pg_class"),
    ("pg_catalog", "pg_inherits"),
    ("pg_catalog", "pg_namespace"),
})


def _require(value):
    if not value:
        raise ValueError("INCOMPLETE_CI_VIEW_LOCK_CANDIDATE")


def _locks(observer, pid, database_oid):
    with observer.execute(LOCK_SQL, (pid, database_oid), prepare=False) as cursor:
        rows = [dict(row) for row in cursor.fetchall()]
    for row in rows:
        _require(set(row) == LOCK_KEYS and row["granted"] is True)
        _require(type(row["database_oid"]) is int and row["database_oid"] in (0, database_oid))
        _require(type(row["oid"]) is int and row["oid"] > 0)
        _require(type(row["nspname"]) is str and type(row["relname"]) is str)
        _require(type(row["relkind"]) is str and len(row["relkind"]) == 1)
        _require(type(row["owner_oid"]) is int and row["owner_oid"] > 0)
        _require(type(row["mode"]) is str and row["mode"] in
                 {"AccessShareLock", "AccessExclusiveLock"})
    _require(rows == sorted(rows, key=lambda r: (r["database_oid"], r["oid"],
                                                 r["mode"], r["relkind"])))
    return rows


def _view_seal(observer, oid):
    with observer.execute(
        "SELECT pg_catalog.pg_get_viewdef(%s::oid, false) AS definition",
        (oid,), prepare=False,
    ) as cursor:
        rows = cursor.fetchall()
    _require(len(rows) == 1 and type(rows[0]["definition"]) is str)
    raw = rows[0]["definition"].encode("utf-8")
    return {"is_null": False, "sha256": hashlib.sha256(raw).hexdigest(),
            "utf8_bytes": len(raw)}


def require_source_outside_scope(observation, *, partition_health=False):
    """Reject every non-source outside relation without filtering its row."""
    _require(type(observation) is dict and type(observation.get("observed_lock_delta")) is list)
    outside = {(r["nspname"], r["relname"])
               for r in observation["observed_lock_delta"]
               if r["nspname"] not in broad_reader_policy.SCHEMAS}
    _require(outside <= REVIEWED_CATALOG_LEAVES)
    if partition_health:
        _require(outside == REVIEWED_CATALOG_LEAVES)
    return outside


def observe_view(dsn, observer, *, database_oid, view, expected_seal):
    """One new pristine target backend, one NOWAIT view lock, confirmed ROLLBACK."""
    _require(type(view) is dict and set(view) == {"oid", "nspname", "relname", "relkind"})
    _require(view["relkind"] in ("v", "m") and type(view["oid"]) is int)
    observed_seal = _view_seal(observer, view["oid"])
    _require(observed_seal == expected_seal)
    target = psycopg.connect(dsn, autocommit=True, connect_timeout=5,
                             row_factory=dict_row)
    rolled_back = False
    try:
        pid = target.info.backend_pid  # Protocol metadata, not target SQL.
        _require(type(pid) is int and pid > 0)
        baseline = _locks(observer, pid, database_oid)
        _require(baseline == [])
        target.execute("BEGIN", prepare=False)
        target.execute(
            sql.SQL("LOCK TABLE ONLY {}.{} IN ACCESS SHARE MODE NOWAIT").format(
                sql.Identifier(view["nspname"]), sql.Identifier(view["relname"])
            ), prepare=False,
        )
        after = _locks(observer, pid, database_oid)
        _require(any(row["oid"] == view["oid"] and
                     row["mode"] == "AccessShareLock" for row in after))
        target.execute("ROLLBACK", prepare=False)
        rolled_back = True
        _require(_locks(observer, pid, database_oid) == [])
    finally:
        target.close()
    _require(rolled_back)
    return {"target_view": deepcopy(view),
            "expected_view_definition_seal": deepcopy(expected_seal),
            "observed_view_definition_seal": observed_seal,
            "target_backend_pid": pid,
            "target_database_oid": database_oid,
            "target_prelock_relation_baseline": baseline,
            "observed_lock_delta": after,
            "source_declared_recursive_closure": None,
            "reviewed_observation_only": None,
            "classification_complete": False,
            "rollback_confirmed": True}


def observe_profile(dsn, payload, *, expected_git):
    """Observe every exact source-profile view, preserving CI OIDs as evidence."""
    _require(type(payload) is dict and type(expected_git) is str and len(expected_git) == 40)
    profile = payload.get("profile")
    _require(profile in ("BASE44_EXTENDED", "BASE44_BOOK_V2_SINGLE_PASS"))
    _require(payload.get("compiler_git") == expected_git)
    _require(payload.get("base_authority_sha256") == legacy.AUTHORITY_SHA)
    _require(payload.get("variant_authority_sha256") == variants.AUTHORITY_SHA)
    _require(payload.get("authority") == variants.variant_authority(Path(__file__).resolve().parents[1]))
    _require(payload.get("runtime_authority") is payload.get("permission_acceptance") is False)
    _require(type(payload.get("raw")) is dict and type(payload["raw"].get("legacy")) is dict)
    _require(payload.get("raw_sha256") == legacy.digest(legacy.canonical(payload["raw"]).encode()))
    raw = payload["raw"]["legacy"]
    authority = payload["authority"]
    migration_pins = [{"path": row["path"], "sha256": row["sha256"]}
                      for row in authority["base"]["files"]]
    _require(len(migration_pins) == 44)
    variant_pins = [{"path": row["path"], "sha256": row["sha256"]}
                    for row in authority["variant"]["files"]]
    _require(len(variant_pins) == 9)
    namespaces = {o["oid"]: o["row"]["nspname"] for o in raw["objects"]
                  if o["catalog"] == "pg_namespace"}
    views = []
    for obj in raw["objects"]:
        if obj["catalog"] != "pg_class" or obj["row"]["relkind"] not in ("v", "m"):
            continue
        row = obj["row"]
        views.append(({"oid": obj["oid"], "nspname": namespaces[row["relnamespace"]],
                       "relname": row["relname"], "relkind": row["relkind"]},
                      row["view_definition_seal"]))
    _require(len(views) == 15 and all(view["relkind"] == "v" for view, _ in views))
    views.sort(key=lambda item: (item[0]["nspname"], item[0]["relname"], item[0]["oid"]))
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5,
                         row_factory=dict_row) as observer:
        database_oid = observer.execute(
            "SELECT oid::bigint AS oid FROM pg_catalog.pg_database "
            "WHERE datname=pg_catalog.current_database()", prepare=False
        ).fetchone()["oid"]
        _require(type(database_oid) is int and database_oid > 0)
        results = [observe_view(dsn, observer, database_oid=database_oid,
                                view=view, expected_seal=seal)
                   for view, seal in views]
    for row in results:
        target = row["target_view"]
        require_source_outside_scope(
            row, partition_health=(target["nspname"], target["relname"])
            == ("mf", "v_partition_health"))
    result = {"format": FORMAT, "profile": profile, "compiler_git": expected_git,
              "source_reference_sha256": legacy.digest(legacy.canonical(payload).encode()),
              "source_reference_raw_sha256": payload["raw_sha256"],
              "base_authority_sha256": payload["base_authority_sha256"],
              "variant_authority_sha256": payload["variant_authority_sha256"],
              "compiler_sources": deepcopy(payload["compiler_sources"]),
              "migration_source_pins": migration_pins,
              "migration_source_pins_sha256": legacy.digest(legacy.canonical(migration_pins).encode()),
              "variant_source_pins": variant_pins,
              "variant_source_pins_sha256": legacy.digest(legacy.canonical(variant_pins).encode()),
              "database_oid": database_oid,
              "ordinary_views": len(results), "materialized_views": 0,
              "observations": results, "source_only": True,
              "permission_acceptance": False, "runtime_authority": False}
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    _require(0 < len(encoded) <= 60000)
    return result
