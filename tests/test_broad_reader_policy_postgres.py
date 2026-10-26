"""Disposable PG18 mechanics only, no local/provider database authority."""

from datetime import datetime, timedelta, timezone

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from mfdataindia.review import broad_reader_policy as p
from test_reader_access_correction_postgres import fixture_db as fixture_db
from test_reader_access_correction_postgres import owner, rows

pytestmark = pytest.mark.postgres


def state(dsn):
    with owner(dsn) as conn:
        return conn.execute(p.STATE_SQL).fetchone()["value"]


def permit(before):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    window = dict(
        starts_at=(now - timedelta(seconds=10)).isoformat(),
        ends_at=(now + timedelta(minutes=5)).isoformat(),
        exclusive_administration=True,
        data_schema_custody=True,
        future_owner_provisioning=True,
    )
    document = p.approval_document(p.prepare(before), window=window)
    return p.Permit(document, p.fingerprint(document))


def reader(dsn):
    return psycopg.connect(
        make_conninfo(dsn, user=p.READER, password="column-fixture-only"),
        autocommit=True,
        row_factory=dict_row,
    )


@pytest.mark.parametrize("creator", ["owner", "other"])
def test_native_schema_local_public_future_read_rejects_without_changes(fixture_db, creator):
    with (
        owner(fixture_db) if creator == "owner" else psycopg.connect(fixture_db, autocommit=True)
    ) as conn:
        conn.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA mf GRANT SELECT ON TABLES TO PUBLIC")
    before, original = state(fixture_db), rows(fixture_db)
    with pytest.raises(p.PolicyError, match="PUBLIC_FUTURE_DATA_READ"):
        p.prepare(before)
    assert state(fixture_db) == before and rows(fixture_db) == original


@pytest.mark.parametrize("grantee", [p.READER, "PUBLIC"])
def test_native_global_future_read_rejects_before_changes_and_preserves_state(fixture_db, grantee):
    with owner(fixture_db) as conn:
        conn.execute(
            sql.SQL("ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO {}").format(
                sql.Identifier(grantee) if grantee != "PUBLIC" else sql.SQL("PUBLIC")
            )
        )
    before, original = state(fixture_db), rows(fixture_db)
    with pytest.raises(p.PolicyError, match="GLOBAL_TABLE_READ"):
        p.prepare(before)
    assert state(fixture_db) == before and rows(fixture_db) == original


@pytest.mark.parametrize("column", [False, True])
def test_native_public_current_read_rejects_before_changes_and_preserves_state(fixture_db, column):
    with owner(fixture_db) as conn:
        conn.execute(
            "GRANT SELECT"
            + ("(amfi_scheme_code)" if column else "")
            + " ON mf.fund_variants TO PUBLIC"
        )
    before, original = state(fixture_db), rows(fixture_db)
    with pytest.raises(p.PolicyError, match="PUBLIC_DATA_READ"):
        p.prepare(before)
    assert state(fixture_db) == before and rows(fixture_db) == original


def test_native_schema_usage_cannot_unlock_existing_security_definer_mutation(fixture_db):
    original = rows(fixture_db)
    with owner(fixture_db) as conn:
        conn.execute(
            "CREATE FUNCTION mf_review.broad_owner_mutation() RETURNS void LANGUAGE SQL SECURITY DEFINER AS $$ UPDATE mf.fund_variants SET group_key='unauthorized' $$"
        )
    with reader(fixture_db) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT mf_review.broad_owner_mutation()")
    before = state(fixture_db)
    with pytest.raises(p.PolicyError, match="PRIVILEGED_DOMAIN_FUNCTION"):
        p.prepare(before)
    assert state(fixture_db) == before and rows(fixture_db) == original
    with owner(fixture_db) as conn:
        conn.execute("REVOKE EXECUTE ON FUNCTION mf_review.broad_owner_mutation() FROM PUBLIC")
        p.apply(conn, permit(state(fixture_db)))
    with reader(fixture_db) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT mf_review.broad_owner_mutation()")
        assert conn.execute("SELECT group_key FROM mf.fund_variants").fetchone() == {
            "group_key": "SYNTHETIC"
        }
    assert rows(fixture_db) == original


def test_native_broad_reads_exact_catalogue_delta_preserve_all_rows_and_preexisting_grants(
    fixture_db,
):
    with owner(fixture_db) as conn:
        conn.execute("GRANT SELECT(amfi_scheme_code) ON mf.fund_variants TO mfdata_verified_reader")
    before, original = state(fixture_db), rows(fixture_db)
    plan = p.prepare(before)
    with owner(fixture_db) as conn:
        result = p.apply(conn, permit(before))
    assert result["stage"] == "COMMITTED_VERIFIED"
    assert p.catalogue(state(fixture_db)) == plan["after"] and rows(fixture_db) == original
    assert p.prepare(state(fixture_db))["sql"] == []
    assert p.require_current_policy(state(fixture_db))["present_schemas"] == ["mf", "mf_review"]
    with reader(fixture_db) as conn:
        assert len(conn.execute("SELECT * FROM mf.current_fund_facts").fetchone()) == 84
        assert conn.execute(
            "SELECT group_key,base_scheme_name FROM mf.fund_variants"
        ).fetchone() == {"group_key": "SYNTHETIC", "base_scheme_name": "preserve"}
        assert conn.execute(
            "SELECT has_column_privilege(current_user,'mf.fund_variants','base_scheme_name','SELECT WITH GRANT OPTION') AS allowed"
        ).fetchone() == {"allowed": False}
        for command in (
            "UPDATE mf.fund_variants SET group_key='bad'",
            "DELETE FROM mf.fund_variants",
            "CREATE TABLE mf.unauthorized(x int)",
            "SELECT nextval('mf.fixture_sequence')",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(command)
        # PostgreSQL can warn/no-op rather than raise when the role has SELECT
        # but no grant option. Exact ACL equality is the independent denial proof.
        try:
            conn.execute("GRANT SELECT ON mf.fund_variants TO PUBLIC")
        except psycopg.errors.InsufficientPrivilege:
            pass
    assert p.catalogue(state(fixture_db)) == plan["after"]


@pytest.mark.parametrize("kind", ["table", "view", "materialized", "partition"])
def test_native_future_owner_data_objects_and_new_columns_are_read_without_entitlement_edit(
    fixture_db, kind
):
    with owner(fixture_db) as conn:
        p.apply(conn, permit(state(fixture_db)))
        conn.execute("ALTER TABLE mf.fund_variants ADD COLUMN future_fact text DEFAULT 'new-fact'")
        if kind == "table":
            conn.execute("CREATE TABLE mf.future_data AS SELECT 7 AS value")
        elif kind == "view":
            conn.execute("CREATE VIEW mf.future_data AS SELECT 7 AS value")
        elif kind == "materialized":
            conn.execute("CREATE MATERIALIZED VIEW mf.future_data AS SELECT 7 AS value")
        else:
            conn.execute("CREATE TABLE mf.future_parent(value integer) PARTITION BY RANGE(value)")
            conn.execute(
                "CREATE TABLE mf.future_data PARTITION OF mf.future_parent FOR VALUES FROM(0) TO(10)"
            )
            conn.execute("INSERT INTO mf.future_parent VALUES(7)")
    with reader(fixture_db) as conn:
        assert conn.execute("SELECT value FROM mf.future_data").fetchone() == {"value": 7}
        assert conn.execute("SELECT future_fact FROM mf.fund_variants").fetchone() == {
            "future_fact": "new-fact"
        }
    assert p.require_current_policy(state(fixture_db))["future_creator"] == p.OWNER


def test_native_new_view_column_also_inherits_table_select(fixture_db):
    with owner(fixture_db) as conn:
        p.apply(conn, permit(state(fixture_db)))
        conn.execute(
            "CREATE OR REPLACE VIEW mf.current_fund_facts AS SELECT f.*,7 AS future_view_fact FROM mf.facts_fixture f"
        )
    with reader(fixture_db) as conn:
        assert conn.execute("SELECT future_view_fact FROM mf.current_fund_facts").fetchone() == {
            "future_view_fact": 7
        }


def test_native_other_creator_and_outside_schema_are_not_falsely_claimed_covered(fixture_db):
    with owner(fixture_db) as conn:
        p.apply(conn, permit(state(fixture_db)))
    with psycopg.connect(fixture_db, autocommit=True) as admin:
        admin.execute("CREATE TABLE mf.other_creator(value integer)")
        admin.execute("CREATE TABLE public.outside_fund_domain(value integer)")
    with reader(fixture_db) as conn:
        for command in (
            "SELECT * FROM mf.other_creator",
            "SELECT * FROM public.outside_fund_domain",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(command)
    with pytest.raises(p.PolicyError, match="DIRECT_OBJECT_OWNER"):
        p.require_current_policy(state(fixture_db))


def test_native_other_grantee_global_defaults_and_function_sequence_capabilities_preserved(
    fixture_db,
):
    with owner(fixture_db) as conn:
        conn.execute("ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO mfdata_verified_admin")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA mf GRANT UPDATE ON TABLES TO mfdata_verified_admin"
        )
        conn.execute(
            "REVOKE EXECUTE ON FUNCTION mf_review.append_event(integer,text,text,text,text,text,jsonb) FROM PUBLIC"
        )
    before = state(fixture_db)
    with owner(fixture_db) as conn:
        p.apply(conn, permit(before))
    after = p.catalogue(state(fixture_db))
    assert after == p.prepare(before)["after"]
    assert after["functions"] == p.catalogue(before)["functions"]
    assert [r for r in after["relations"] if r["relkind"] == "S"] == [
        r for r in p.catalogue(before)["relations"] if r["relkind"] == "S"
    ]
    with reader(fixture_db) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT mf_review.append_event(1,'x','x','x','x','x','{}')")


def test_native_partial_write_error_rolls_back_every_acl_and_default_change(fixture_db):
    before, original = state(fixture_db), rows(fixture_db)
    with owner(fixture_db) as conn:

        class FailSecondGrant:
            autocommit = True
            info = conn.info
            grants = 0

            def execute(self, statement, **kwargs):
                if statement.startswith("GRANT"):
                    self.grants += 1
                    if self.grants == 2:
                        return conn.execute(
                            "SELECT * FROM pg_catalog.no_such_broad_fixture_relation", **kwargs
                        )
                return conn.execute(statement, **kwargs)

        adapter = FailSecondGrant()
        with pytest.raises(p.PolicyError, match="PRECOMMIT_ABORTED_NO_RETRY"):
            p.apply(adapter, permit(before))
        assert adapter.grants == 2
    assert p.catalogue(state(fixture_db)) == p.catalogue(before) and rows(fixture_db) == original


@pytest.mark.parametrize("defect", ["rls", "foreign"])
def test_native_unsupported_scope_is_rejected_before_any_grant(fixture_db, defect):
    with owner(fixture_db) as conn:
        if defect == "rls":
            conn.execute("ALTER TABLE mf.fund_variants ENABLE ROW LEVEL SECURITY")
        else:
            with psycopg.connect(fixture_db, autocommit=True) as admin:
                admin.execute("CREATE FOREIGN DATA WRAPPER broad_fixture_fdw")
                admin.execute(
                    "CREATE SERVER broad_fixture_server FOREIGN DATA WRAPPER broad_fixture_fdw"
                )
                admin.execute(
                    "CREATE FOREIGN TABLE mf.foreign_fixture(value integer) SERVER broad_fixture_server"
                )
                admin.execute(
                    sql.SQL("ALTER FOREIGN TABLE mf.foreign_fixture OWNER TO {}").format(
                        sql.Identifier(p.OWNER)
                    )
                )
    before = state(fixture_db)
    with pytest.raises(p.PolicyError, match="RLS|FOREIGN"):
        p.prepare(before)
    assert state(fixture_db) == before
