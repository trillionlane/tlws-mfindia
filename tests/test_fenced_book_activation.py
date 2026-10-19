"""Disposable protocol proofs; synthetic sources never count as live coverage."""

from copy import deepcopy

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import (
    activation_fence as fence, book_activation as activation, book_preservation_v2 as proof,
    book_projection, book_store, current_facts_store as current,
)
from mfdataindia.review.policy import fingerprint
from test_amc_book_facts import ROOT
from test_uti_v2_store import prepare_v4, inputs_v2
import test_uti_store as old
from test_verified_review_store import source_state, verified_db as verified_db


def test_configuration_substitution_respects_literals_and_quoted_identifiers():
    definition = "SELECT 'mf_live_facts.configuration',\"mf_live_facts.configuration\" FROM mf_live_facts.configuration c;"
    assert activation.substitute_configuration(definition) == (
        "SELECT 'mf_live_facts.configuration',\"mf_live_facts.configuration\" FROM activation_configuration c")
    for invalid in ("SELECT 1", "SELECT * FROM mf_live_facts.configuration c,mf_live_facts.configuration d",
                    "SELECT * FROM other.mf_live_facts.configuration", "SELECT * FROM mf_live_facts.configuration_extra"):
        with pytest.raises(ValueError, match="single active-authority"):
            activation.substitute_configuration(invalid)


@pytest.mark.parametrize("codes", [[], [True], [-1], [1, 1], [2, 1], ["1"], [2147483648], list(range(4098))])
def test_preview_population_validation_precedes_sql(codes):
    preview = object.__new__(activation.Preview)
    with pytest.raises(ValueError, match="ordered candidate population"):
        preview.graph(None, codes)


@pytest.mark.parametrize("change", ["kind", "rls", "force_rls", "partition", "inherited", "custom_function", "options"])
def test_unhandled_dependency_fails_closed(change):
    row = {"kind": "v", "rls": False, "force_rls": False, "partition": False,
           "inherited": False, "custom_function": False, "options": None}
    row[change] = "f" if change == "kind" else (["security_invoker=true"] if change == "options" else True)

    class Connection:
        def execute(self, query, params):
            assert "WITH RECURSIVE" in query
            return self

        def fetchall(self):
            return [row]

    with pytest.raises(ValueError, match="unsupported activation dependency"):
        fence.inventory(Connection())


def test_no_external_certificate_or_timeout_increase_in_writer():
    import inspect

    source = inspect.getsource(activation.activate)
    assert "prepared = _prepare(" in source
    assert source.index("lock_and_verify") < source.index("book_projection.transition") < source.index("seal_configuration")
    assert "300s" not in source and "420s" not in source
    assert "CREATE OR REPLACE" not in source and "preservation.capture" not in source
    assert "prepared" not in inspect.signature(activation.activate).parameters


def test_request_binds_projection_and_reviewed_references():
    refs = [{"code": 1, "value": "original"}]
    request = activation._request(ROOT, refs, "a" * 64)
    refs[0]["value"] = "changed"
    assert request != activation._request(ROOT, refs, "a" * 64)
    assert request != activation._request(ROOT, refs, "b" * 64)


def test_open_caller_transaction_is_rejected_without_implicit_commit_or_rollback():
    from types import SimpleNamespace

    conn = SimpleNamespace(info=SimpleNamespace(transaction_status=psycopg.pq.TransactionStatus.INTRANS))
    with pytest.raises(ValueError, match="idle connection"):
        activation.activate(conn, ROOT, [], expected_configuration_hash="a" * 64)


def original_rows(conn, codes):
    rows = []
    for relation, column, section, sourced in (
        ("mf_live_facts.field_versions", "code", proof.SECTIONS[0], True),
        ("mf_live_facts.selected_fields", "code", proof.SECTIONS[1], True),
        ("mf_live_facts.current_values", "code", proof.SECTIONS[2], False),
        ("mf_live_facts.resolved", "amfi_scheme_code", proof.SECTIONS[3], False),
    ):
        payload = sql.SQL("to_jsonb(t)-'sources'") if sourced else sql.SQL("to_jsonb(t)")
        members = sql.SQL("""COALESCE((SELECT jsonb_agg(h ORDER BY h) FROM (
            SELECT encode(sha256(convert_to(value::text,'UTF8')),'hex') AS h
            FROM jsonb_array_elements(t.sources)) original_assertions),'[]'::jsonb)""") if sourced else sql.SQL("'[]'::jsonb")
        rows.extend(conn.execute(sql.SQL("""SELECT {} AS section,t.{} AS code,{} AS field,
            encode(sha256(convert_to(({})::text,'UTF8')),'hex') AS payload_hash,
            {} AS source_hashes FROM {} t WHERE {}=ANY(%s) ORDER BY code,field,payload_hash""").format(
                sql.Literal(section), sql.Identifier(column), sql.SQL("t.field") if sourced else sql.SQL("NULL::text"),
                payload, members, sql.Identifier(*relation.split(".")), sql.Identifier(column)), (codes,)).fetchall())
    return sorted(rows, key=lambda r: (r["section"], r["code"], r["field"] or "", r["payload_hash"]))


@pytest.mark.postgres
def test_snapshot_preview_atomic_fence_concurrency_and_recovery(verified_db, monkeypatch):
    identities, calls, baseline, original_proof, _, _ = prepare_v4(
        verified_db, monkeypatch, canonical=True, aum_rows=True)
    inputs = inputs_v2(identities, calls, canonical=True, aum_rows=True)
    old.publish(verified_db, identities, inputs, execution="fenced-candidate")
    refs = old.references(inputs[0][0])
    expected = fingerprint(baseline)

    def connect():
        return psycopg.connect(verified_db, row_factory=dict_row)

    def state():
        with connect() as conn:
            return book_store.capture(conn), dict(current.configuration(conn))

    initial = state()
    with connect() as conn:
        prepared = activation._prepare(conn, ROOT, refs, expected)
        assert prepared["prior"] == baseline
        proof.compare(original_proof, prepared["before"])
        assert prepared["checked"]["all_four_financial_query_payloads_unchanged"]
        assert state() == initial

    # Exact physical row-version fences must catch update-and-restore, INSERT,
    # DELETE, and same-count replacement without needing a dataset increment.
    with connect() as conn:
        conn.execute("CREATE TABLE public.fence_fixture(value integer)")
        conn.execute("INSERT INTO public.fence_fixture VALUES(1)")
    for mutation in (
        "UPDATE public.fence_fixture SET value=2",
        "UPDATE public.fence_fixture SET value=1",  # ABA restoration is still a new tuple.
        "INSERT INTO public.fence_fixture VALUES(3)",
        "DELETE FROM public.fence_fixture WHERE value=3",
        "TRUNCATE public.fence_fixture",
    ):
        with connect() as reader:
            reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
            roots = (*fence.ROOTS, "public.fence_fixture")
            with monkeypatch.context() as scoped:
                scoped.setattr(fence, "ROOTS", roots)
                before = fence.capture(reader)
                with connect() as writer:
                    writer.execute(mutation)
                with connect() as verifier:
                    from mfdataindia.enrichment.monthly_projection import owner
                    owner(verifier)
                    with pytest.raises(ValueError, match="changed after preparation"):
                        fence.lock_and_verify(verifier, before)

    # A stale actual fund input is rejected before any transition/config append,
    # even when the caller bypasses the advisory operation lock.
    with connect() as conn:
        original_name = conn.execute("SELECT scheme_name FROM mf.funds WHERE amfi_scheme_code=102398").fetchone()["scheme_name"]
    with connect() as conn:
        conn.execute("UPDATE mf.funds SET scheme_name=scheme_name||' changed' WHERE amfi_scheme_code=102398")
    with monkeypatch.context() as scoped:
        scoped.setattr(activation, "_prepare", lambda *args: deepcopy(prepared))
        with connect() as conn, pytest.raises(ValueError, match="changed after preparation"):
            book_projection.activate(conn, ROOT, refs, expected_configuration_hash=expected)
    assert state() == initial
    with connect() as conn:
        conn.execute("UPDATE mf.funds SET scheme_name=%s WHERE amfi_scheme_code=102398", (original_name,))

    # Replacing a view with the identical SQL still changes its catalog version.
    # A prepared proof cannot be reused across this administrative operation.
    with connect() as conn:
        metadata_prepared = activation._prepare(conn, ROOT, refs, expected)
        definition = current.view_definition(conn, "mf_live_facts.selected_fields")
        conn.execute("CREATE OR REPLACE VIEW mf_live_facts.selected_fields AS " + definition)
    with monkeypatch.context() as scoped:
        scoped.setattr(activation, "_prepare", lambda *args: deepcopy(metadata_prepared))
        with connect() as conn, pytest.raises(ValueError, match="changed after preparation"):
            book_projection.activate(conn, ROOT, refs, expected_configuration_hash=expected)
    assert state() == initial

    # SHARE locks protect all underlying inputs even from direct SQL writers.
    with connect() as conn:
        fresh = activation._prepare(conn, ROOT, refs, expected)
    with connect() as publisher:
        from mfdataindia.enrichment.monthly_projection import owner
        owner(publisher)
        fence.lock_and_verify(publisher, fresh["fence"])
        with connect() as concurrent:
            concurrent.execute("SET LOCAL lock_timeout='100ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                concurrent.execute("UPDATE mf.funds SET scheme_name=scheme_name WHERE amfi_scheme_code=102398")
        # A reader keeps working while the short publication fence is held.
        with connect() as reader:
            assert reader.execute("SELECT count(*) AS n FROM mf.funds").fetchone()["n"] > 0
    with connect() as writer:
        writer.execute("UPDATE mf.funds SET scheme_name=scheme_name WHERE amfi_scheme_code=102398")
        with connect() as conn:
            with pytest.raises(psycopg.errors.LockNotAvailable):
                book_projection.activate(conn, ROOT, refs, expected_configuration_hash=expected)
    assert state() == initial

    # Both inserts must roll back on failure, including failure after the next
    # configuration exists. No client-side compensating publication is needed.
    for phase in ("before_configuration", "after_configuration"):
        real_seal = activation.seal_configuration

        def fail(conn, *args):
            if phase == "after_configuration":
                real_seal(conn, *args)
            raise RuntimeError("injected publication failure")

        with monkeypatch.context() as scoped:
            scoped.setattr(activation, "seal_configuration", fail)
            with connect() as conn, pytest.raises(RuntimeError, match="injected"):
                book_projection.activate(conn, ROOT, refs, expected_configuration_hash=expected)
        assert state() == initial

    # Publish once; all four exact original relation queries must equal the
    # pre-publication candidate, over the entire retained fixture population.
    with connect() as conn:
        original = source_state(conn)
        conn.commit()
        prepared = activation._prepare(conn, ROOT, refs, expected)
        real_seal = activation.seal_configuration

        def verify_limits(conn, *args):
            assert conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"] == "5s"
            assert conn.execute("SHOW lock_timeout").fetchone()["lock_timeout"] == "5s"
            assert conn.execute("SHOW transaction_timeout").fetchone()["transaction_timeout"] == "15s"
            return real_seal(conn, *args)

        with monkeypatch.context() as scoped:
            scoped.setattr(activation, "seal_configuration", verify_limits)
            activated = book_projection.activate(conn, ROOT, refs, expected_configuration_hash=expected)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        assert current.configuration(conn)["view_definitions"] == baseline["view_definitions"]
    with connect() as conn:
        actual = proof.capture(conn, 4)
        assert actual == prepared["after"]
        assert proof._rows(conn, actual["codes"]) == original_rows(conn, actual["codes"])
        assert source_state(conn) == original
        replay_hash = fingerprint(dict(current.configuration(conn)))
        conn.commit()
        replay = book_projection.activate(conn, ROOT, refs,
            expected_configuration_hash=replay_hash)
        assert replay["status"] == "ALREADY_ACTIVE"
    with connect() as conn:
        assert book_projection.recover(conn, ROOT, activated["transition_id"])["status"] == "RECOVERED"
        assert book_projection.recover(conn, ROOT, activated["transition_id"])["status"] == "ALREADY_RECOVERED"
        assert current.configuration(conn)["view_definitions"] == baseline["view_definitions"]
        assert source_state(conn) == original


@pytest.mark.postgres
def test_dependency_metadata_and_lock_fence_without_protocol_fixture(verified_db, monkeypatch):
    # Small genuine PG tests for replacement/storage and metadata drift; no
    # installed financial relation or source framework is changed.
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("CREATE TABLE public.fenced_input(value integer)")
        conn.execute("INSERT INTO public.fenced_input VALUES(1)")
        conn.execute("CREATE VIEW public.fenced_view AS SELECT * FROM public.fenced_input")
        row = conn.execute("SELECT 'public.fenced_input'::regclass::oid::bigint AS oid").fetchone()
        relation = {**row, "schema": "public", "name": "fenced_input", "kind": "r"}
        before = fence.row_versions(conn, [relation])
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute(sql.SQL("UPDATE {} SET value=value").format(sql.Identifier("public", "fenced_input")))
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert fence.row_versions(conn, [relation]) != before
    monkeypatch.setattr(fence, "ROOTS", ("public.fenced_view",))
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        closed = fence.inventory(conn)
        assert {r["name"] for r in closed} == {"fenced_view", "fenced_input"}
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("ALTER TABLE public.fenced_input ENABLE ROW LEVEL SECURITY")
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        with pytest.raises(ValueError, match="unsupported activation dependency"):
            fence.inventory(conn)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("ALTER TABLE public.fenced_input DISABLE ROW LEVEL SECURITY")
        conn.execute("CREATE FUNCTION public.fenced_custom(integer) RETURNS integer LANGUAGE SQL IMMUTABLE AS 'SELECT $1'")
        conn.execute("CREATE OR REPLACE VIEW public.fenced_view AS SELECT public.fenced_custom(value) AS value FROM public.fenced_input")
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        with pytest.raises(ValueError, match="unsupported activation dependency"):
            fence.inventory(conn)
