"""Provider-free exact helper/callee declaration and ACL gate."""

from copy import deepcopy

import pytest

from mfdataindia.review import broad_reader_function_closure as closure
from test_broad_reader_function_scope import _case as function_case


def _case():
    state, _ = function_case()
    rows = []
    for item in state["functions"]:
        helper = item["proname"] == "dependencies"
        expected = closure._EXPECTED[("mf_fact_review" if helper else "mf_review",
                                      item["proname"])]
        rows.append({
            "oid": item["oid"], "namespace_oid": item["pronamespace"],
            "schema": "mf_fact_review" if helper else "mf_review", "name": item["proname"],
            "owner_oid": item["proowner"], "kind": "f",
            "argument_oids": expected["argument_oids"], "return_oid": expected["return_oid"],
            "language": "plpgsql", "language_trusted": True,
            "cost": "100", "rows": "0", "variadic_oid": 0, "support_oid": 0,
            "leakproof": False, "all_arguments_null": True,
            "argument_modes_null": True, "transform_types_null": True,
            "argument_defaults_null": True, "binary_null": True,
            "sql_body_null": True,
            "argument_names": {"is_null": False,
                               "sha256": expected["argument_names"][0],
                               "utf8_bytes": expected["argument_names"][1]},
            "security_definer": helper,
            "volatility": expected["volatility"], "strict": expected["strict"],
            "parallel": "u", "returns_set": False, "argument_count": 1,
            "argument_default_count": 0,
            "body": {"is_null": False, "sha256": expected["body"][0],
                     "utf8_bytes": expected["body"][1]},
            "definition": {"is_null": False, "sha256": expected["definition"][0],
                           "utf8_bytes": expected["definition"][1]},
            "configuration": {"is_null": False, "sha256": expected["configuration"][0],
                              "utf8_bytes": expected["configuration"][1]},
            "acl": deepcopy(item["acl"]), "reader_execute": helper,
            "reader_schema_usage": helper,
        })
    return state, rows


def test_exact_full_seals_and_acl_bind_to_live_state_without_invocation():
    state, rows = _case()
    result = closure.verify(rows, state)
    assert len(result["functions"]) == 2
    assert result["function_invoked"] is False and result["grant_authorized"] is False
    assert len(result["source_reviewed_call_graph"]) == 2


def test_callee_broad_seal_requires_same_full_source_catalog_bytes():
    state, rows = _case()
    assert rows[1]["configuration"] == state["functions"][1]["proconfig"]
    closure.verify(rows, state)
    state["functions"][1]["proconfig"] = {
        "is_null": False,
        "sha256": "a2cac24529b7f82b6e5a6c1574baf25bcd24678b648faca2b3da9869f6925c46",
        "utf8_bytes": 36,
    }
    with pytest.raises(closure.FunctionClosureError,
                       match="UNREVIEWED_BROAD_READER_FUNCTION_CLOSURE"):
        closure.verify(rows, state)


@pytest.mark.parametrize("defect", [
    "body", "definition", "config", "public", "extra", "oid", "execute", "schema",
    "overload", "other_definer", "state_drift",
])
def test_function_closure_fails_on_unreviewed_or_incomplete_source(defect):
    state, rows = _case()
    if defect == "body":
        rows[0]["body"]["sha256"] = "0" * 64
    elif defect == "definition":
        rows[1]["definition"]["sha256"] = "0" * 64
    elif defect == "config":
        rows[1]["configuration"]["sha256"] = "0" * 64
    elif defect == "public":
        rows[0]["acl"].append([16587, 0, "EXECUTE", False])
    elif defect == "extra":
        rows.append(deepcopy(rows[1]))
    elif defect == "oid":
        rows[0]["oid"] += 1
    elif defect == "execute":
        rows[1]["reader_execute"] = True
    elif defect == "schema":
        rows[0]["reader_schema_usage"] = False
    elif defect == "overload":
        overload = deepcopy(state["functions"][0])
        overload["oid"] += 1
        state["functions"].append(overload)
    elif defect == "other_definer":
        other = deepcopy(state["functions"][0])
        other["oid"] += 1
        other["proname"] = "other"
        state["functions"].append(other)
    else:
        state["functions"][0]["acl"].append([16587, 0, "EXECUTE", False])
    with pytest.raises(closure.FunctionClosureError,
                       match="UNREVIEWED_BROAD_READER_FUNCTION_CLOSURE"):
        closure.verify(rows, state)


def test_fixed_query_seals_without_function_execution_or_private_values():
    statement = closure.FUNCTION_SQL
    assert "pg_get_functiondef(p.oid)" in statement
    assert "p.prosrc,'UTF8'" in statement
    assert "has_function_privilege('mfdata_verified_reader'" in statement
    assert "SELECT p.oid" in statement and "FROM pg_catalog.pg_proc p" in statement
    assert "SELECT mf_fact_review.dependencies" not in statement
