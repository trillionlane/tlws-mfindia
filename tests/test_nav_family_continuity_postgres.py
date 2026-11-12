from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from mfdataindia.ingest.amfi_client import FetchResult
from mfdataindia.ingest.amfi_navall import ParseReport
from mfdataindia.jobs import nav_continuity as nav
from mfdataindia.load.amfi_to_store import load_parsed_amfi
from mfdataindia.load.family_continuity import normalize_issuers
from mfdataindia.load.fund_family import build_fund_family
from mfdataindia.store.postgres import PostgresStore
from test_nav_family_continuity import DAY, scheme, sundaram_idcw, zerodha

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from refresh_daily import integrity_manifest, validate_dataset_summary  # noqa: E402


@pytest.fixture
def nav_store(pg_dsn):
    if not pg_dsn:
        pytest.skip("MF_TEST_DSN not set")
    name = "nav_continuity_test_" + uuid4().hex[:12]
    database = sql.Identifier(name)
    with psycopg.connect(pg_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(database))
        try:
            dsn = make_conninfo(pg_dsn, dbname=name)
            with PostgresStore(dsn, use_copy=False) as store:
                store.apply_migrations(ROOT / "sql")
                yield store
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(database))


def start(store, old_source):
    load_parsed_amfi(store, old_source, source_date=date(2026, 11, 4))
    build_fund_family(store)
    store.refresh_dataset_summary("native_test_baseline")
    store.apply_migrations(ROOT / "sql/optional", files=("046_fund_family_continuity.sql",))


def fetch():
    return FetchResult("https://portal.amfiindia.com/spages/NAVAll.txt", 200, "", "a" * 64, 1,
                       datetime(2026, 11, 11, 15, tzinfo=timezone.utc), 0, "FIXTURE")


def publish_catalogue(store, schemes):
    report = ParseReport(data_rows=len(schemes), distinct_amcs=1, distinct_categories=1)
    return nav.publish_catalogue(
        store, schemes, report, fetch(), as_of=DAY, feed_date=DAY, scope="FULL",
        integrity_manifest=integrity_manifest,
        validate_completeness=lambda *a, **kw: {"fixture": "composition only"},
        validate_summary=validate_dataset_summary,
    )


def family_links(conn):
    return conn.execute("""
        SELECT v.amfi_scheme_code, f.tlws_mf_id, f.slug, f.base_scheme_name, b.family_id,
               v.base_scheme_name AS variant_name
        FROM mf.fund_variants v JOIN mf.fund_family f USING(group_key)
        JOIN mf_identity.variant_families b USING(amfi_scheme_code)
        ORDER BY v.amfi_scheme_code
    """).fetchall()


def test_native_all_25_rekeys_preserve_ids_slugs_tags_and_replay(nav_store):
    store = nav_store
    stable = scheme(1, "Stable Fund", "INFTEST00001")
    old = [replace(s, nav_date=date(2026, 11, 4))
           for s in [stable, *zerodha(True), *sundaram_idcw(with_reinvestment=False)]]
    start(store, old)
    conn = store.connect()
    before = family_links(conn)
    prior_nav = conn.execute("SELECT * FROM mf.nav_history WHERE nav_date < %s "
                             "ORDER BY amfi_scheme_code, nav_date", (DAY,)).fetchall()
    conn.execute("""
        INSERT INTO mf.fund_family_association_tags (tlws_mf_id,value,tag_type,source)
        VALUES (%s,'existing-link','scheme_alias','trillion-insights')
    """, (before[1]["tlws_mf_id"],))
    tags = conn.execute("SELECT * FROM mf.fund_family_association_tags").fetchall()
    source = normalize_issuers([stable, *zerodha(False), *sundaram_idcw(with_reinvestment=True)])
    assert nav.publish_prices(store, source, fetch(), as_of=DAY)["load"]["inserted"] == 2
    report = publish_catalogue(store, source)
    assert report["state"] == "CATALOGUE_PUBLISHED" and len(report["renames"]) == 25
    after = family_links(conn)
    assert len(after) == 28
    for original, current in zip(before, after):
        assert current["amfi_scheme_code"] == original["amfi_scheme_code"]
        assert current["tlws_mf_id"] == current["family_id"] == original["tlws_mf_id"]
        assert current["slug"] == original["slug"]
        if current["amfi_scheme_code"] not in {1, 154124, 154125}:
            assert current["base_scheme_name"].startswith("Zerodha CASE ")
            assert current["variant_name"] == current["base_scheme_name"]
    assert conn.execute("""
        SELECT amfi_scheme_code, isin_growth_or_div_payout, isin_div_reinvest
        FROM mf.funds WHERE amfi_scheme_code IN (154124, 154125) ORDER BY amfi_scheme_code
    """).fetchall() == [
        {"amfi_scheme_code": 154124, "isin_growth_or_div_payout": "INF903JA1MA8",
         "isin_div_reinvest": "INF903JA1MB6"},
        {"amfi_scheme_code": 154125, "isin_growth_or_div_payout": "INF903JA1MD2",
         "isin_div_reinvest": "INF903JA1ME0"},
    ]
    assert conn.execute("SELECT * FROM mf.fund_family_association_tags").fetchall() == tags
    assert conn.execute("SELECT * FROM mf.nav_history WHERE nav_date < %s "
                        "ORDER BY amfi_scheme_code, nav_date", (DAY,)).fetchall() == prior_nav
    assert integrity_manifest(store)["orphan_families"] == 0
    assert nav.freshness(store)["nav_last"] == nav.freshness(store)["catalogue_snapshot_date"] == DAY
    version = nav.freshness(store)["dataset_version"]
    assert nav.publish_prices(store, source, fetch(), as_of=DAY)["load"]["inserted"] == 0
    assert publish_catalogue(store, source)["state"] == "CATALOGUE_UNCHANGED"
    assert nav.freshness(store)["dataset_version"] == version
    assert conn.execute("SELECT count(*) AS n FROM mf_identity.family_renames").fetchone()["n"] == 25
    assert conn.execute("SELECT count(*) AS n FROM mf.source_snapshot_runs").fetchone()["n"] == 1


def test_native_identity_hold_preserves_catalogue_and_publishes_unrelated_prices(nav_store):
    store = nav_store
    stable = scheme(1, "Stable Fund", "INFTEST00001")
    risky = scheme(2, "Risky Fund", "INFTEST00002")
    start(store, [replace(s, nav_date=date(2026, 11, 4)) for s in (stable, risky)])
    conn = store.connect()
    before = family_links(conn)
    source = [stable, replace(risky, scheme_name="Ambiguous Fund", isin_div_payout_or_growth="INFTEST00099")]
    assert nav.publish_prices(store, source, fetch(), as_of=DAY)["load"]["inserted"] == 1
    assert publish_catalogue(store, source)["state"] == "CATALOGUE_HELD"
    assert family_links(conn) == before
    assert nav.freshness(store)["nav_last"] == DAY
    assert nav.freshness(store)["catalogue_snapshot_date"] is None
    assert conn.execute("SELECT amfi_scheme_code FROM mf.nav_history WHERE nav_date=%s",
                        (DAY,)).fetchall() == [{"amfi_scheme_code": 1}]
    assert publish_catalogue(store, source)["state"] == "CATALOGUE_HELD"
    assert conn.execute("SELECT count(*) AS n FROM mf_identity.catalogue_holds").fetchone()["n"] == 1


def test_native_backfill_selects_only_missing_keys(nav_store):
    store = nav_store
    start(store, [scheme(1, "Stable Fund", "INFTEST00001")])
    older = date(2026, 11, 5)
    prices = [(1, DAY, Decimal("100.55"), "AMFI_HISTORY", False),
              (1, older, Decimal("9.25"), "AMFI_HISTORY", False)]
    with store.transaction() as conn:
        rows = nav.missing_history_prices(conn, prices, from_date=older, to_date=DAY)
        assert rows == prices[1:]
        result = store.upsert_nav(iter(rows), ensure_partitions=False)
        assert result.inserted == 1 and result.updated == 0
        assert nav.missing_history_prices(conn, prices, from_date=older, to_date=DAY) == []
    assert store.connect().execute("SELECT nav FROM mf.nav_history WHERE nav_date=%s",
                                   (DAY,)).fetchone()["nav"] == Decimal("10.5000")


def test_native_rename_recomputes_invalidated_nav_derivations(nav_store):
    store = nav_store
    prior = scheme(1, "Old Regular Fund", "INFTEST00001")
    start(store, [replace(prior, nav_date=date(2026, 11, 4))])
    conn = store.connect()
    old_id = family_links(conn)[0]["family_id"]
    source = [replace(prior, scheme_name="Renamed Regular Fund")]
    assert nav.publish_prices(store, source, fetch(), as_of=DAY)["load"]["inserted"] == 1
    before = conn.execute("SELECT revision FROM mf.nav_source_revisions WHERE amfi_scheme_code=1").fetchone()
    assert publish_catalogue(store, source)["state"] == "CATALOGUE_PUBLISHED"
    revision = conn.execute("""
        SELECT r.revision, c.source_revision, c.as_of FROM mf.nav_source_revisions r
        JOIN mf.nav_calculations c USING(amfi_scheme_code)
        WHERE r.amfi_scheme_code=1
    """).fetchone()
    assert revision["revision"] > before["revision"]
    assert revision["revision"] == revision["source_revision"] and revision["as_of"] == DAY
    assert family_links(conn)[0]["family_id"] == old_id
