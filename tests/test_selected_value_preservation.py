"""Value-row SQL equivalence; no invented source coverage or relaxed bounds."""

from copy import deepcopy

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import book_preservation, book_preservation_v2 as engine
from mfdataindia.enrichment import book_projection, book_store, current_facts_store as current
from mfdataindia.review.policy import fingerprint
from test_amc_book_facts import ROOT
from test_uti_v2_store import prepare_v4, inputs_v2
import test_uti_store as old
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES, source_state, verified_db as verified_db


@pytest.mark.parametrize("codes", [[], [True], [0], [-1], [1, 1], [2, 1], ["1"], [2147483648], list(range(1, 4098))])
def test_invalid_target_population_fails_without_database_access(codes):
    with pytest.raises(ValueError, match="ordered target population"):
        engine._selected_value_rows(None, codes)


@pytest.mark.parametrize("drift", ["missing", "extra", "order"])
def test_column_drift_cannot_drop_a_value_from_the_old_hash(monkeypatch, drift):
    monkeypatch.setattr(book_preservation, "sealed_core_read_relation", lambda conn: None)

    class Connection:
        def execute(self, query, params=None):
            self.contexts = "pg_class" in query
            return self

        def fetchall(self):
            if self.contexts:
                return [{"relname": name, "relowner": 1, "reloptions": None}
                        for name in ["field_versions", "field_heads", "selected_fields"]]
            names = ["code", "field", "as_of", "value", "distinct_values", "usable", "sources"]
            if drift == "missing":
                names.remove("usable")
            elif drift == "extra":
                names.append("new_financial_column")
            else:
                names.reverse()
            return [{"attname": name} for name in names]

    with pytest.raises(ValueError, match="column signature"):
        engine._selected_value_rows(Connection(), [1])


@pytest.mark.parametrize("drift", ["missing", "owner", "options"])
def test_original_execution_context_guards_are_not_lost(monkeypatch, drift):
    monkeypatch.setattr(book_preservation, "sealed_core_read_relation", lambda conn: None)

    class Connection:
        def execute(self, query, params):
            assert "pg_class" in query
            self.rows = [{"relname": name, "relowner": 1, "reloptions": None} for name in params[0]]
            if drift == "missing":
                self.rows.pop()
            elif drift == "owner":
                self.rows[0]["relowner"] = 2
            else:
                self.rows[0]["reloptions"] = ["security_invoker=true"]
            return self

        def fetchall(self):
            return self.rows

    with pytest.raises(ValueError, match="execution contexts"):
        engine._selected_value_rows(Connection(), [1])


def test_query_uses_original_view_and_all_value_columns_without_unused_provenance(monkeypatch):
    guarded, queries = [], []
    monkeypatch.setattr(book_preservation, "sealed_core_read_relation", lambda conn: guarded.append(conn))

    class Connection:
        def execute(self, query, params=None):
            queries.append((query, params))
            self.contexts = "pg_class" in query
            self.columns = "pg_attribute" in query
            return self

        def fetchall(self):
            if self.contexts:
                return [{"relname": name, "relowner": 1, "reloptions": None}
                        for name in ["field_versions", "field_heads", "selected_fields"]]
            if self.columns:
                return [{"attname": name} for name in ["code", "field", "as_of", "value", "distinct_values", "usable", "sources"]]
            return []

    conn = Connection()
    assert engine._selected_value_rows(conn, [1, 2]) == []
    assert guarded == [conn]
    query, params = queries[-1]
    assert params == ([1, 2],)
    assert "FROM mf_live_facts.selected_fields t WHERE t.code=ANY(%s)" in query
    assert "jsonb_build_object" in query and "sha256" in query
    assert all("'" + name + "',t." + name in query for name in ["code", "field", "as_of", "value", "distinct_values", "usable"])
    assert not any(term in query for term in ["sources", "MATERIALIZED", "LIMIT", "SET ", "float", "canonical_json"])


def original_selected(conn, codes):
    return conn.execute("""SELECT t.code,t.field,
        encode(sha256(convert_to((to_jsonb(t)-'sources')::text,'UTF8')),'hex') AS payload_hash
        FROM mf_live_facts.selected_fields t WHERE t.code=ANY(%s)
        ORDER BY t.code,t.field,payload_hash""", (codes,)).fetchall()


@pytest.mark.postgres
def test_complete_selected_value_hashes_match_before_after_activation_and_recovery(verified_db, monkeypatch):
    identities, calls, baseline, prior, original, _ = prepare_v4(verified_db, monkeypatch, canonical=True, aum_rows=True)
    inputs = inputs_v2(identities, calls, canonical=True, aum_rows=True)
    old.publish(verified_db, identities, inputs, execution="selected-value-candidate")

    def reader_witnesses():
        # Every fund retained in this disposable fixture, not only the new pair.
        with psycopg.connect(verified_db, row_factory=dict_row) as conn:
            codes = [r["code"] for r in conn.execute("SELECT amfi_scheme_code AS code FROM mf.funds ORDER BY amfi_scheme_code")]
            inventory = book_store.capture(conn)
        for role in (None, *ROLES):
            with psycopg.connect(runtime_dsn(verified_db, role) if role else verified_db, row_factory=dict_row) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
                expected = original_selected(conn, codes)
                frozen = deepcopy(expected)
                assert engine._selected_value_rows(conn, codes) == frozen
                assert original_selected(conn, codes) == frozen
                assert conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
                assert engine._selected_value_rows(conn, [999999]) == []
        with psycopg.connect(verified_db, row_factory=dict_row) as conn:
            assert book_store.capture(conn) == inventory
            assert source_state(conn) == original

    reader_witnesses()
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert book_preservation.capture(conn, 4) == prior
        conn.commit()
        activated = book_projection.activate(conn, ROOT, old.references(inputs[0][0]),
                                             expected_configuration_hash=fingerprint(baseline))
        # The bounded writer transaction is now already committed. Its LOCAL
        # limits are asserted inside publication by the protocol regression.
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        engine.compare(prior, book_preservation.capture(conn, 4))
    reader_witnesses()
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert book_projection.recover(conn, ROOT, activated["transition_id"])["status"] == "RECOVERED"
        assert book_projection.recover(conn, ROOT, activated["transition_id"])["status"] == "ALREADY_RECOVERED"
        assert current.configuration(conn)["policy_hash"] == baseline["policy_hash"]
    reader_witnesses()
