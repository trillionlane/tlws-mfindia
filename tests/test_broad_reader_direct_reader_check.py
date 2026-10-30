"""Pure exact shared-reader capability check, with no business-row export."""

from copy import deepcopy

import pytest

from mfdataindia.review import broad_reader_direct_reader_check as check
from mfdataindia.review import broad_reader_policy as policy


def _case():
    planned = {"namespaces": [
        {"oid": 100 + i, "nspname": name} for i, name in enumerate(policy.SCHEMAS)],
        "relations": [{"oid": 900, "nspname": "mf", "relname": "funds",
                       "relkind": "r"}],
        "columns": [{"oid": 900, "attnum": 1, "name": "amfi_scheme_code",
                     "dropped": False}]}
    context = {"database": policy.DATABASE, "principal": policy.READER,
               "session": policy.READER, "in_recovery": False,
               "readonly": "on", "isolation": "read committed"}
    namespaces = [{"oid": row["oid"], "nspname": row["nspname"],
                   "usage": True, "create_allowed": False}
                  for row in planned["namespaces"]]
    relations = [{"oid": 900, "nspname": "mf", "relname": "funds",
                  "relkind": "r", "select_allowed": True,
                  **dict.fromkeys(("insert_allowed", "update_allowed", "delete_allowed",
                                   "truncate_allowed", "references_allowed",
                                   "trigger_allowed"), False)}]
    columns = [{"oid": 900, "attnum": 1, "attname": "amfi_scheme_code",
                "select_allowed": True, "insert_allowed": False,
                "update_allowed": False, "references_allowed": False}]
    return context, namespaces, relations, columns, planned


def test_all_sixteen_and_every_relation_and_column_are_exact():
    case = _case()
    result = check.validate(*case[:4], planned_after=case[4])
    assert result["permission_enabled"] is result["ti_verified"] is False
    assert len(result["namespaces"]) == 16


@pytest.mark.parametrize("case", [
    "wrong_role", "wrong_database", "missing_schema", "schema_create", "relation_write",
    "missing_relation", "column_write", "missing_column", "outside_relation",
])
def test_reader_drift_or_write_capability_rejected(case):
    context, namespaces, relations, columns, planned = deepcopy(_case())
    if case == "wrong_role":
        context["session"] = "mfdata_admin"
    elif case == "wrong_database":
        context["database"] = "other"
    elif case == "missing_schema":
        namespaces.pop()
    elif case == "schema_create":
        namespaces[0]["create_allowed"] = True
    elif case == "relation_write":
        relations[0]["update_allowed"] = True
    elif case == "missing_relation":
        relations.clear()
    elif case == "column_write":
        columns[0]["insert_allowed"] = True
    elif case == "missing_column":
        columns.clear()
    else:
        relations.append(deepcopy(relations[0]) | {"oid": 901})
    with pytest.raises(check.ReaderCheckError):
        check.validate(context, namespaces, relations, columns, planned_after=planned)
