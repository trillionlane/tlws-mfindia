"""Adversarial provider-free checks for the two-backend lock protocol."""

from copy import deepcopy

import pytest

from mfdataindia.review import broad_reader_lock_witness as witness


def _case():
    rows = [
        {"oid": 10, "nspname": "mf", "relname": "nav_history", "relkind": "p"},
        {"oid": 11, "nspname": "mf", "relname": "nav_history_2026", "relkind": "r"},
        {"oid": 20, "nspname": "mf", "relname": "v_partition_health", "relkind": "v"},
        {"oid": 30, "nspname": "mf", "relname": "snapshot", "relkind": "m"},
    ]
    leaves = [{"oid": 40, "nspname": "pg_catalog", "relname": "pg_class", "relkind": "r"},
              {"oid": 41, "nspname": "pg_catalog", "relname": "pg_inherits", "relkind": "r"},
              {"oid": 42, "nspname": "pg_catalog", "relname": "pg_namespace", "relkind": "r"}]
    classification = {"grant_roots": rows, "lock_only_leaves": leaves,
                      "materialized_unlocked": [rows[3]],
                      "required_locks": [{"oid": oid, "mode": mode} for oid, mode in [
                          (10, "AccessExclusiveLock"), (11, "AccessExclusiveLock"),
                          (20, "AccessShareLock"), (40, "AccessShareLock"),
                          (41, "AccessShareLock"), (42, "AccessShareLock")]]}

    def lock(row, mode):
        return {"pid": 500, "database": 100, **row, "mode": mode, "granted": True}

    tables = [lock(row, "AccessExclusiveLock") for row in rows[:2]]
    views = tables + [lock(row, "AccessShareLock") for row in [*rows[:2], rows[2], *leaves]]
    views.sort(key=lambda row: (row["oid"], row["mode"]))
    return classification, [], tables, views


def _verify(classification, baseline, tables, views):
    return witness.verify_phases(classification, baseline=baseline, tables=tables, views=views,
                                 target_pid=500, observer_pid=501, database_oid=100)


def test_valid_phases_preserve_raw_overlap_and_never_claim_per_view_origin():
    classification, baseline, tables, views = _case()
    result = _verify(classification, baseline, tables, views)
    assert result["effective"] == [
        {"oid": 10, "mode": "AccessExclusiveLock"},
        {"oid": 11, "mode": "AccessExclusiveLock"},
        {"oid": 20, "mode": "AccessShareLock"},
        {"oid": 40, "mode": "AccessShareLock"},
        {"oid": 41, "mode": "AccessShareLock"},
        {"oid": 42, "mode": "AccessShareLock"},
    ]
    assert len(result["views"]) == 8 and result["grant_authorized"] is False
    assert result["per_view_provenance_claim"] is False


@pytest.mark.parametrize("defect", [
    "baseline_lock", "observer_is_target", "wrong_pid", "wrong_database", "ungranted",
    "phase1_catalog", "missing_table", "phase2_missing_leaf", "phase2_extra_outside",
    "phase2_weak_table", "duplicate_mode", "materialized_lock", "changed_identity",
])
def test_incomplete_or_fabricated_phase_witness_fails_closed(defect):
    classification, baseline, tables, views = _case()
    classification, baseline, tables, views = deepcopy((classification, baseline, tables, views))
    observer = 501
    if defect == "baseline_lock":
        baseline.append(deepcopy(tables[0]))
    elif defect == "observer_is_target":
        observer = 500
    elif defect == "wrong_pid":
        views[0]["pid"] = 501
    elif defect == "wrong_database":
        views[0]["database"] = 999
    elif defect == "ungranted":
        views[0]["granted"] = False
    elif defect == "phase1_catalog":
        tables.append(deepcopy(views[-1]))
    elif defect == "missing_table":
        tables.pop(0)
    elif defect == "phase2_missing_leaf":
        views.pop()
    elif defect == "phase2_extra_outside":
        views.append({"pid": 500, "database": 100, "oid": 99, "nspname": "public",
                      "relname": "unreviewed", "relkind": "r", "mode": "AccessShareLock",
                      "granted": True})
    elif defect == "phase2_weak_table":
        views = [r for r in views if not (r["oid"] == 10 and r["mode"] == "AccessExclusiveLock")]
    elif defect == "duplicate_mode":
        views.append(deepcopy(views[-1]))
    elif defect == "materialized_lock":
        views.append({"pid": 500, "database": 100, "oid": 30, "nspname": "mf",
                      "relname": "snapshot", "relkind": "m", "mode": "AccessShareLock",
                      "granted": True})
    else:
        views[0]["relname"] = "changed"
    views.sort(key=lambda row: (row["oid"], row["mode"]))
    with pytest.raises(witness.LockWitnessError, match="UNPROVEN_BROAD_READER_LOCK_CLOSURE"):
        witness.verify_phases(classification, baseline=baseline, tables=tables, views=views,
                              target_pid=500, observer_pid=observer, database_oid=100)


def test_observer_sql_filters_target_pid_not_own_backend():
    assert "l.pid=%s" in witness.LOCK_WITNESS_SQL
    assert "l.pid=pg_backend_pid()" not in witness.LOCK_WITNESS_SQL
    assert "LEFT JOIN" in witness.LOCK_WITNESS_SQL
