"""Provider-free classification; live dependency edges and locks are separate."""

from copy import deepcopy

import pytest

from mfdataindia.review import broad_reader_dependency_scope as scope
from mfdataindia.review import broad_reader_policy as policy


def _seal(value=None):
    return ({"is_null": True, "sha256": None, "utf8_bytes": 0} if value is None
            else {"is_null": False, "sha256": value * 64, "utf8_bytes": 1})


def _case():
    state = {"namespaces": [{"nspname": name} for name in policy.SCHEMAS],
             "relations": [
                 {"oid": 10001, "nspname": "mf", "relname": "fund", "relkind": "r",
                  "owner": 42, "definition": _seal()},
                 {"oid": 10002, "nspname": "mf", "relname": "v_partition_health", "relkind": "v",
                  "owner": 42, "definition": _seal("a")},
                 {"oid": 10003, "nspname": "mf", "relname": "snapshot", "relkind": "m",
                  "owner": 42, "definition": _seal()},
             ]}
    leaves = {name: {"oid": 11000 + i, "nspname": "pg_catalog", "relname": name,
                     "relkind": "r", "owner": 10, "definition": _seal()}
              for i, name in enumerate(sorted(scope.SYSTEM_LEAF_NAMES))}
    candidate = deepcopy(state["relations"])
    candidate[2]["definition"] = _seal("b")
    candidate += deepcopy(list(leaves.values()))
    return state, candidate, leaves


def test_exact_system_leaves_are_lock_only_and_materialized_is_unlocked():
    state, candidate, leaves = _case()
    result = scope.classify(state, candidate, system_leaves=leaves)
    assert [r["oid"] for r in result["grant_roots"]] == [10001, 10002, 10003]
    assert {r["relname"] for r in result["lock_only_leaves"]} == scope.SYSTEM_LEAF_NAMES
    assert [r["oid"] for r in result["materialized_unlocked"]] == [10003]
    assert result["required_locks"] == [
        {"oid": 10001, "mode": "AccessExclusiveLock"},
        {"oid": 10002, "mode": "AccessShareLock"},
        *({"oid": leaves[name]["oid"], "mode": "AccessShareLock"}
          for name in sorted(leaves)),
    ]
    assert all(r["nspname"] != "pg_catalog" for r in result["grant_roots"])
    statements = scope.lock_statements(result)
    assert statements[0] == 'LOCK TABLE ONLY "mf"."fund" IN ACCESS EXCLUSIVE MODE NOWAIT'
    assert statements[1] == 'LOCK TABLE ONLY "mf"."v_partition_health" IN ACCESS SHARE MODE NOWAIT'
    assert len(statements) == 2 and all('"snapshot"' not in item for item in statements)
    assert all("pg_catalog" not in item for item in statements)
    assert all('GRANT' not in item for item in statements)


def test_unusual_catalogue_identifier_is_quoted_not_executed():
    state, candidate, leaves = _case()
    state["relations"][0]["relname"] = 'fund";DROP TABLE public.x;--'
    candidate[0]["relname"] = state["relations"][0]["relname"]
    result = scope.classify(state, candidate, system_leaves=leaves)
    assert scope.lock_statements(result)[0] == (
        'LOCK TABLE ONLY "mf"."fund"";DROP TABLE public.x;--" IN ACCESS EXCLUSIVE MODE NOWAIT'
    )


def test_table_locks_precede_view_locks_even_when_oid_order_is_interleaved():
    state, candidate, leaves = _case()
    state["relations"][0]["oid"] = candidate[0]["oid"] = 10005
    result = scope.classify(state, candidate, system_leaves=leaves)
    tables, views = scope.lock_phases(result)
    assert len(tables) == len(views) == 1
    assert "ACCESS EXCLUSIVE" in tables[0] and "ACCESS SHARE" in views[0]
    assert scope.lock_statements(result) == tables + views


@pytest.mark.parametrize("change", [
    "outside_user", "extra_catalogue", "missing_leaf", "changed_leaf_oid",
    "missing_schema", "missing_root", "duplicate_root", "changed_root", "numeric_boolean",
])
def test_incomplete_or_outside_dependency_closure_fails_closed(change):
    state, candidate, leaves = _case()
    if change == "outside_user":
        candidate.append({"oid": 12000, "nspname": "other", "relname": "user_data",
                          "relkind": "r", "owner": 42, "definition": _seal()})
    elif change == "extra_catalogue":
        candidate.append({"oid": 12000, "nspname": "pg_catalog", "relname": "pg_authid",
                          "relkind": "r", "owner": 10, "definition": _seal()})
    elif change == "missing_leaf":
        candidate.pop()
    elif change == "changed_leaf_oid":
        candidate[-1]["oid"] += 1
    elif change == "missing_schema":
        state["namespaces"].pop()
    elif change == "missing_root":
        candidate.pop(0)
    elif change == "duplicate_root":
        candidate.append(deepcopy(candidate[0]))
    elif change == "numeric_boolean":
        candidate[-1]["definition"]["is_null"] = 1
    else:
        candidate[0]["relname"] = "wrong"
    with pytest.raises(scope.DependencyScopeError, match="UNREVIEWED_READER_DEPENDENCY"):
        scope.classify(state, candidate, system_leaves=leaves)
