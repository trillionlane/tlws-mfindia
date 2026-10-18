"""Read equivalence of the installed graph, not alternate selection semantics."""

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import book_preservation, current_graph_read as shared
from mfdataindia.enrichment import current_facts_store as current
from mfdataindia.review.policy import fingerprint
from test_book_preservation_reader import active_prior
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES, source_state
from test_verified_review_store import verified_db as verified_db


def test_substitution_retains_literals_aliases_and_quoted_text():
    original = (
        "SELECT field_versions.*, 'mf_live_facts.field_versions', "
        "'quoted '' mf_live_facts.field_versions', \"mf_live_facts.field_versions\" "
        "FROM mf_live_facts.field_versions;\n"
    )
    rewritten = shared.reuse(original, ("field_versions",))
    assert rewritten == original.replace("FROM mf_live_facts.field_versions;\n", "FROM field_versions")


def test_missing_actual_dependency_is_rejected_not_guessed():
    with pytest.raises(ValueError, match="not reusable"):
        shared.reuse("SELECT 'mf_live_facts.field_versions'", ("field_versions",))


@pytest.mark.parametrize("codes", [[], [True], [0], [-1], [1, 1], ["1"], list(range(1, 4098))])
def test_unbounded_or_ambiguous_codes_fail_before_database_access(codes):
    with pytest.raises(ValueError, match="bounded distinct"):
        shared.graph(None, codes)


@pytest.mark.parametrize("defect", ["missing", "owner", "options"])
def test_context_drift_rejected_before_interpreting_views(defect):
    class Connection:
        def execute(self, query, params):
            self.rows = [
                {"relname": name, "relowner": 1, "reloptions": None}
                for name in shared.ORDER[:3]
            ]
            if defect == "missing":
                self.rows.pop()
            elif defect == "owner":
                self.rows[0]["relowner"] = 2
            else:
                self.rows[0]["reloptions"] = ["security_invoker=true"]
            return self

        def fetchall(self):
            return self.rows

    with pytest.raises(ValueError, match="contexts differ"):
        shared.graph(Connection(), [1])


def original_fields(conn, code):
    return conn.execute(
        "SELECT h.field,h.as_of,h.distinct_values,h.usable,h.sources AS latest_sources,"
        "s.as_of AS selected_as_of,s.value,s.sources FROM mf_live_facts.field_heads h "
        "LEFT JOIN mf_live_facts.selected_fields s USING(code,field) "
        "WHERE h.code=%s ORDER BY h.field", (code,),
    ).fetchall()


def original_hashes(conn, codes):
    result = {}
    for relation, code, order in (
        ("mf_live_facts.field_versions", "code", "code,field,as_of"),
        ("mf_live_facts.selected_fields", "code", "code,field"),
        ("mf_live_facts.current_values", "code", "code"),
        ("mf_live_facts.resolved", "amfi_scheme_code", "amfi_scheme_code"),
    ):
        rows = conn.execute(
            sql.SQL("SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS sha256 "
                    "FROM {} t WHERE {}=ANY(%s) ORDER BY {}").format(
                        sql.Identifier(*relation.split(".")), sql.Identifier(code), sql.SQL(order)),
            (codes,),
        ).fetchall()
        key = "mf.current_fund_facts" if relation.endswith("resolved") else relation
        result[key] = {"rows": len(rows), "sha256": fingerprint(rows)}
    return result


@pytest.mark.postgres
@pytest.mark.parametrize("role", [None, *ROLES])
def test_full_prior_population_hashes_and_single_fund_rows_match_original_without_writes(
    verified_db, monkeypatch, role
):
    active_prior(verified_db, monkeypatch)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        before = source_state(conn)
    with psycopg.connect(runtime_dsn(verified_db, role) if role else verified_db, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        current.verify_acl(conn)
        proof = book_preservation.capture(conn, 1)
        assert proof["hashes"] == original_hashes(conn, proof["codes"])
        for code in [*proof["codes"], 1, 999999]:
            assert shared.fields(conn, code) == original_fields(conn, code)
        rendered = shared.graph(conn, proof["codes"], complete=True).as_string(conn)
        assert rendered.count("FROM mf_live_facts.field_versions") == 1
        assert "field_versions AS MATERIALIZED" in rendered
        assert "FROM mf_live_facts.selected_fields" not in rendered
        # A request gets a new statement-local result, not cached facts.
        assert proof == book_preservation.capture(conn, 1)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert source_state(conn) == before


@pytest.mark.postgres
def test_current_uti_graph_and_recovery_keep_exact_selection_and_row_hashes(
    verified_db, monkeypatch
):
    from test_uti_store import prepared, publish, references, ROOT
    from mfdataindia.enrichment import book_projection

    identities, inputs, prior, preserved, _ = prepared(verified_db, monkeypatch)
    publish(verified_db, identities, inputs)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        activated = book_projection.activate(
            conn, ROOT, references(inputs[0][0]),
            expected_configuration_hash=fingerprint(prior),
        )
        assert book_preservation.capture(conn, 3) == preserved
        current.verify_acl(conn)
        for code in [i["code"] for i in identities]:
            assert shared.fields(conn, code) == original_fields(conn, code)
        proof = book_preservation.capture(conn, 3)
        assert proof["hashes"] == original_hashes(conn, proof["codes"])
        book_projection.recover(conn, ROOT, activated["transition_id"])
        assert book_preservation.capture(conn, 3) == preserved
        assert book_projection.recover(conn, ROOT, activated["transition_id"])["status"] == "ALREADY_RECOVERED"
