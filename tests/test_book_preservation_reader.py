"""Restricted-role full-row proofs; no grants to the private core facade."""

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import (
    book_preservation,
    book_projection,
    current_facts_store as current,
)
from mfdataindia.review.policy import fingerprint
from test_amc_book_facts import ROOT, prepared, publish
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES, source_state
from test_verified_review_store import verified_db as verified_db


def active_prior(dsn, monkeypatch):
    identities, inputs, _ = prepared(dsn, monkeypatch)
    publish(dsn, identities, inputs)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        book_projection.activate(
            conn,
            ROOT,
            [
                {
                    "code": 100001,
                    "batch_id": inputs[0][0]["batch_id"],
                    "as_of": "2026-09-30",
                    "values": {"aum": "12345.67"},
                }
            ],
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )


@pytest.mark.postgres
@pytest.mark.parametrize("role", ROLES)
def test_restricted_full_row_seal_equals_owner_facade_without_wider_privileges(
    verified_db, monkeypatch, role
):
    active_prior(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        original = source_state(conn)
        expected = book_preservation.capture(conn, 1)
        rows = conn.execute(
            "SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS sha256 FROM mf.current_fund_facts t WHERE amfi_scheme_code=ANY(%s) ORDER BY amfi_scheme_code",
            (expected["codes"],),
        ).fetchall()
        assert expected["hashes"]["mf.current_fund_facts"] == {
            "rows": len(rows),
            "sha256": fingerprint(rows),
        }
    with psycopg.connect(runtime_dsn(verified_db, role), row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        assert (
            conn.execute(
                "SELECT has_table_privilege(current_user,'mf.current_fund_facts','SELECT') AS allowed"
            ).fetchone()["allowed"]
            is False
        )
        assert book_preservation.capture(conn, 1) == expected
        assert (
            conn.execute(
                "SELECT has_table_privilege(current_user,'mf.current_fund_facts','SELECT') AS allowed"
            ).fetchone()["allowed"]
            is False
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(runtime_dsn(verified_db, role)) as conn:
            conn.execute("SELECT to_jsonb(t) FROM mf.current_fund_facts t LIMIT 1")
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert source_state(conn) == original


@pytest.mark.postgres
@pytest.mark.parametrize("drift", ["facade", "both_equal", "invoker", "both_invoker"])
def test_equivalent_rows_do_not_hide_an_unapproved_facade_definition(
    verified_db, monkeypatch, drift
):
    active_prior(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        if drift == "facade":
            conn.execute(
                "CREATE OR REPLACE VIEW mf.current_fund_facts AS SELECT * FROM mf_live_facts.resolved WHERE amfi_scheme_code>0"
            )
        elif drift == "both_equal":
            definition = current.view_definition(conn, "mf_live_facts.resolved").rstrip(";\n ")
            conn.execute(
                "CREATE OR REPLACE VIEW mf_live_facts.resolved AS SELECT * FROM ("
                + definition
                + ") matching_rows WHERE true"
            )
            conn.execute(
                "CREATE OR REPLACE VIEW mf.current_fund_facts AS "
                + current.view_definition(conn, "mf_live_facts.resolved")
            )
        else:
            conn.execute("ALTER VIEW mf.current_fund_facts SET(security_invoker=true)")
            if drift == "both_invoker":
                conn.execute("ALTER VIEW mf_live_facts.resolved SET(security_invoker=true)")
    with pytest.raises(ValueError, match="drift|definitions|contexts"):
        with psycopg.connect(runtime_dsn(verified_db, ROLES[1]), row_factory=dict_row) as conn:
            book_preservation.sealed_core_read_relation(conn)


@pytest.mark.parametrize("defect", ["columns", "owner", "options"])
def test_execution_signature_mismatch_fails_even_if_query_guard_is_satisfied(monkeypatch, defect):
    monkeypatch.setattr(current, "verify_acl", lambda conn: None)
    monkeypatch.setattr(current, "projection_state", lambda conn: True)

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0]

    class Connection:
        def execute(self, query, params):
            if "relowner,reloptions" in query:
                return Result(
                    [
                        {
                            "relowner": 2
                            if defect == "owner" and params[0].startswith("mf.")
                            else 1,
                            "reloptions": ["security_invoker=true"]
                            if defect == "options"
                            else None,
                        }
                    ]
                )
            return Result(
                [
                    {
                        "attnum": 1,
                        "attname": "code",
                        "atttypid": 23
                        if defect != "columns" or params[0].startswith("mf.")
                        else 20,
                        "atttypmod": -1,
                        "attcollation": 0,
                    }
                ]
            )

    with pytest.raises(ValueError, match="column signatures|contexts"):
        book_preservation.sealed_core_read_relation(Connection())
