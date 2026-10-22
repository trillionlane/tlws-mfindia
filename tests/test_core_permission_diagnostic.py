"""Catalogue-only permission diagnostics; no DEV/provider calls."""

from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from mfdataindia.review import core_permission_spec as spec
from mfdataindia.review.policy import fingerprint
from test_verified_review_store import verified_db as verified_db, source_state

WORKER = Path(__file__).parents[1] / "scripts/diagnose_core_permissions_dev.py"
loader = importlib.util.spec_from_file_location("core_permission_worker", WORKER)
worker = importlib.util.module_from_spec(loader)
loader.loader.exec_module(worker)


def catalogue_fixture():
    role = {
        "role_name": "mfdata_verified_reader",
        "memberships": 0,
        "can_connect": True,
        **{key: False for key in spec.ROLE_FLAGS},
    }
    schemas = [
        {"schema_name": name, "object_exists": True, "can_usage": True, "can_create": False}
        for name in spec.SCHEMAS
    ]
    relations = [
        {
            "schema_name": s,
            "relation_name": n,
            "object_exists": True,
            "relation_kind": kind,
            **{key: key in ("can_select", "any_column_select") for key in spec.RELATION_FLAGS},
        }
        for s, n, kind in spec.RELATIONS
    ]
    return {
        "context": {**spec.CONTEXT, "snapshot": "100:101:"},
        "role": role,
        "schemas": schemas,
        "relations": relations,
        "specification_hash": fingerprint(spec.specification()),
    }


def result_fixture():
    c = catalogue_fixture()
    return {
        "level": "CURRENT_CATALOGUE_PERMISSIONS_NOT_HISTORICAL_CAUSALITY",
        "catalogue": c,
        "catalogue_hash": fingerprint(c),
        "source_hashes": spec.SOURCE_HASHES,
        "worker_binding": {},
        "historical_denied_statement_proved": False,
        "performance_acceptance": False,
        "rollout_acceptance": False,
        **{
            k: 0
            for k in (
                "application_queries_executed",
                "application_functions_executed",
                "data_writes",
                "source_requests",
                "human_events_created",
                "permission_changes",
                "role_changes",
            )
        },
    }


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return deepcopy(self.rows)


class Connection:
    def __init__(self, catalogue=None, fail=None, drift=None):
        self.value = catalogue or catalogue_fixture()
        self.calls, self.contexts = [], 0
        self.fail, self.drift = fail, drift

    def execute(self, sql, bindings=None):
        self.calls.append((sql, deepcopy(bindings)))
        if sql == self.fail:
            raise psycopg.errors.InsufficientPrivilege("SENSITIVE_DSN_SQL_SERVER_TEXT")
        if sql in spec.BOUNDS:
            return Rows([])
        if sql == spec.CONTEXT_SQL:
            self.contexts += 1
            context = deepcopy(self.value["context"])
            if self.contexts == 2 and self.drift:
                context.update(self.drift)
            return Rows([context])
        if sql == spec.ROLE_SQL:
            return Rows([self.value["role"]])
        if sql == spec.SCHEMA_SQL:
            assert bindings == spec.STATEMENTS[1][2]
            return Rows(self.value["schemas"])
        if sql == spec.RELATION_SQL:
            assert bindings == spec.STATEMENTS[2][2]
            return Rows(self.value["relations"])
        raise AssertionError("unapproved/application SQL")


def test_exact_finite_metadata_inventory_no_application_data_or_statement_execution():
    c = Connection()
    result = worker.diagnose(c)
    assert [sql for sql, _ in c.calls] == [
        *spec.BOUNDS,
        spec.CONTEXT_SQL,
        spec.ROLE_SQL,
        spec.SCHEMA_SQL,
        spec.RELATION_SQL,
        spec.CONTEXT_SQL,
    ]
    assert len(spec.SCHEMAS) == 4 and len(spec.RELATIONS) == 15
    assert result["catalogue"] == c.value
    assert result["historical_denied_statement_proved"] is False
    spec.validate_result({**result, "worker_binding": {}})
    for _, sql, _ in spec.STATEMENTS:
        assert "pg_catalog." in sql
        assert "EXPLAIN" not in sql and "GRANT" not in sql and "SET ROLE" not in sql
        assert "FROM mf." not in sql and "FROM mf_live_facts." not in sql
    # Fixed request order must not depend on the database locale/collation.
    assert "WITH ORDINALITY" in spec.SCHEMA_SQL and "ORDER BY target.ordinal" in spec.SCHEMA_SQL
    assert "WITH ORDINALITY" in spec.RELATION_SQL and "ORDER BY target.ordinal" in spec.RELATION_SQL
    assert "FROM ROWS FROM (pg_catalog.unnest(" in spec.RELATION_SQL


def test_missing_objects_and_effective_denials_are_explicit_not_retried_or_treated_as_rootcause():
    v = catalogue_fixture()
    s = v["schemas"][1]
    s.update(object_exists=False, can_usage=None, can_create=None)
    missing = v["relations"][3]
    missing.update(
        object_exists=False, relation_kind=None, **{k: None for k in spec.RELATION_FLAGS}
    )
    denied = v["relations"][2]
    denied.update(can_select=False, any_column_select=True)
    c = Connection(v)
    result = worker.diagnose(c)
    assert result["catalogue"] == v and len(c.calls) == 9
    assert result["historical_denied_statement_proved"] is False


@pytest.mark.parametrize(
    "sql,phase",
    [
        (spec.CONTEXT_SQL, "context_before"),
        (spec.ROLE_SQL, "role_catalogue"),
        (spec.SCHEMA_SQL, "schema_catalogue"),
        (spec.RELATION_SQL, "relation_catalogue"),
    ],
)
def test_failure_redacts_messages_and_stops_once_without_retry_or_application_fallback(sql, phase):
    c = Connection(fail=sql)
    with pytest.raises(worker.ProbeFailure) as caught:
        worker.diagnose(c)
    assert str(caught.value) == phase + ":InsufficientPrivilege"
    assert "SENSITIVE" not in str(caught.value)
    assert sum(statement == sql for statement, _ in c.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [(k, True) for k in spec.ROLE_FLAGS]
    + [
        ("memberships", 1),
        ("memberships", False),
        ("role_name", "postgres"),
        ("can_connect", False),
    ],
)
def test_role_elevation_or_indirect_login_stops_before_target_catalogues(field, value):
    v = catalogue_fixture()
    v["role"][field] = value
    c = Connection(v)
    with pytest.raises(worker.ProbeFailure):
        worker.diagnose(c)
    assert not any(sql in (spec.SCHEMA_SQL, spec.RELATION_SQL) for sql, _ in c.calls)


@pytest.mark.parametrize(
    "field,value",
    [
        ("effective", "postgres"),
        ("authenticated", "postgres"),
        ("database", "other"),
        ("read_only", "off"),
        ("isolation", "read committed"),
        ("statement_timeout", "30s"),
        ("lock_timeout", "30s"),
        ("transaction_timeout", "60s"),
        ("snapshot", ""),
    ],
)
def test_context_before_and_after_must_remain_identical_bounded_direct_reader(field, value):
    c = Connection(drift={field: value})
    with pytest.raises(worker.ProbeFailure):
        worker.diagnose(c)
    bad = catalogue_fixture()
    bad["context"][field] = value
    c = Connection(bad)
    with pytest.raises(worker.ProbeFailure):
        worker.diagnose(c)
    assert len(c.calls) == 5


@pytest.mark.parametrize(
    "defect",
    [
        "extra",
        "missing_schema",
        "duplicate_schema",
        "order",
        "unknown_relation",
        "missing_relation",
        "boolean_coercion",
        "null_denial",
        "kind",
        "spec",
    ],
)
def test_snapshot_contract_never_drops_unknown_or_missing_rows_or_coerces_flags(defect):
    v = catalogue_fixture()
    if defect == "extra":
        v["extra"] = 1
    elif defect == "missing_schema":
        v["schemas"].pop()
    elif defect == "duplicate_schema":
        v["schemas"][1] = v["schemas"][0]
    elif defect == "order":
        v["relations"].reverse()
    elif defect == "unknown_relation":
        v["relations"][0]["relation_name"] = "other"
    elif defect == "missing_relation":
        v["relations"].pop()
    elif defect == "boolean_coercion":
        v["relations"][0]["can_select"] = 1
    elif defect == "null_denial":
        v["schemas"][0]["can_usage"] = None
    elif defect == "kind":
        v["relations"][0]["relation_kind"] = "S"
    else:
        v["specification_hash"] = "0" * 64
    with pytest.raises(ValueError):
        spec.validate_catalogue(v)


def binding_fixture():
    m = SimpleNamespace(
        rollout_id=spec.IDENTITY,
        cohort=[154117],
        canary_code=154117,
        git_sha="a" * 40,
        image="SYNTHETIC_NOT_PROVIDER_PROVENANCE",
    )
    e = {
        "DEPLOYMENT_GIT_SHA": m.git_sha,
        "CORE_PERMISSION_PROGRAM_SHA256": hashlib.sha256(WORKER.read_bytes()).hexdigest(),
        "CORE_PERMISSION_SPEC_SHA256": hashlib.sha256(Path(spec.__file__).read_bytes()).hexdigest(),
    }
    return m, e


def test_actual_packaged_program_and_spec_bound_before_database():
    m, e = binding_fixture()
    assert worker.worker_binding(m, e)["packaged_spec_sha256"] == e["CORE_PERMISSION_SPEC_SHA256"]


@pytest.mark.parametrize(
    "key", ["DEPLOYMENT_GIT_SHA", "CORE_PERMISSION_PROGRAM_SHA256", "CORE_PERMISSION_SPEC_SHA256"]
)
def test_mutated_program_spec_or_git_never_reaches_dsn_or_connection(monkeypatch, key):
    m, e = binding_fixture()
    e[key] = "0" * (40 if key == "DEPLOYMENT_GIT_SHA" else 64)
    with pytest.raises(ValueError):
        worker.worker_binding(m, e)
    monkeypatch.setattr(worker.Manifest, "model_validate_json", lambda _: m)
    monkeypatch.setattr(worker, "pinned_dsn", lambda *_: pytest.fail("DSN reached on bad binding"))
    monkeypatch.setattr(worker.sys, "argv", ["worker"])
    monkeypatch.setenv("REVIEW_MANIFEST", "{}")
    for name, value in e.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(worker.ProbeFailure):
        worker.main()


@pytest.mark.parametrize("value", ["dev-core-plan-261021a", "dev-uti-gap-261021k"])
def test_old_failed_diagnostic_or_rollout_identity_cannot_be_reused(value):
    m, e = binding_fixture()
    m.rollout_id = value
    with pytest.raises(ValueError):
        worker.worker_binding(m, e)


def test_original_failed_worker_queries_and_privilege_helpers_bytes_remain_pinned():
    spec.verify_sources(WORKER.parents[1])


def test_source_drift_stops_before_any_permission_queries(tmp_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        spec.verify_sources(tmp_path)


@pytest.mark.parametrize("tampered", list(spec.SOURCE_HASHES))
def test_installed_only_sources_bind_without_checkout_src_and_reject_each_tamper(tmp_path, monkeypatch, tampered):
    application = tmp_path / "app"
    package = tmp_path / "site-packages" / "mfdataindia"
    locations = {}
    for name in spec.SOURCE_HASHES:
        target = (package / name.removeprefix("src/mfdataindia/")
                  if name.startswith("src/mfdataindia/") else application / name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((WORKER.parents[1] / name).read_bytes())
        locations[name] = target
    installed_spec = package / "review" / "core_permission_spec.py"
    installed_spec.parent.mkdir(parents=True, exist_ok=True)
    installed_spec.write_bytes(Path(spec.__file__).read_bytes())
    monkeypatch.setattr(spec, "__file__", str(installed_spec))
    assert not (application / "src").exists()
    spec.verify_sources(application)
    locations[tampered].write_bytes(b"unreviewed source")
    with pytest.raises(ValueError, match="source bytes differ"):
        spec.verify_sources(application)


@pytest.mark.postgres
def test_native_catalogue_denied_select_and_missing_schema_without_application_reads_or_grant_changes(
    verified_db,
):
    # Disposable CI database only. No production or local PG is started.
    with psycopg.connect(verified_db, row_factory=dict_row) as admin:
        admin.execute("REVOKE SELECT ON mf.fund_variants FROM mfdata_verified_reader")
        admin.execute(
            "GRANT SELECT (amfi_scheme_code) ON mf.fund_variants TO mfdata_verified_reader"
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as admin:
        before = source_state(admin)
        acl_before = admin.execute(
            "SELECT relacl::text AS acl FROM pg_catalog.pg_class WHERE oid='mf.fund_variants'::regclass"
        ).fetchone()
    direct = make_conninfo(
        verified_db, user="mfdata_verified_reader", password="verified-local-fixture-only"
    )
    with psycopg.connect(direct, row_factory=dict_row) as conn:
        for sql in spec.BOUNDS:
            conn.execute(sql)
        ctx = one_context = dict(conn.execute(spec.CONTEXT_SQL).fetchone())
        assert ctx["effective"] == ctx["authenticated"] == "mfdata_verified_reader"
        assert ctx["read_only"] == "on" and ctx["isolation"] == "repeatable read"
        assert (ctx["statement_timeout"], ctx["lock_timeout"], ctx["transaction_timeout"]) == (
            "5s",
            "5s",
            "15s",
        )
        spec.validate_role(dict(conn.execute(spec.ROLE_SQL).fetchone()))
        schemas = conn.execute(spec.SCHEMA_SQL, spec.STATEMENTS[1][2]).fetchall()
        assert next(r for r in schemas if r["schema_name"] == "mf_live_facts") == {
            "schema_name": "mf_live_facts",
            "object_exists": False,
            "can_usage": None,
            "can_create": None,
        }
        relations = conn.execute(spec.RELATION_SQL, spec.STATEMENTS[2][2]).fetchall()
        denied = next(
            r
            for r in relations
            if r["schema_name"] == "mf" and r["relation_name"] == "fund_variants"
        )
        assert denied["can_select"] is False and denied["any_column_select"] is True
        assert len(relations) == 15
        assert dict(conn.execute(spec.CONTEXT_SQL).fetchone()) == one_context
    with psycopg.connect(verified_db, row_factory=dict_row) as admin:
        assert source_state(admin) == before
        assert (
            admin.execute(
                "SELECT relacl::text AS acl FROM pg_catalog.pg_class WHERE oid='mf.fund_variants'::regclass"
            ).fetchone()
            == acl_before
        )
