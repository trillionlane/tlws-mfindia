"""Synthetic source expectations only; no database or live permission claim."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import reader_source_reference as old
import reader_source_variants as v

ROOT = Path(__file__).resolve().parents[1]


def synthetic():
    objects, identities = [], []

    def add(cat, number, name, **changes):
        row = {
            key: [] if key in old.ARRAY_REFS else 0 for key in old.FIELDS[cat] if key != "rolconfig"
        }
        row["oid"] = number
        for key in old.PRIVATE.get(cat, {}):
            row[key + "_seal"] = {"is_null": True, "sha256": None, "utf8_bytes": 0}
        row.update(changes)
        for field in v.OPAQUE.get(cat, ()):
            row[field] = {"is_null": False, "sha256": "a" * 64, "utf8_bytes": 1}
            row[v.DEPARSE[cat]] = {"is_null": False, "sha256": "b" * 64, "utf8_bytes": 2}
        objects.append({"catalog": cat, "oid": number, "row": row})
        identities.append(
            {
                "catalog": cat,
                "oid": number,
                "subid": 0,
                "identity": {
                    "type": cat,
                    "schema": None if cat == "pg_authid" else "mf",
                    "name": name,
                    "identity": name if cat == "pg_authid" else "mf." + name,
                },
            }
        )
        return row

    for index, role in enumerate(old.GOVERNED_ROLES):
        add("pg_authid", 40000 + index, role, rolname=role)
    add("pg_namespace", 22000, "mf", nspname="mf", nspowner=40000, nspacl=None)
    add("pg_language", 12, "plpgsql", lanname="plpgsql", lanowner=40000, lanacl=None)
    for number, name in ((23, "int4"), (25, "text")):
        add("pg_type", number, name, typnamespace=22000, typname=name, typowner=40000, typacl=None)
    add(
        "pg_proc",
        33000,
        "function(integer)",
        pronamespace=22000,
        proname="function",
        proowner=40000,
        prolang=12,
        proargtypes=[23],
        prorettype=25,
        proacl=None,
    )
    for number, name in ((30000, "parent"), (30001, "child")):
        add("pg_class", number, name, relnamespace=22000, relname=name, relkind="r", relowner=40000)
    add("pg_rewrite", 34000, "rule", rulename="_RETURN", ev_class=30000)
    add("pg_constraint", 35000, "constraint", connamespace=22000, conrelid=30000)
    add("pg_attrdef", 36000, "default", adrelid=30000, adnum=1)
    raw = {
        "format": "mf-reader-raw-CI-source-reference-v1",
        "base_git": old.BASE,
        "authority_sha256": old.AUTHORITY_SHA,
        "context": {"version": 180006},
        "database": {
            "oid": 999,
            "datname": "mfdata_reference_synthetic",
            "encoding": 6,
            "datlocprovider": "c",
            "datcollate": "C",
            "datctype": "C",
            "datlocale": None,
        },
        "objects": sorted(objects, key=lambda o: (o["catalog"], o["oid"])),
        "identities": identities,
        "edges": [],
        "aggregates": [],
        "ranges": [],
        "columns": [],
        "memberships": [],
        "conversions": [],
        "runtime_authority": False,
        "permission_acceptance": False,
    }
    acl = []
    for obj in raw["objects"]:
        cat = obj["catalog"]
        if cat in v.ACL:
            atoms = [[40000, 40000, p, False] for p in sorted(v.ACL[cat][3])]
            if cat != "pg_namespace":
                atoms += [[40000, 0, next(iter(v.ACL[cat][3])), False]]
            acl.append(
                dict(
                    catalog=cat,
                    oid=obj["oid"],
                    raw_acl_is_null=True,
                    atoms=sorted(atoms),
                    reader_execute=True if cat == "pg_proc" else None,
                )
            )
    candidate = [
        dict(
            oid=o["oid"],
            nspname="mf",
            relname=o["row"]["relname"],
            relkind="r",
            owner=40000,
            definition=deepcopy(o["row"]["view_definition_seal"]),
        )
        for o in raw["objects"]
        if o["catalog"] == "pg_class"
    ]
    return {
        "format": "extended-CI-source-reference-NOT-admission-v1",
        "legacy": raw,
        "acl_rows": acl,
        "relation_candidate": candidate,
        "inheritance_rows": [
            dict(inhrelid=30001, inhparent=30000, inhseqno=1, inhdetachpending=False)
        ],
    }


def test_exact_nine_source_pins_and_source_classification():
    authority = v.variant_authority(ROOT)
    rows = authority["variant"]["files"]
    assert len(rows) == 9 and sum(r["bytes"] for r in rows) == 95068
    assert [r["path"] for r in rows] == list(v.SOURCE_PATHS)
    assert rows[-1]["path"] == "src/mfdataindia/enrichment/book_store.py"
    assert rows[-1]["bytes"] == 18662
    assert authority["variant"]["execution_order"] == list(v.EXECUTION_ORDER)
    assert authority["variant"]["selection_only_not_invoked"] == list(v.SELECTION_ONLY)
    assert "book_activation" not in authority["variant"]["execution_order"]


@pytest.mark.parametrize("mutation", ["omission", "order", "bytes", "sha", "source_bytes",
                                       "execution_omission", "execution_order", "selection_overlap"])
def test_ninth_pin_or_classification_drift_rejects_before_any_connection(tmp_path, mutation):
    paths = [old.AUTHORITY_PATH, v.AUTHORITY_PATH]
    for authority_path in paths:
        raw = (ROOT / authority_path).read_bytes()
        target = tmp_path / authority_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        for row in json.loads(raw)["files"]:
            target = tmp_path / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / row["path"]).read_bytes())
    authority_path = tmp_path / v.AUTHORITY_PATH
    value = json.loads(authority_path.read_text())
    if mutation == "omission":
        value["files"].pop()
    elif mutation == "order":
        value["files"][-2:] = list(reversed(value["files"][-2:]))
    elif mutation in ("bytes", "sha"):
        value["files"][-1]["bytes" if mutation == "bytes" else "sha256"] = (
            1 if mutation == "bytes" else "0" * 64
        )
    elif mutation == "source_bytes":
        target = tmp_path / value["files"][-1]["path"]
        target.write_bytes(target.read_bytes() + b"\n")
    elif mutation == "execution_omission":
        value["execution_order"].pop(0)
    elif mutation == "execution_order":
        value["execution_order"][:2] = reversed(value["execution_order"][:2])
    else:
        value["selection_only_not_invoked"].append("src/mfdataindia/enrichment/book_store.py")
    if mutation != "source_bytes":
        authority_path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        v.variant_authority(tmp_path)


def test_complete_portable_reference_is_separate_and_keeps_raw_untouched():
    raw = synthetic()
    before = deepcopy(raw)
    portable = v.portable_source_reference(raw)
    assert raw == before
    assert portable["runtime_authority"] is portable["permission_acceptance"] is False
    assert portable["format"] == "portable-CI-source-expectation-NOT-live-admission-v1"
    assert "raw_reference_sha256" not in portable
    assert portable["relation_candidate"] and portable["inheritance_rows"]
    for obj in portable["objects"]:
        if obj["catalog"] in v.ACL:
            assert v.ACL[obj["catalog"]][0] not in obj["declaration"]
            assert "canonical_ACL" in obj["declaration"]


OPAQUE = [(cat, field) for cat, fields in v.OPAQUE.items() for field in fields]


@pytest.mark.parametrize("cat,field", OPAQUE)
@pytest.mark.parametrize("part", ["sha256", "utf8_bytes"])
def test_opaque_hash_and_bytes_stay_only_in_raw_but_change_raw_hash(cat, field, part):
    raw = synthetic()
    original_raw = old.digest(old.canonical(raw).encode())
    portable = v.portable_source_reference(raw)
    obj = next(o for o in raw["legacy"]["objects"] if o["catalog"] == cat)
    obj["row"][field][part] = "c" * 64 if part == "sha256" else 99
    assert old.digest(old.canonical(raw).encode()) != original_raw
    assert v.portable_source_reference(raw) == portable


@pytest.mark.parametrize("cat,field", OPAQUE)
def test_opaque_null_shape_and_deparse_are_not_ignored(cat, field):
    raw = synthetic()
    original = v.portable_source_reference(raw)
    obj = next(o for o in raw["legacy"]["objects"] if o["catalog"] == cat)
    obj["row"][field] = dict(is_null=True, sha256=None, utf8_bytes=0)
    assert v.portable_source_reference(raw) != original
    obj["row"][v.DEPARSE[cat]]["sha256"] = "f" * 64
    assert v.portable_source_reference(raw) != original


@pytest.mark.parametrize("cat,field", OPAQUE)
def test_nonnull_opaque_requires_nonnull_complete_deparse(cat, field):
    raw = synthetic()
    obj = next(o for o in raw["legacy"]["objects"] if o["catalog"] == cat)
    obj["row"][v.DEPARSE[cat]] = dict(is_null=True, sha256=None, utf8_bytes=0)
    with pytest.raises(ValueError):
        v.portable_source_reference(raw)


@pytest.mark.parametrize(
    "mutation",
    [
        "privilege",
        "grantor_bool",
        "grantee_bool",
        "unknown_role",
        "grant_option",
        "missing",
        "extra",
        "duplicate",
        "null_flag",
        "effective_type",
        "unknown_key",
        "inheritance_bool",
        "candidate_owner",
        "unknown_declaration",
        "order",
    ],
)
def test_complete_extended_schema_rejects_ambiguity(mutation):
    raw = synthetic()
    row = next(r for r in raw["acl_rows"] if r["catalog"] == "pg_proc")
    if mutation == "privilege":
        row["atoms"][0][2] = "UPDATE"
    elif mutation == "grantor_bool":
        row["atoms"][0][0] = True
    elif mutation == "grantee_bool":
        row["atoms"][0][1] = False
    elif mutation == "unknown_role":
        row["atoms"][0][1] = 999999
    elif mutation == "grant_option":
        row["atoms"][0][3] = 1
    elif mutation == "missing":
        raw["acl_rows"].pop()
    elif mutation == "extra":
        raw["acl_rows"].append(deepcopy(row))
    elif mutation == "duplicate":
        row["atoms"].append(deepcopy(row["atoms"][0]))
    elif mutation == "null_flag":
        row["raw_acl_is_null"] = False
    elif mutation == "effective_type":
        row["reader_execute"] = 1
    elif mutation == "unknown_key":
        raw["unexpected"] = True
    elif mutation == "inheritance_bool":
        raw["inheritance_rows"][0]["inhseqno"] = True
    elif mutation == "candidate_owner":
        raw["relation_candidate"][0]["owner"] = 40002
    elif mutation == "order":
        row["atoms"].reverse()
    else:
        raw["legacy"]["objects"][0]["row"]["unexpected"] = True
    with pytest.raises((ValueError, KeyError, TypeError)):
        v.portable_source_reference(raw)


@pytest.mark.parametrize(
    "mutation",
    [
        "grantable",
        "grantee",
        "execution",
        "null_empty",
        "declaration",
        "locale",
        "membership",
        "view",
    ],
)
def test_portable_security_or_source_semantics_change_hash(mutation):
    raw = synthetic()
    original = old.digest(old.canonical(v.portable_source_reference(raw)).encode())
    row = next(r for r in raw["acl_rows"] if r["catalog"] == "pg_proc")
    if mutation == "grantable":
        row["atoms"][0][3] = True
    elif mutation == "grantee":
        row["atoms"][0][1] = 40002
        row["atoms"].sort()
    elif mutation == "execution":
        row["reader_execute"] = False
    elif mutation == "null_empty":
        next(o for o in raw["legacy"]["objects"] if o["catalog"] == "pg_proc")["row"]["proacl"] = []
        row.update(raw_acl_is_null=False, atoms=[], reader_execute=False)
    elif mutation == "declaration":
        next(o for o in raw["legacy"]["objects"] if o["catalog"] == "pg_proc")["row"][
            "prosecdef"
        ] = True
    elif mutation == "locale":
        raw["legacy"]["database"]["datcollate"] = "OTHER"
    elif mutation == "membership":
        raw["legacy"]["memberships"] = [
            dict(
                roleid=40000,
                member=40002,
                grantor=40000,
                admin_option=False,
                inherit_option=True,
                set_option=False,
            )
        ]
    else:
        obj = next(o for o in raw["legacy"]["objects"] if o["catalog"] == "pg_class")
        obj["row"]["view_definition_seal"] = dict(is_null=False, sha256="e" * 64, utf8_bytes=3)
        raw["relation_candidate"][0]["definition"] = deepcopy(obj["row"]["view_definition_seal"])
    assert old.digest(old.canonical(v.portable_source_reference(raw)).encode()) != original


def payload(profile):
    from mfdataindia.review import reader_access_transport_spec as shape

    raw = synthetic()
    raw["legacy"]["context"]["database"] = "mfdata_reference_synthetic"
    state = {name: [] for name in shape.SCHEMAS}
    state["databases"] = [
        dict(
            oid=999,
            datname="mfdata_reference_synthetic",
            owner=40000,
            acl=[],
            connect_allowed=False,
            create_allowed=False,
            temporary_allowed=False,
        )
    ]
    state["relations"] = [
        dict(
            oid=r["oid"],
            namespace_oid=22000,
            nspname=r["nspname"],
            relname=r["relname"],
            relkind=r["relkind"],
            owner=r["owner"],
            attribute_count=0,
            row_security=False,
            force_row_security=False,
            options=None,
            definition=r["definition"],
            acl=[],
            capabilities=[False] * 4,
            sequence_capabilities=None,
        )
        for r in raw["relation_candidate"]
    ]
    return dict(
        format="extended-source-calibration-NOT-live-acceptance-v1",
        profile=profile,
        compiler_git="e" * 40,
        compiler_sources={
            path: old.digest((ROOT / path).read_bytes())
            for path in ("tests/reader_source_reference.py", "tests/reader_source_variants.py")
        },
        base_authority_sha256=old.AUTHORITY_SHA,
        variant_authority_sha256=v.AUTHORITY_SHA,
        authority=v.variant_authority(ROOT),
        raw=raw,
        raw_sha256=old.digest(old.canonical(raw).encode()),
        grant_state=state,
        portable_sha256=old.digest(old.canonical(v.portable_source_reference(raw)).encode()),
        portable_projection_format="portable-CI-source-expectation-NOT-live-admission-v1",
        runtime_authority=False,
        permission_acceptance=False,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_git",
        "git",
        "helper_sha",
        "missing_helper",
        "extra_helper",
        "base_authority",
        "authority_body",
        "raw_sha",
        "projection_format",
        "format",
        "unknown_key",
        "missing_state",
    ],
)
def test_actual_old_named_extractor_rejects_missing_or_false_compiler_provenance(mutation):
    profile = v.PROFILES[0]
    doc = payload(profile)
    if mutation == "missing_git":
        doc.pop("compiler_git")
    elif mutation == "git":
        doc["compiler_git"] = "f" * 40
    elif mutation == "helper_sha":
        doc["compiler_sources"]["tests/reader_source_reference.py"] = "0" * 64
    elif mutation == "missing_helper":
        doc["compiler_sources"].pop("tests/reader_source_variants.py")
    elif mutation == "extra_helper":
        doc["compiler_sources"]["unexpected.py"] = "0" * 64
    elif mutation == "base_authority":
        doc["base_authority_sha256"] = "0" * 64
    elif mutation == "authority_body":
        doc["authority"]["variant"]["total_bytes"] -= 1
    elif mutation == "raw_sha":
        doc["raw_sha256"] = "0" * 64
    elif mutation == "projection_format":
        doc["portable_projection_format"] = "unreviewed"
    elif mutation == "format":
        doc["format"] = "unreviewed"
    elif mutation == "unknown_key":
        doc["unexpected"] = True
    else:
        doc.pop("grant_state")
    lines = v.named_envelope(doc, identity="b" * 32)
    with pytest.raises((ValueError, KeyError)):
        v.extract_named(lines, profile, expected_git="e" * 40)


def test_named_source_envelopes_are_separate_and_lossless():
    lines = [*old.reference_envelope({"old_fixture": True}, identity="a" * 32)]
    for profile in v.PROFILES:
        lines += v.named_envelope(payload(profile), identity="b" * 32)
    for profile in v.PROFILES:
        assert v.extract_named(lines, profile, expected_git="e" * 40) == payload(profile)


@pytest.mark.parametrize(
    "mutation",
    [
        "db_oid",
        "db_name",
        "context_database",
        "grant_root",
        "state_unknown",
        "state_missing",
        "state_acl",
    ],
)
def test_named_source_rejects_grant_state_or_database_inconsistency(mutation):
    doc = payload(v.PROFILES[0])
    state = doc["grant_state"]
    if mutation == "db_oid":
        state["databases"][0]["oid"] += 1
    elif mutation == "db_name":
        state["databases"][0]["datname"] += "_other"
    elif mutation == "context_database":
        doc["raw"]["legacy"]["context"]["database"] += "_other"
        doc["raw_sha256"] = old.digest(old.canonical(doc["raw"]).encode())
    elif mutation == "grant_root":
        state["relations"][0]["oid"] += 2
    elif mutation == "state_unknown":
        state["unexpected"] = []
    elif mutation == "state_missing":
        state.pop("roles")
    else:
        # A well-formed but non-list ACL atom must fail the frozen shape guard.
        state["relations"][0]["acl"] = [{"unexpected": True}]
    from mfdataindia.review.reader_access_correction import ChangeFailure

    with pytest.raises((ValueError, KeyError, ChangeFailure)):
        v.extract_named(
            v.named_envelope(doc, identity="b" * 32), doc["profile"], expected_git="e" * 40
        )


@pytest.mark.parametrize("git", [None, True, "E" * 40, "e" * 39, "f" * 40])
def test_named_source_requires_independent_exact_git_binding(git):
    doc = payload(v.PROFILES[0])
    with pytest.raises(ValueError):
        v.extract_named(v.named_envelope(doc, identity="b" * 32), doc["profile"], expected_git=git)


def transition_pair():
    base = payload(v.PROFILES[0])
    for i, (namespace, name) in enumerate(sorted(v.BOOK_VIEW_KEYS)):
        before = deepcopy(base["grant_state"]["relations"][0])
        before.update(
            oid=31000 + i,
            nspname=namespace,
            relname=name,
            relkind="v",
            definition=dict(is_null=False, sha256="a" * 64, utf8_bytes=10),
        )
        base["grant_state"]["relations"].append(before)
        base["raw"]["relation_candidate"].append(
            {k: before[k] for k in v.inventory.CANDIDATE_COLUMNS}
        )
    book = deepcopy(base)
    book["profile"] = v.PROFILES[1]
    for row in book["grant_state"]["relations"]:
        if (row["nspname"], row["relname"]) in v.BOOK_VIEW_KEYS:
            row["definition"] = dict(is_null=False, sha256="b" * 64, utf8_bytes=20)
    return base, book


def test_source_only_transition_accepts_only_four_exact_changed_view_seals():
    base, book = transition_pair()
    original = deepcopy((base, book))
    v.assert_source_only_transition(base, book)
    assert (base, book) == original


@pytest.mark.parametrize(
    "mutation",
    [
        "root",
        "owner",
        "relation_acl",
        "column_acl",
        "namespace_acl",
        "database_access",
        "role",
        "membership",
        "default",
        "policy",
        "canonical_acl",
        "missing_view_change",
        "extra_definition",
        "attribute",
        "capabilities",
        "candidate",
    ],
)
def test_source_only_transition_rejects_any_root_acl_or_other_grant_state_drift(mutation):
    base, book = transition_pair()
    state = book["grant_state"]
    if mutation == "root":
        state["relations"][0]["relname"] += "_other"
    elif mutation == "owner":
        state["relations"][0]["owner"] += 1
    elif mutation in ("relation_acl", "namespace_acl"):
        if mutation == "namespace_acl":
            # A complete schema row is not needed to show a rejected unexpected addition.
            state["namespaces"].append({"unexpected": True})
        else:
            state["relations"][0]["acl"].append([40000, 40002, "SELECT", False])
    elif mutation == "database_access":
        state["databases"][0]["connect_allowed"] = True
    elif mutation == "canonical_acl":
        book["raw"]["acl_rows"][0]["reader_execute"] = False
    elif mutation == "missing_view_change":
        state["relations"][-1]["definition"] = deepcopy(
            base["grant_state"]["relations"][-1]["definition"]
        )
    elif mutation == "extra_definition":
        state["relations"][0]["definition"] = dict(is_null=False, sha256="c" * 64, utf8_bytes=30)
    elif mutation == "attribute":
        state["relations"][0]["attribute_count"] += 1
    elif mutation == "capabilities":
        state["relations"][0]["capabilities"][0] = True
    elif mutation == "candidate":
        book["raw"]["relation_candidate"][0]["oid"] += 1
    else:
        group = {
            "column_acl": "columns",
            "role": "roles",
            "membership": "memberships",
            "default": "defaults",
            "policy": "policies",
        }[mutation]
        state[group].append({"unexpected": True})
    with pytest.raises((ValueError, KeyError)):
        v.assert_source_only_transition(base, book)


@pytest.mark.parametrize(
    "mutation", ["duplicate", "gap", "wrong_profile", "unknown_marker", "mixed", "portable_hash"]
)
def test_named_envelope_rejects_mixed_or_incomplete_source_provenance(mutation):
    profile = v.PROFILES[0]
    doc = payload(profile)
    if mutation == "portable_hash":
        doc["portable_sha256"] = "0" * 64
    lines = v.named_envelope(doc, identity="b" * 32)
    if mutation == "duplicate":
        lines += lines
    elif mutation == "gap":
        lines.pop(1)
    elif mutation == "wrong_profile":
        lines = [s.replace(v.MARKERS[profile], v.MARKERS[v.PROFILES[1]]) for s in lines]
    elif mutation == "unknown_marker":
        lines.append("SOURCE_SEMANTIC_REFERENCE_UNKNOWN_V1:{}")
    elif mutation == "mixed":
        lines[1] = lines[1].replace('"identity":"' + "b" * 32, '"identity":"' + "c" * 32)
    with pytest.raises((ValueError, KeyError)):
        v.extract_named(lines, profile, expected_git="e" * 40)


def test_fixed_ACL_queries_expand_null_defaults_but_not_empty_arrays():
    for cat, (field, owner, kind, _) in v.ACL.items():
        text = v.acl_query(cat)
        assert f"COALESCE(r.{field},pg_catalog.acldefault('{kind}'::\"char\",r.{owner}))" in text
        assert (
            "pg_catalog.aclexplode" in text and "WHERE r.oid=ANY(%s::oid[]) ORDER BY r.oid" in text
        )
        assert ("has_function_privilege" in text) is (cat == "pg_proc")


@pytest.mark.parametrize(
    "mutation", [None, "columns", "cardinality", "command_tag", "missing_object"]
)
def test_actual_extended_collector_queries_have_complete_fixed_populations(monkeypatch, mutation):
    value = synthetic()
    monkeypatch.setattr(old, "collect", lambda _conn: deepcopy(value["legacy"]))
    calls = []

    class Connection:
        def execute(self, text, params=(), *, prepare=False):
            assert prepare is False
            calls.append((text, params))
            for cat in sorted(v.ACL):
                if text == v.acl_query(cat):
                    rows = [
                        {k: deepcopy(atom) for k, atom in row.items() if k != "catalog"}
                        for row in value["acl_rows"]
                        if row["catalog"] == cat
                    ]
                    assert params == (
                        (40002, [r["oid"] for r in rows])
                        if cat == "pg_proc"
                        else ([r["oid"] for r in rows],)
                    )
                    if mutation == "missing_object" and cat == "pg_type":
                        rows.pop()
                    cursor = Cursor(
                        ["SELECT " + str(len(rows))],
                        rows,
                        ("oid", "raw_acl_is_null", "atoms", "reader_execute"),
                    )
                    if mutation == "columns":
                        cursor.description[0].name = "unexpected"
                    elif mutation == "cardinality":
                        cursor.rowcount += 1
                    elif mutation == "command_tag":
                        cursor.tags = ["SELECT 0"]
                    return cursor
            if text == v.inventory.CANDIDATE_SQL:
                rows = deepcopy(value["relation_candidate"])
                assert params == ()
                return Cursor(["SELECT " + str(len(rows))], rows, v.inventory.CANDIDATE_COLUMNS)
            assert "FROM pg_catalog.pg_inherits WHERE inhparent=ANY" in text
            assert params == ([30000, 30001], [30000, 30001])
            rows = deepcopy(value["inheritance_rows"])
            return Cursor(
                ["SELECT 1"], rows, ("inhrelid", "inhparent", "inhseqno", "inhdetachpending")
            )

    conn = old.Bounded(Connection(), old.time.monotonic() + 15)
    if mutation:
        with pytest.raises(ValueError):
            v.collect_extended(conn)
    else:
        assert v.collect_extended(conn) == value
        assert len(calls) == 6
        assert all(text.startswith(("SELECT ", "WITH RECURSIVE ")) for text, _ in calls)


def test_native_variant_authority_is_checked_before_admin_connect(monkeypatch):
    import test_reader_source_variants_postgres as native

    calls = []

    def reject(_root):
        raise ValueError("synthetic preflight drift")

    monkeypatch.setattr(native.variants, "variant_authority", reject)
    monkeypatch.setattr(native.psycopg, "connect", lambda *_args, **_kwargs: calls.append(True))
    generator = native.source_variant_db.__wrapped__("synthetic-no-connection")
    with pytest.raises(ValueError, match="synthetic preflight drift"):
        next(generator)
    assert calls == []


class Cursor:
    def __init__(self, tags, rows=None, columns=(), fail_next=False):
        self.tags, self.index, self.values, self.fail_next = tags, 0, rows, fail_next
        self.description = [SimpleNamespace(name=n) for n in columns] if rows is not None else None
        self.rowcount = len(rows) if rows is not None else -1

    @property
    def statusmessage(self):
        return self.tags[self.index]

    def fetchall(self):
        return self.values

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def nextset(self):
        if self.fail_next:
            raise RuntimeError("synthetic later DDL result failed")
        self.index += 1
        return self.index < len(self.tags)


class Recording:
    def __init__(self, failure=None):
        self.autocommit, self.calls, self.failure = True, [], failure
        self.info = SimpleNamespace(
            transaction_status=old.TransactionStatus.IDLE,
            dbname="mfdata_reference_synthetic",
            user="mfdata_admin",
        )

    def execute(self, statement, parameters=(), *, prepare=False):
        assert prepare is False
        self.calls.append(statement)
        if statement.startswith("BEGIN"):
            self.info.transaction_status = old.TransactionStatus.INTRANS
            return Cursor(["BEGIN"])
        if statement.startswith("SET LOCAL"):
            return Cursor(["SET"])
        if statement in ("COMMIT", "ROLLBACK"):
            if self.failure == statement:
                raise RuntimeError("synthetic terminal ambiguity")
            self.info.transaction_status = (
                old.TransactionStatus.INTRANS
                if self.failure == "NONIDLE"
                else old.TransactionStatus.IDLE
            )
            return Cursor([statement])
        if statement.startswith("SELECT current_database"):
            return Cursor(
                ["SELECT 1"],
                [dict(database=self.info.dbname, actor="mfdata_admin", principal="mfdata_admin")],
                ("database", "actor", "principal"),
            )
        if statement.startswith("SELECT pg_get_viewdef"):
            assert parameters == ("mf_live_facts.resolved",)
            return Cursor(
                ["SELECT 1"], [{"definition": "SELECT 1 AS synthetic_source"}], ("definition",)
            )
        core = (ROOT / "sql/optional/amc_book_core_projection_v1.sql.inc").read_text()
        tags = ["CREATE VIEW", "DO"] if statement == core else ["CREATE VIEW"]
        if self.failure == "EXTRA_RESULT" and statement == core:
            tags.append("UPDATE 1")
        return Cursor(tags, fail_next=self.failure == "LATER_RESULT" and statement == core)


def test_exact_pinned_two_stage_DDL_drains_results_and_confirms_one_commit():
    conn = Recording()
    v.install_book_variant(conn, ROOT)
    assert conn.calls.count("COMMIT") == 1 and "ROLLBACK" not in conn.calls
    assert conn.info.transaction_status == old.TransactionStatus.IDLE
    assert conn.calls.index(
        (ROOT / "sql/optional/amc_book_current_projection_v2.sql.inc").read_text()
    ) < conn.calls.index((ROOT / "sql/optional/amc_book_core_projection_v1.sql.inc").read_text())


@pytest.mark.parametrize("failure", ["LATER_RESULT", "EXTRA_RESULT"])
def test_later_DDL_result_failure_is_not_hidden_by_first_success(failure):
    conn = Recording(failure)
    with pytest.raises((RuntimeError, ValueError)):
        v.install_book_variant(conn, ROOT)
    assert "COMMIT" not in conn.calls and conn.calls.count("ROLLBACK") == 1


@pytest.mark.parametrize("failure", ["COMMIT", "NONIDLE"])
def test_commit_ambiguity_or_nonidle_is_terminal_without_rollback_retry(failure):
    conn = Recording(failure)
    with pytest.raises(v.DDLAbortUnconfirmed):
        v.install_book_variant(conn, ROOT)
    assert conn.calls.count("COMMIT") == 1 and "ROLLBACK" not in conn.calls


def test_precommit_original_error_only_after_confirmed_abort():
    conn = Recording()
    with pytest.raises(ValueError):
        with v.ddl_transaction(conn):
            raise ValueError("synthetic original")
    assert conn.calls.count("ROLLBACK") == 1 and "COMMIT" not in conn.calls


def test_dual_failure_and_spent_wall_issue_no_second_cleanup():
    conn = Recording("ROLLBACK")
    with pytest.raises(v.DDLAbortUnconfirmed):
        with v.ddl_transaction(conn):
            raise ValueError("synthetic original")
    assert conn.calls.count("ROLLBACK") == 1
    conn = Recording()
    with pytest.raises(v.DDLAbortUnconfirmed):
        with v.ddl_transaction(conn):
            raise old.WallSpent("synthetic spent")
    assert "ROLLBACK" not in conn.calls and "COMMIT" not in conn.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("dbname", "mfdataindia"),
        ("user", "postgres"),
        ("transaction_status", old.TransactionStatus.INTRANS),
    ],
)
def test_wrong_DDL_connection_rejects_before_any_IO(field, value):
    conn = Recording()
    setattr(conn.info, field, value)
    with pytest.raises(ValueError):
        with v.ddl_transaction(conn):
            pytest.fail("unsafe source fixture admitted")
    assert conn.calls == []
