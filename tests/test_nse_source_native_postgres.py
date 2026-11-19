"""Hosted disposable PostgreSQL and the real private core/API dataset paths.

ASGI proves application responses, not Cloud Run IAM or live TLAM acceptance.
"""

from hashlib import sha256
import json
from time import perf_counter
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from mfdataindia.api.app import create_app
from mfdataindia.datasets.policy import dataset_id
from mfdataindia.nse import contract as c, sch44, source_native as native, store
from test_nse01_postgres import nse_db as nse_db, ro01_db as ro01_db, api as dataset_api, head
from test_ro01_reader_policy_postgres import connect, PASSWORD, generator_capabilities
from test_nse_sch44_reconcile import source, amfi, ISIN

pytestmark = pytest.mark.postgres


def prepare(dsn, raw=None, *, source_date="17112026"):
    if raw is None:
        header, cell = source(flag="Y").decode().splitlines()
        cells = cell.split("|")
        cells[0] = "000001"
        cells[2] = "00007"
        cells[11] = "000.2500"
        second = cells.copy()
        second[0] = "000002"
        second[1] = "SYNTH_A-L1"
        sif = cells.copy()
        sif[0] = "000003"
        sif[1] = "SIF_A"
        sif[5] = "FIXTURE_SIF"
        held = cells.copy()
        held[0] = "000004"
        held[1] = "UNKNOWN_ROUTE"
        raw = (
            "\n".join([header, *("|".join(r) for r in [cells, second, sif, held])]) + "\n"
        ).encode()
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
            for i, p in enumerate(["MF_EXACT", "MF_EXACT", "SIF", "MF_HELD"], 2)
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
        "row_count": 4,
    }
    earlier = b"\n".join(raw.splitlines()[:4]) + b"\n"
    earlier = earlier.replace(b"title ignored", b"earlier title")  # no unknown fourth route witness
    first = amfi("isin_growth_or_div_payout").replace(b"100001;", b"1;")
    second = amfi("isin_growth_or_div_payout", date="16-Nov-2026").replace(b"100001;", b"1;")
    plan = native.prepare(
        raw, provenance, cat, earlier, first, second, products, c.digest(products)
    )
    return raw, plan


def publish(dsn, raw, plan, expected=None):
    with connect(dsn, "mfdata_ingest", autocommit=False) as conn:
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
def test_native_source_all_fields_routes_api_replay_revision_and_preservation(nse_db, monkeypatch):
    dsn = nse_db
    with connect(dsn, autocommit=False) as owner:
        owner.execute("UPDATE mf.amcs SET amfi_amc_name='Fixture Mutual Fund' WHERE amc_id=1")
        owner.execute(
            "UPDATE mf.funds SET option_type='IDCW',isin_growth_or_div_payout=%s WHERE amfi_scheme_code=1",
            (ISIN,),
        )
        native.install(owner)
        native.install(owner)
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
    # Current identity drift can withhold an old exact link but never create a
    # replacement. Read on the same real transaction; rollback fixture change.
    with connect(dsn, autocommit=False) as conn:
        conn.execute("UPDATE mf.funds SET isin_growth_or_div_payout=NULL WHERE amfi_scheme_code=1")
        assert native.fund_section(conn, 1)["status"] == "NO_VERIFIED_LINK"
        conn.rollback()
    replay = publish(dsn, raw, plan)
    assert replay["state"] == "UNCHANGED" and not replay["head_advanced"]
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

    with connect(dsn) as conn:
        assert conn.execute("SELECT count(*) AS n FROM mf_nse.snapshots").fetchone()["n"] == 2
        assert conn.execute("SELECT count(*) AS n FROM mf_nse.sch_rows").fetchone()["n"] == 8
        assert (
            conn.execute("SELECT generation FROM mf_nse.source_head").fetchone()["generation"] == 2
        )
    # Fault after complete readback, before commit. All old data/head survive.
    real_fingerprint = store._fingerprint
    calls = []

    def fail_late(conn):
        calls.append(1)
        return real_fingerprint(conn) if len(calls) == 1 else "injected-late-mismatch"

    next_raw, next_plan = prepare(dsn, raw.replace(b"Current title", b"Later title"))
    with monkeypatch.context() as fault:
        fault.setattr(store, "_fingerprint", fail_late)
        with pytest.raises(store.OutcomeError, match="CANONICAL_PRESERVATION_FAILED") as error:
            publish(dsn, next_raw, next_plan, revision["snapshot_id"])
    assert error.value.report["effect"] == "ROLLBACK_CONFIRMED"
    with read(dsn) as client:
        assert (
            client.get("/api/funds/1").json()["nse"]["routes"][0]["snapshot_id"]
            == revision["snapshot_id"]
        )
    assert baseline(dsn) == before and generator_capabilities(dsn) == generator
    for sql in (
        "DELETE FROM mf_nse.source_head",
        "UPDATE mf_nse.source_head SET generation=0",
        "CREATE TABLE mf_nse.forbidden(x int)",
        "ALTER ROLE mfdata_verified_reader CREATEDB",
        "SELECT * FROM mf_nse.source_artifacts",
    ):
        with connect(dsn, "mfdata_verified_reader") as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
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
