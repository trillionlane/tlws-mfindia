"""Real CI compiler with synthetic metadata rows; no connection/provider."""

from copy import deepcopy
import json

import pytest

import reader_source_reference as ref
from test_reader_source_reference import mock_catalogue


def compiler_with_dependencies(monkeypatch, codes):
    classes = {n: 1000 + i for i, n in enumerate(sorted(ref.FIELDS))}
    extension = dict(
        oid=55000,
        extname="synthetic_extension",
        extowner=40000,
        extnamespace=0,
        extrelocatable=False,
        extversion="1.0",
        extconfig=None,
        conditions_seal=dict(is_null=True, sha256=None, utf8_bytes=0),
        config_count=None,
        condition_count=None,
    )

    class Proxy:
        def setattr(self, module, name, original):
            assert module is ref and name == "rows"

            def wrapped(conn, query, params=()):
                if (
                    "FROM pg_catalog.pg_depend " in query
                    and params[0] == classes["pg_type"]
                    and 23 in params[1]
                ):
                    return [
                        dict(
                            classid=classes["pg_type"],
                            objid=23,
                            objsubid=0,
                            refclassid=classes["pg_extension"],
                            refobjid=55000,
                            refobjsubid=0,
                            deptype=code,
                        )
                        for code in codes
                    ]
                if "AS value FROM pg_catalog.pg_extension r WHERE" in query:
                    return [{"value": deepcopy(extension)}]
                if (
                    "pg_catalog.pg_identify_object" in query
                    and params[2] == classes["pg_extension"]
                ):
                    return [
                        dict(
                            oid=55000,
                            subid=0,
                            value=dict(
                                type="extension",
                                schema=None,
                                name="synthetic_extension",
                                identity="synthetic_extension",
                            ),
                        )
                    ]
                return original(conn, query, params)

            monkeypatch.setattr(module, name, wrapped)

    return mock_catalogue(Proxy())


@pytest.mark.parametrize("codes", [("x",), ("e", "x")])
def test_actual_old_compiler_retains_distinct_documented_extension_dependency(monkeypatch, codes):
    result = compiler_with_dependencies(monkeypatch, codes)
    edges = [
        e for e in result["edges"] if e[0] == "pg_type" and e[1] == 23 and e[3] == "pg_extension"
    ]
    assert {e[-1] for e in edges} == set(codes)
    assert len(edges) == len(codes)
    extensions = [r for r in result["objects"] if r["catalog"] == "pg_extension"]
    assert len(extensions) == 1 and extensions[0]["oid"] == 55000


CLASSES = {n: 1000 + i for i, n in enumerate(sorted(ref.FIELDS))}


def dependency(code="n", target="pg_type", **changes):
    return dict(
        dict(
            classid=CLASSES["pg_type"],
            objid=23,
            objsubid=0,
            refclassid=CLASSES[target],
            refobjid=25,
            refobjsubid=0,
            deptype=code,
        ),
        **changes,
    )


def rejection(monkeypatch, deps, *, shared=False, lookup=None):
    calls = []

    def fake(_conn, query, params=()):
        calls.append((query, params))
        assert query.startswith("SELECT requested.refclassid::bigint")
        return lookup or []

    monkeypatch.setattr(ref, "rows", fake)
    with pytest.raises(ref.RejectedDependency) as error:
        ref.dependency_batch(object(), deps, "pg_type", CLASSES, shared=shared)
    marker = "CI_SOURCE_REFERENCE_REJECTED_DEPENDENCY_V1:"
    assert str(error.value).startswith(marker) and ref.MARKER not in str(error.value)
    return json.loads(str(error.value)[len(marker) :]), calls


@pytest.mark.parametrize("code", ["n", "a", "i", "P", "S"])
def test_all_original_local_dependency_semantics_still_accept(code):
    assert ref.dependency_batch(object(), [dependency(code)], "pg_type", CLASSES) == []


@pytest.mark.parametrize("code", ["e", "x"])
def test_extension_classification_requires_full_positive_nonsubobject_address(monkeypatch, code):
    accepted = dependency(code, "pg_extension", refobjid=55000)
    assert ref.dependency_batch(object(), [accepted], "pg_type", CLASSES) == [55000]
    for changes in ({"refobjid": 0}, {"refobjsubid": 1}, {"refclassid": CLASSES["pg_type"]}):
        bad = dict(accepted, **changes)
        doc, calls = rejection(monkeypatch, [bad])
        assert doc["rejected_total"] == 1 and not calls


@pytest.mark.parametrize("label", ["?", "n\nserver text", None, True, 17])
def test_unknown_dependency_labels_are_exact_byte_encoded_never_raw(monkeypatch, label):
    doc, calls = rejection(monkeypatch, [dependency(label)])
    assert not calls and doc["rejected_total"] == 1
    raw = label.encode("utf8") if type(label) is str else ref.canonical(label).encode("utf8")
    assert doc["rows"][0]["dependency_type"] == dict(
        type=type(label).__name__, utf8_bytes=len(raw), hex=raw.hex(), sha256=ref.digest(raw)
    )
    assert doc["runtime_authority"] is doc["permission_acceptance"] is False


def test_rejected_batch_cap_hash_preserves_all_duplicates_and_scope(monkeypatch):
    deps = [dependency("?")] * 19
    doc, _ = rejection(monkeypatch, deps)
    assert doc["scope"] == "QUERIED_BATCH_ONLY_NOT_UNVISITED_GRAPH"
    assert doc["rejected_total"] == 19 and len(doc["rows"]) == 16
    assert doc["complete_rejected_rows_sha256"] == ref.digest(
        ref.canonical([doc["rows"][0]] * 19).encode()
    )


@pytest.mark.parametrize(
    "schema,name", [("pg_catalog", "pg_tablespace"), ("private\n", "object\x00"), (None, None)]
)
def test_unknown_catalogue_lookup_is_complete_bounded_and_diagnostic_only(
    monkeypatch, schema, name
):
    dep = dependency("n", refclassid=900001)
    doc, calls = rejection(
        monkeypatch,
        [dep],
        lookup=[dict(refclassid=900001, catalog_schema=schema, catalog_name=name)],
    )
    assert len(calls) == 1 and calls[0][1] == ([900001],)
    assert "FROM pg_catalog.unnest(%s::oid[])" in calls[0][0]
    assert (
        "LEFT JOIN pg_catalog.pg_class" in calls[0][0]
        and "LEFT JOIN pg_catalog.pg_namespace" in calls[0][0]
    )
    assert "UNMODELLED_REFERENCE_CATALOGUE" in doc["rows"][0]["reasons"]
    for field, value, known in [
        ("catalog_schema", schema, ("pg_catalog",)),
        ("catalog_name", name, tuple(CLASSES) + ("pg_tablespace",)),
    ]:
        assert doc["rows"][0][field] == ref.diagnostic_label(value, known)


@pytest.mark.parametrize(
    "lookup",
    [
        [],
        [dict(refclassid=900002, catalog_schema=None, catalog_name=None)],
        [dict(refclassid=900001, catalog_schema=None, catalog_name=None)] * 2,
    ],
)
def test_diagnostic_lookup_missing_wrong_or_duplicate_population_rejects(monkeypatch, lookup):
    monkeypatch.setattr(ref, "rows", lambda *_: lookup)
    with pytest.raises(ValueError):
        ref.dependency_batch(object(), [dependency(refclassid=900001)], "pg_type", CLASSES)


@pytest.mark.parametrize("code", ["o", "a", "i", "r"])
def test_all_original_role_shared_semantics_still_accept(code):
    dep = dependency(code, "pg_authid")
    for key in ("classid", "refobjsubid"):
        dep.pop(key)
    assert ref.dependency_batch(object(), [dep], "pg_type", CLASSES, shared=True) == []


def test_documented_shared_tablespace_type_remains_rejected(monkeypatch):
    dep = dependency("t", refclassid=900001)
    for key in ("classid", "refobjsubid"):
        dep.pop(key)
    doc, _ = rejection(
        monkeypatch,
        [dep],
        shared=True,
        lookup=[dict(refclassid=900001, catalog_schema="pg_catalog", catalog_name="pg_tablespace")],
    )
    assert doc["shared"] is True and "SHARED_TARGET_NOT_ROLE" in doc["rows"][0]["reasons"]


@pytest.mark.parametrize(
    "values", [[], [{"value": {"oid": 55001}}], [{"value": {"oid": 55000}}] * 2]
)
def test_missing_mismatched_or_duplicate_extension_target_cannot_be_admitted(monkeypatch, values):
    monkeypatch.setattr(ref, "rows", lambda *_: values)
    with pytest.raises((ValueError, KeyError)):
        ref.prefetch_extensions(object(), [55000], {})
