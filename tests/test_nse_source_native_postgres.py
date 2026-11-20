"""Hosted disposable PostgreSQL and the real private core/API dataset paths.

ASGI proves application responses, not Cloud Run IAM or live TLAM acceptance.
"""

from copy import deepcopy
from hashlib import sha256
import json
from time import perf_counter
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.conninfo import make_conninfo
import pytest

from mfdataindia.api.app import create_app
from mfdataindia.datasets.policy import dataset_id, DatasetError
from mfdataindia.nse import (
    contract as c,
    sch44,
    source_native as native,
    store,
    source_roles,
    source_storage,
)
from test_nse01_postgres import ro01_db as ro01_db, api as dataset_api
from test_ro01_reader_policy_postgres import connect, PASSWORD, generator_capabilities, adopt
from test_nse_sch44_reconcile import source, amfi, ISIN

pytestmark = pytest.mark.postgres


@pytest.fixture
def nse_source_db(ro01_db, pg_dsn):
    adopt(ro01_db)
    with connect(ro01_db, autocommit=False) as owner:
        owner.execute(
            "INSERT INTO mf.nav_history(amfi_scheme_code,nav_date,nav,source) VALUES(1,'2026-11-09',12.3456,'AMFI')"
        )
        owner.execute(
            "INSERT INTO mf.nav_history(amfi_scheme_code,nav_date,nav,source) VALUES(2,'2026-11-09',12345678901234.1234,'AMFI')"
        )
    identities = (source_roles.OWNER, source_roles.WRITER)
    with psycopg.connect(pg_dsn, autocommit=True) as root:
        for name in identities:
            root.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(name), sql.Literal(PASSWORD))
            )
        try:
            with psycopg.connect(
                make_conninfo(pg_dsn, dbname="mfdataindia"), autocommit=True
            ) as admin:
                admin.execute(
                    sql.SQL("CREATE SCHEMA mf_nse AUTHORIZATION {}").format(
                        sql.Identifier(source_roles.OWNER)
                    )
                )
                admin.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA mf TO {}").format(
                        sql.Identifier(source_roles.WRITER)
                    )
                )
                admin.execute(
                    sql.SQL(
                        "GRANT SELECT ON mf.funds,mf.amcs,mf.nav_history,mf.dataset_summary TO {}"
                    ).format(sql.Identifier(source_roles.WRITER))
                )
            yield ro01_db
        finally:
            with psycopg.connect(
                make_conninfo(pg_dsn, dbname="mfdataindia"), autocommit=True
            ) as admin:
                for name in identities:
                    admin.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(name)))
            for name in reversed(identities):
                root.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))


def head(dsn):
    with connect(dsn, source_roles.OWNER) as conn:
        value = conn.execute(
            "SELECT snapshot_id,generation FROM mf_nse.family_heads WHERE family='SCH'"
        ).fetchone()
        return str(value["snapshot_id"]) if value["snapshot_id"] else None, value["generation"]


def prepare(dsn, raw=None, *, source_date="17112026", reason_cases=False):
    if raw is None:
        header, cell = source(flag="Y").decode().splitlines()
        cells = cell.split("|")
        cells[0] = "000001"
        cells[2] = "00007"
        cells[11] = "000.2500"
        second = cells.copy()
        second[0] = "000002"
        second[1] = "SYNTH_A-L1"
        cells[28] = "Y"
        cells[32] = "T1"
        second[28] = "N"
        second[32] = "L1"
        sif = cells.copy()
        sif[0] = "000003"
        sif[1] = "SIF_A"
        sif[5] = "FIXTURE_SIF"
        held = cells.copy()
        held[0] = "000004"
        held[1] = "UNKNOWN_ROUTE"
        held[5] = "UNKNOWN_ISSUER_MF"
        rows = [cells, second, sif, held]
        if reason_cases:
            unmatched = cells.copy()
            unmatched[0:2] = ["000005", "NO_CATALOGUE_ROUTE"]
            unmatched[4] = "INZZ00000027"
            other, unknown = cells.copy(), cells.copy()
            other[0:2] = ["000006", "OTHER_PRODUCT"]
            unknown[0:2] = ["000007", "UNCLASSIFIED_PRODUCT"]
            rows.extend([unmatched, other, unknown])
        raw = ("\n".join([header, *("|".join(r) for r in rows)]) + "\n").encode()
    with connect(dsn) as conn:
        funds = conn.execute(store.FUNDS).fetchall()
        version = conn.execute(
            "SELECT dataset_version FROM mf.dataset_summary WHERE singleton"
        ).fetchone()["dataset_version"]
    cat = c.canonical(
        {
            "format": sch44.CATALOGUE_FORMAT,
            "as_of": "2026-11-17",
            "dataset_version": str(version),
            "funds": funds,
        }
    )
    products = {
        "source_sha256": sha256(raw).hexdigest(),
        "rows": [
            {
                "source_line": i,
                "disposition": p,
                "reason": "FIXTURE_PRODUCT_EVIDENCE",
                "evidence_sha256": "1" * 64,
            }
            for i, p in enumerate(
                ["MF_EXACT", "MF_EXACT", "SIF", "MF_HELD"]
                + (["MF_UNMATCHED", "OTHER_VERIFIED_OUT_OF_SCOPE", "UNCLASSIFIED"] if reason_cases else []),
                2,
            )
        ],
    }
    provenance = {
        "channel": "NSE_PRODUCTION_WEBSITE",
        "filename": f"NSE_NSEINVEST_ALL_{source_date}.txt",
        "retrieved_at_utc": "2026-11-17T13:03:30.761Z",
        "object_uri": "gs://synthetic-private/source.txt",
        "object_generation": "123",
        "sha256": sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "row_count": len(products["rows"]),
    }
    earlier = b"\n".join(raw.splitlines()[:2]) + b"\n"
    earlier = earlier.replace(b"title ignored", b"earlier title")  # no unknown fourth route witness
    first = amfi("isin_growth_or_div_payout").replace(b"100001;", b"1;")
    second = amfi("isin_growth_or_div_payout", date="16-Nov-2026").replace(b"100001;", b"1;")
    plan = native.prepare(
        raw, provenance, cat, earlier, first, second, products, c.digest(products)
    )
    return raw, plan


def publish(dsn, raw, plan, expected=None):
    with connect(dsn, source_roles.WRITER, autocommit=False) as conn:
        return native.publish(conn, raw, plan, c.digest(plan), str(uuid4()), expected_head=expected)


def read(dsn):
    return TestClient(
        create_app(make_conninfo(dsn, user="mfdata_verified_reader", password=PASSWORD))
    )


def baseline(dsn):
    with connect(dsn) as conn:
        return {
            "canonical": store._fingerprint(conn),
            "full_head": head(dsn),
            "families": conn.execute("SELECT * FROM mf.fund_family ORDER BY group_key").fetchall(),
        }


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_stored_dataset_reasons_follow_final_disposition(nse_source_db):
    dsn = nse_source_db
    with connect(dsn, autocommit=False) as owner:
        owner.execute("UPDATE mf.amcs SET amfi_amc_name='Fixture Mutual Fund' WHERE amc_id=1")
        owner.execute(
            "UPDATE mf.funds SET option_type='IDCW',isin_growth_or_div_payout=%s WHERE amfi_scheme_code=1",
            (ISIN,),
        )
    with connect(dsn, source_roles.OWNER, autocommit=False) as owner:
        owner.execute("SELECT 1")
        native.install(owner)
    before = baseline(dsn)
    raw, plan = prepare(dsn, reason_cases=True)
    assert publish(dsn, raw, plan)["effect"] == "COMMITTED_VERIFIED"
    with dataset_api(dsn) as client:
        response = client.get(
            "/api/datasets/v1/" + dataset_id("mf_nse", "source_routes") + "/rows",
            params={"limit": 100},
        )
        assert response.status_code == 200, response.text
        rows = {r["source_line"]: r for r in response.json()["rows"]}
    assert len(rows) == 7
    assert (rows[5]["disposition"], rows[5]["reason"]) == (
        "MF_HELD", "ISSUER_NOT_CORROBORATED"
    )
    assert (rows[6]["disposition"], rows[6]["reason"]) == (
        "MF_UNMATCHED", "NO_CATALOGUE_ISIN_SLOT"
    )
    for line, disposition in ((4, "SIF"), (7, "OTHER_VERIFIED_OUT_OF_SCOPE"), (8, "UNCLASSIFIED")):
        assert (rows[line]["disposition"], rows[line]["reason"]) == (
            disposition, "FIXTURE_PRODUCT_EVIDENCE"
        )
    assert baseline(dsn) == before


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_source_all_fields_routes_api_replay_revision_and_preservation(
    nse_source_db, pg_dsn
):
    dsn = nse_source_db
    with connect(dsn, autocommit=False) as owner:
        owner.execute("UPDATE mf.amcs SET amfi_amc_name='Fixture Mutual Fund' WHERE amc_id=1")
        owner.execute(
            "UPDATE mf.funds SET option_type='IDCW',isin_growth_or_div_payout=%s WHERE amfi_scheme_code=1",
            (ISIN,),
        )
    with connect(dsn, source_roles.OWNER, autocommit=False) as owner:
        owner.execute("SELECT 1")
        native.install(owner)
        native.install(owner)
        assert (
            owner.execute("SELECT version FROM mf_nse.configuration").fetchone()["version"]
            == source_storage.PROFILE
        )
    before = baseline(dsn)
    generator = generator_capabilities(dsn)
    with read(dsn) as client:
        legacy = client.get("/api/funds/1")
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["nse"]["status"] == "NO_VERIFIED_LINK"
    raw, plan = prepare(dsn)
    assert plan["metadata"]["counts"] == {"MF_EXACT": 2, "SIF": 1, "MF_HELD": 1}
    start = perf_counter()
    result = publish(dsn, raw, plan)
    assert result["effect"] == "COMMITTED_VERIFIED"
    assert (
        result["format"] == "nse-source-native-result-v1" and not result["full_sch_head_advanced"]
    )
    with connect(dsn, source_roles.WRITER, autocommit=False) as observer:
        inspection = native.inspect(observer, result["operation_id"])
        assert inspection["active_snapshot_id"] == result["snapshot_id"]
        assert inspection["effect"] == "COMMITTED_VERIFIED"

    with read(dsn) as client:
        response = client.get("/api/funds/1")
        assert response.status_code == 200, response.text
        detail = response.json()
        section = detail.pop("nse")
        old = legacy.json()
        old.pop("nse")
        assert detail == old
        routes = section["routes"]
        assert len(routes) == 2
        assert [r["fields"]["SCHEME CODE"] for r in routes] == ["SYNTH_A", "SYNTH_A-L1"]
        assert [r["source_sip_allowed"] for r in routes] == [True, False]
        assert [r["fields"]["SETTLEMENT TYPE"] for r in routes] == ["T1", "L1"]
        assert [r["identity_evidence"]["historical_route"] for r in routes] == [
            "CORROBORATED",
            "ABSENT_NO_ADVERSE_HISTORY",
        ]
        assert routes[1]["identity_evidence"]["earlier_nse_line"] is None
        assert all(len(r["identity_evidence"]["amfi_witnesses"]) == 2 for r in routes)
        assert all(r["identity_evidence"]["amfi_scheme_code"] == 1 for r in routes)
        for row, (cells, holds) in zip(routes, sch44.parse(raw)):
            assert row["fields"] == dict(zip(native.HEADERS44, cells))
            assert len(row["fields"]) == 44
            assert row["fields"]["UNIQUE SR NO"].startswith("00000")
            assert row["fields"]["NEW PURCHASE MIN AMOUNT"] == "000.2500"
            assert row["fields"]["DIV REINVEST FLAG"] == "Y"
            assert row["amfi_isin_slot"] == "isin_growth_or_div_payout"
            assert row["flag_slot_disagreement"] == "SOURCE_FLAG_SLOT_DISAGREEMENT"
            assert row["snapshot_id"] == result["snapshot_id"]
        assert client.get("/api/funds/2").json()["nse"]["status"] == "NO_VERIFIED_LINK"
        assert client.post("/api/funds/1", json={}).status_code == 405
    with dataset_api(dsn) as client:
        url = "/api/datasets/v1/" + dataset_id("mf_nse", "source_routes") + "/rows"
        response = client.get(url, params={"limit": 100})
        assert response.status_code == 200, response.text
        assert len(response.json()["rows"]) == 4  # source-native includes held/SIF, not attached
        page = client.get(url, params={"limit": 2}).json()
        later = client.get(url, params={"limit": 2, "cursor": page["next_cursor"]}).json()
        assert len(page["rows"]) == len(later["rows"]) == 2 and later["next_cursor"] is None
        assert {r["source_line"] for r in page["rows"] + later["rows"]} == {2, 3, 4, 5}
        assert {r["disposition"] for r in page["rows"] + later["rows"]} == {
            "MF_EXACT",
            "MF_HELD",
            "SIF",
        }
    # Current identity drift can withhold an old exact link but never create a
    # replacement. Read on the same real transaction; rollback fixture change.
    with psycopg.connect(make_conninfo(pg_dsn, dbname="mfdataindia"), row_factory=dict_row) as conn:
        conn.execute("UPDATE mf.funds SET isin_growth_or_div_payout=NULL WHERE amfi_scheme_code=1")
        assert native.fund_section(conn, 1)["status"] == "NO_VERIFIED_LINK"
        conn.rollback()
    replay = publish(dsn, raw, plan)
    assert replay["state"] == "UNCHANGED" and not replay["head_advanced"]
    retrieved_again = deepcopy(plan)
    retrieved_again["metadata"].update(
        retrieved_at_utc="2026-11-19T10:00:00Z", object_generation="456"
    )
    observation = publish(dsn, raw, retrieved_again)
    assert observation["state"] == "UNCHANGED" and not observation["head_advanced"]
    assert observation["snapshot_id"] == result["snapshot_id"]

    assert baseline(dsn) == before and generator_capabilities(dsn) == generator
    # Same filename generation date, changed source bytes. Old snapshot remains.
    revised = raw.replace(b"Current title", b"Revised title")
    revised_raw, revised_plan = prepare(dsn, revised)
    revision = publish(dsn, revised_raw, revised_plan, result["snapshot_id"])
    assert revision["snapshot_id"] != result["snapshot_id"]
    retired_replay = publish(dsn, raw, plan)
    assert retired_replay["state"] == "UNCHANGED" and not retired_replay["head_advanced"]
    with read(dsn) as client:
        assert (
            client.get("/api/funds/1").json()["nse"]["routes"][0]["snapshot_id"]
            == revision["snapshot_id"]
        )

    with connect(dsn, source_roles.OWNER) as conn:
        assert conn.execute("SELECT count(*) AS n FROM mf_nse.snapshots").fetchone()["n"] == 2
        assert conn.execute("SELECT count(*) AS n FROM mf_nse.sch_rows").fetchone()["n"] == 8
        assert (
            conn.execute("SELECT generation FROM mf_nse.source_head").fetchone()["generation"] == 2
        )
    # A real BEFORE INSERT corruption must be found by complete readback,
    # not a fake success/failure report. The source writer cannot bypass it.
    next_raw, next_plan = prepare(dsn, raw.replace(b"Current title", b"Later title"))
    with connect(dsn, source_roles.OWNER, autocommit=False) as owner:
        owner.execute("""CREATE FUNCTION mf_nse.fixture_corrupt() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN NEW.original_cells=jsonb_set(NEW.original_cells,'{8}','"fixture-corrupt"');
            RETURN NEW; END $$;
            CREATE TRIGGER fixture_corrupt BEFORE INSERT ON mf_nse.sch_rows
            FOR EACH ROW EXECUTE FUNCTION mf_nse.fixture_corrupt()""")
    try:
        with pytest.raises(store.OutcomeError, match="SOURCE_FULL_READBACK_DIFFERS") as error:
            publish(dsn, next_raw, next_plan, revision["snapshot_id"])
    finally:
        with connect(dsn, source_roles.OWNER, autocommit=False) as owner:
            owner.execute(
                "DROP TRIGGER fixture_corrupt ON mf_nse.sch_rows; DROP FUNCTION mf_nse.fixture_corrupt()"
            )
    assert error.value.report["effect"] == "ROLLBACK_CONFIRMED"
    assert error.value.report["head_kind"] == "SOURCE_REFERENCE"
    with read(dsn) as client:
        assert (
            client.get("/api/funds/1").json()["nse"]["routes"][0]["snapshot_id"]
            == revision["snapshot_id"]
        )
    assert baseline(dsn) == before and generator_capabilities(dsn) == generator
    for statement in (
        "DELETE FROM mf_nse.source_head",
        "UPDATE mf_nse.source_head SET generation=0",
        "CREATE TABLE mf_nse.forbidden(x int)",
        "ALTER ROLE mfdata_verified_reader CREATEDB",
        "SELECT * FROM mf_nse.source_artifacts",
    ):
        with connect(dsn, "mfdata_verified_reader") as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    print(
        json.dumps(
            {
                "source_native_native_seconds": round(perf_counter() - start, 4),
                "source_rows": 4,
                "literal_fields_per_route": 44,
                "canonical_preserved": True,
            }
        )
    )


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_native_source_role_scope_rejects_shared_elevation_and_preserves_canonical(
    nse_source_db, pg_dsn
):
    from mfdataindia.datasets import catalogue, enrollment

    dsn = nse_source_db
    before = baseline_before_install(dsn)
    with connect(dsn, autocommit=False) as shared:
        shared.execute("SELECT 1")
        with pytest.raises(c.ImportError, match="NSE_SOURCE_IDENTITY_UNSAFE"):
            native.install(shared)
        shared.rollback()
    with connect(dsn, source_roles.OWNER, autocommit=False) as owner:
        owner.execute("SELECT 1")
        owner.execute("CREATE TYPE mf_nse.fixture_occupied AS ENUM ('retained')")
        with pytest.raises(c.ImportError, match="NSE_SOURCE_EXISTING_NAMESPACE_INCOMPATIBLE"):
            native.install(owner)
        owner.execute("DROP TYPE mf_nse.fixture_occupied")
        native.install(owner)
        assert catalogue.creator_supported("mf_nse", source_roles.OWNER)
        assert not catalogue.creator_supported("mf", source_roles.OWNER)
        assert not catalogue.creator_supported("mf_unrelated", source_roles.OWNER)
        with pytest.raises(DatasetError, match="NSE_SOURCE_READ_MODEL_ONLY"):
            enrollment.enroll(
                owner,
                "mf_nse",
                "source_artifacts",
                classification="fund_research",
                title="forbidden",
            )
    assert baseline_before_install(dsn) == before
    with connect(dsn, source_roles.WRITER) as writer:
        assert not writer.execute(
            "SELECT has_database_privilege(current_database(),'CREATE') AS value"
        ).fetchone()["value"]
        for statement in (
            "UPDATE mf.funds SET scheme_name=scheme_name",
            "DELETE FROM mf.nav_history",
            "UPDATE mf_nse.family_heads SET generation=0",
            "DELETE FROM mf_nse.current_sch",
            "CREATE TABLE mf_nse.forbidden(x int)",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                writer.execute(statement)
    # A newly introduced business write grant must stop source publication too.
    with psycopg.connect(make_conninfo(pg_dsn, dbname="mfdataindia"), autocommit=True) as admin:
        admin.execute(
            sql.SQL("GRANT UPDATE ON mf.funds TO {}").format(sql.Identifier(source_roles.WRITER))
        )
    try:
        with connect(dsn, source_roles.WRITER, autocommit=False) as writer:
            writer.execute("SELECT 1")
            with pytest.raises(c.ImportError, match="NSE_SOURCE_IDENTITY_UNSAFE"):
                source_roles.identity(writer)
            writer.rollback()
    finally:
        with psycopg.connect(make_conninfo(pg_dsn, dbname="mfdataindia"), autocommit=True) as admin:
            admin.execute(
                sql.SQL("REVOKE UPDATE ON mf.funds FROM {}").format(
                    sql.Identifier(source_roles.WRITER)
                )
            )
    assert baseline_before_install(dsn) == before


def baseline_before_install(dsn):
    with connect(dsn) as conn:
        return store._fingerprint(conn)
