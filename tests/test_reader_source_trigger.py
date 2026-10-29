"""Provider-free PG18 trigger graph regression; no operational authority."""

import pytest

import reader_source_reference as ref
import reader_source_variants as variants
from test_reader_source_reference import mock_catalogue
from test_reader_source_variants import synthetic


def test_constraint_internal_trigger_dependency_has_an_explicit_catalogue():
    assert "pg_trigger" in ref.FIELDS
    classes = {name: 1000 + index for index, name in enumerate(sorted(ref.FIELDS))}
    edge = dict(classid=classes["pg_constraint"], objid=52000, objsubid=0,
                refclassid=classes["pg_trigger"], refobjid=53000,
                refobjsubid=0, deptype="i")
    assert ref.dependency_batch(object(), [edge], "pg_constraint", classes) == []


@pytest.mark.parametrize("numbers", [[0], [-1], [1, 1], [2], [1, True]])
def test_invalid_trigger_column_vector_rejected(numbers):
    identity = {("pg_class", 51000, 1): {"identity": "public.example.a"}}
    columns = {(51000, 1): {"attisdropped": False}}
    with pytest.raises(ValueError):
        ref.trigger_columns(51000, numbers, columns, identity)


def test_dropped_trigger_column_rejected_even_with_identity():
    identity = {("pg_class", 51000, 1): {"identity": "public.example.a"}}
    with pytest.raises(ValueError):
        ref.trigger_columns(51000, [1], {(51000, 1): {"attisdropped": True}}, identity)


def test_complete_trigger_projection_seals_private_expressions():
    sql = ref.projection("pg_trigger")
    assert "r.tgattr::int2[]" in sql
    assert "pg_catalog.pg_get_triggerdef(r.oid,false)" in sql
    assert "pg_catalog.encode(r.tgargs,'hex')" in sql
    assert "r.tgqual::text" in sql
    assert "'tgargs'" not in sql and "'tgqual'" not in sql
    assert set(ref.REFS["pg_trigger"]) == {
        "tgrelid", "tgparentid", "tgfoid", "tgconstrrelid", "tgconstrindid", "tgconstraint"
    }


def test_trigger_private_seals_stay_raw_while_portable_keeps_shapes():
    extended = synthetic()
    raw = extended["legacy"]
    row = {field: 0 for field in ref.FIELDS["pg_trigger"]}
    row.update(oid=53000, tgrelid=30000, tgfoid=33000, tgconstraint=35000,
               tgattr=[], tgname="fixture_trigger",
               arguments_seal={"is_null": False, "sha256": "a" * 64, "utf8_bytes": 2},
               qualification_seal={"is_null": True, "sha256": None, "utf8_bytes": 0},
               definition_seal={"is_null": False, "sha256": "b" * 64, "utf8_bytes": 10})
    raw["objects"].append({"catalog": "pg_trigger", "oid": 53000, "row": row})
    raw["objects"].sort(key=lambda item: (item["catalog"], item["oid"]))
    raw["identities"].append({"catalog": "pg_trigger", "oid": 53000, "subid": 0,
                              "identity": {"type": "pg_trigger", "schema": "mf",
                                           "name": "fixture_trigger", "identity": "mf.fixture_trigger"}})
    first = variants.portable_source_reference(extended)
    obj = next(item for item in first["objects"] if item["catalog"] == "pg_trigger")
    assert obj["declaration"]["instance_expression_shapes"] == {
        "arguments_seal": {"is_null": False}, "qualification_seal": {"is_null": True}}
    assert "arguments_seal" not in obj["declaration"]
    row["arguments_seal"]["sha256"] = "c" * 64
    assert variants.portable_source_reference(extended) == first
    row["definition_seal"] = {"is_null": True, "sha256": None, "utf8_bytes": 0}
    with pytest.raises(ValueError):
        variants.portable_source_reference(extended)


def test_real_compiler_follows_constraint_trigger_and_exact_column(monkeypatch):
    classes = {name: 1000 + index for index, name in enumerate(sorted(ref.FIELDS))}
    null = {"is_null": True, "sha256": None, "utf8_bytes": 0}
    relation = {field: 0 for field in ref.FIELDS["pg_class"]}
    relation.update(oid=51000, relname="example", relkind="r", reloptions=None,
                    view_definition_seal=null, index_definition_seal=null)
    constraint = {field: 0 for field in ref.FIELDS["pg_constraint"]}
    constraint.update(oid=52000, conname="example_constraint", conrelid=51000,
                      contypid=23, conpfeqop=[], conppeqop=[], conffeqop=[],
                      conexclop=[], expression_seal=null, definition_seal=null)
    trigger = {field: 0 for field in ref.FIELDS["pg_trigger"]}
    trigger.update(oid=53000, tgname="example_trigger", tgrelid=51000,
                   tgconstraint=52000, tgattr=[1], arguments_seal=null,
                   qualification_seal=null,
                   definition_seal={"is_null": False, "sha256": "a" * 64,
                                    "utf8_bytes": 10})
    records = {("pg_class", 51000): relation,
               ("pg_constraint", 52000): constraint,
               ("pg_trigger", 53000): trigger}

    class Proxy:
        def setattr(self, module, name, original):
            assert module is ref and name == "rows"

            def wrapped(conn, query, params=()):
                if "AND relkind IN ('r','p','v','m','S')" in query:
                    return [{"oid": 51000}]
                if query.startswith("SELECT pg_catalog.jsonb_build_object(") and " r WHERE r.oid=ANY" in query:
                    cat = query.partition(" FROM pg_catalog.")[2].split()[0]
                    if cat in ("pg_class", "pg_constraint", "pg_trigger"):
                        return [{"value": records[(cat, oid)]} for oid in params[0]]
                if "FROM pg_catalog.pg_constraint WHERE contypid=ANY" in query:
                    return [{"oid": 52000, "parent": 23}]
                if "FROM pg_catalog.pg_constraint WHERE conrelid=ANY" in query:
                    return [{"oid": 52000, "parent": 51000}]
                if "FROM pg_catalog.pg_trigger WHERE tgrelid=ANY" in query:
                    return [{"oid": 53000, "parent": 51000}]
                if "FROM pg_catalog.pg_attribute WHERE attrelid=ANY" in query:
                    return [dict(attrelid=51000, attnum=1, attname="a",
                                 attisdropped=False, atttypid=23, atttypmod=-1,
                                 attcollation=0, attnotnull=False,
                                 attgenerated="", attidentity="")]
                if "pg_catalog.pg_identify_object" in query and params[2] in (
                    classes["pg_class"], classes["pg_constraint"], classes["pg_trigger"]
                ):
                    cat = next(name for name, classid in classes.items() if classid == params[2])
                    return [dict(oid=oid, subid=sub,
                                 value=dict(type=cat, schema="public", name=cat,
                                            identity="public.example.a" if sub else f"public.{cat}.{oid}"))
                            for oid, sub in zip(params[0], params[1], strict=True)]
                if "FROM pg_catalog.pg_depend " in query and params[0] == classes["pg_constraint"]:
                    return [dict(classid=classes["pg_constraint"], objid=52000,
                                 objsubid=0, refclassid=classes["pg_trigger"],
                                 refobjid=53000, refobjsubid=0, deptype="i")]
                return original(conn, query, params)

            monkeypatch.setattr(module, name, wrapped)

    raw = mock_catalogue(Proxy())
    assert any(edge[:2] == ["pg_constraint", 52000] and
               edge[3:] == ["pg_trigger", 53000, 0, "i"] for edge in raw["edges"])
    assert any(edge == ["pg_trigger", 53000, 0, "pg_class", 51000, 1, "declared"]
               for edge in raw["edges"])
    trigger_row = next(o["row"] for o in raw["objects"] if o["catalog"] == "pg_trigger")
    assert trigger_row["tgattr"] == [1]
    semantic = ref.semantic_reference(raw)
    declaration = next(o["declaration"] for o in semantic["objects"]
                       if o["catalog"] == "pg_trigger")
    assert declaration["tgattr"] == [{"type": "pg_class", "schema": "public",
                                     "name": "pg_class", "identity": "public.example.a"}]
