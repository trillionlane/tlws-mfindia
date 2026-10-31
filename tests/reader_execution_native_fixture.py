"""Genuine migrated/populated graph for normal hosted disposable PostgreSQL.

No registry overrides, source downloads, replayed numbered migrations, shared
database or live acceptance. Multi/book responses are intentionally empty;
monthly/refresh and generator publication use existing synthetic source inputs.
"""
import hashlib
import json
from pathlib import Path

from mfdataindia.store.postgres import PostgresStore
from mfdataindia.review import broad_reader_embedded_authority as embedded
from mfdataindia.review import store as review
from mfdataindia.review.policy import fingerprint
from mfdataindia.enrichment import (
    current_facts, current_facts_store as current, monthly_facts, monthly_pdf, monthly_schemas,
    refresh_store, monthly_projection, multi_store, multi_facts, multi_projection,
    book_store, book_sources,
)
from test_ro01_reader_policy_postgres import connect
from test_official_enrichment_evidence import intake
from test_official_facts import LINEAGE
from test_monthly_facts import SUMMARY, DETAIL, CHANGES

ROOT = Path(__file__).resolve().parents[1]


def _policy(conn, document):
    review._insert_exact(conn, "policies", "policy_hash", fingerprint(document), document,
                         {"policy_id": document["id"], "policy_version": document["version"]})


def populate(dsn, monkeypatch):
    base = json.loads(embedded.document("base"))
    optional = tuple(Path(row["path"]).name for row in base["files"]
                     if Path(row["path"]).parent == Path("sql/optional")
                     and not Path(row["path"]).name.startswith("023_"))
    assert len(optional) == 21
    with PostgresStore(dsn, use_copy=False) as store:
        assert store.apply_migrations(ROOT / "sql/optional", files=optional) == list(optional)
    with connect(dsn, autocommit=False) as conn:
        # Numbered DDL is not the installer metadata population. Use the exact
        # committed policy constructors that the ordinary installers append;
        # never invented JSON, registry overrides or forged migration rows.
        from mfdataindia.review import policy as review_policy, publication
        from mfdataindia.analytics import calendar, rolling, overlap
        from mfdataindia.enrichment import evidence, facts, portfolios, distributions, idcw_catalog
        from mfdataindia.enrichment import monthly_current, refresh_policy, fact_review_policy
        for builder in (review_policy.policy, publication.policy, calendar.policy, rolling.policy,
                        evidence.policy, facts.policy, portfolios.policy, overlap.policy,
                        distributions.policy, idcw_catalog.policy, current_facts.policy,
                        monthly_facts.policy, monthly_current.policy, refresh_policy.policy,
                        fact_review_policy.policy):
            _policy(conn, builder())
        current._initialize_configuration(conn,
            baseline=current.view_definition(conn, "mf.current_fund_facts"),
            policy_hash=fingerprint(current_facts.policy()))
        current.verify_acl(conn)

    # The ordinary existing portfolio fixture uses ALREADY_INSTALLED correctly;
    # unlike the old combined fixture it does not modify multi/book registries.
    from test_complete_portfolios import prepare as portfolio_inputs
    portfolio_selection = portfolio_inputs(dsn, monkeypatch)
    with connect(dsn, autocommit=False) as conn:
        for code, option, isin in ((103174, "GROWTH", "INF209K01BR9"),
                                   (103173, "IDCW", "INF209K01BO6")):
            conn.execute("INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout) VALUES(%s,1,%s,%s,'OPEN_ENDED','Equity','REGULAR',%s,%s)",
                (code, monthly_facts.NAME, monthly_facts.NAME.upper(), option, isin))
            conn.execute("INSERT INTO mf.fund_variants(amfi_scheme_code,group_key,base_scheme_name) VALUES(%s,%s,'Large cap')", (code, str(code)))
            conn.execute("INSERT INTO mf.fund_facts(amfi_scheme_code,source,aum,base_expense_ratio,risk_level) VALUES(%s,'MANUAL',123.45,2.22,'HIGH')", (code,))
        conn.execute("INSERT INTO mf.amcs(amfi_amc_name,normalised_name) VALUES('HDFC Mutual Fund','HDFC MUTUAL FUND')")
        hdfc = conn.execute("SELECT amc_id FROM mf.amcs WHERE amfi_amc_name='HDFC Mutual Fund'").fetchone()["amc_id"]
        from mfdataindia.enrichment import idcw_catalog
        for code, (name, selected_plan, payout, reinvest) in idcw_catalog.COHORT.items():
            conn.execute("INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout,isin_div_reinvest) VALUES(%s,%s,%s,%s,'OPEN_ENDED','Equity',%s,'IDCW',%s,%s)",
                (code, hdfc, name, name.upper(), selected_plan, payout, reinvest))
        conn.execute("INSERT INTO mf.funds(amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,plan_type,option_type,isin_growth_or_div_payout) VALUES(100001,1,'Synthetic empty book','SYNTHETIC EMPTY BOOK','OPEN_ENDED','Equity','REGULAR','GROWTH','INF209K01BR9')")

    registry = SUMMARY.replace("-Growth 1\n", "-Growth 103174\n").replace("-Regular - IDCW 2\n", "-Regular - IDCW 103173\n")
    raw, identity_raw = b"%PDF-synthetic-ro02-monthly", b"%PDF-synthetic-ro02-identity"
    from mfdataindia.enrichment import store as enrichment
    with connect(dsn, "mfdata_verified_generator", autocommit=False) as conn:
        identity = enrichment.append(conn, intake(identity_raw, media_type="application/pdf",
            amc=monthly_facts.facts.AMC,
            source_url="https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_monthly.pdf",
            final_url="https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_monthly.pdf",
            official_hosts=["nsearchives.nseindia.com"], codes=[103173, 103174]), identity_raw)["document"]["document_id"]
    url = "https://mutualfund.adityabirlacapital.com/-/media/bsl/files/resources/factsheets/2026/monthly.pdf"
    selection = monthly_schemas.Selection(intake=intake(raw, media_type="application/pdf",
        amc=monthly_facts.facts.AMC, source_url=url, final_url=url,
        official_hosts=["mutualfund.adityabirlacapital.com"], codes=[103173, 103174],
        document_as_of="2026-08-31"), identity_document_id=identity,
        detail_page=15, changes_page=16, identity_page=28)
    monkeypatch.setattr(monthly_facts.facts, "decode", lambda *a: (registry, {**LINEAGE, "page": 28}))
    monkeypatch.setattr(monthly_pdf, "extract", lambda *a, **kw: {
        "source_hash": selection.intake.expected_sha256, "policy_hash": "4" * 64,
        "extractor_version": "synthetic", "engine_hash": "5" * 64,
        "pages": [{"page": 15, "text": DETAIL, "text_hash": "6" * 64},
                  {"page": 16, "text": CHANGES, "text_hash": "7" * 64}]})
    from test_official_fact_refresh import append_period, receipt, references
    snapshots = append_period(dsn, monkeypatch, selection)
    with connect(dsn, autocommit=False) as conn:
        refresh_store.append_run(conn, receipt(snapshots, "ro02-synthetic-september"))
    with connect(dsn, autocommit=False) as conn:
        monthly_projection.activate(conn, ROOT, legacy_snapshot_ids=[], reviewed=references(snapshots))
    with connect(dsn, autocommit=False) as conn:
        multi_store._initialize_configuration(conn)
        multi_store.verify_acl(conn, principal="mfdata_verified_reader")
        # Seed an installed graph using the exact committed projection and real
        # configuration constructor, not activate()/fake four-field evidence.
        # There is deliberately NO multi activation transition/publication claim.
        raw_projection = (ROOT / "sql/optional" / multi_projection.PROJECTION).read_bytes()
        assert hashlib.sha256(raw_projection).hexdigest() == multi_projection.PROJECTION_SHA256
        prior = current.configuration(conn)
        conn.execute(raw_projection.decode())
        definitions = {name: current.view_definition(conn, "mf_live_facts." + name)
                       for name in monthly_projection.ORDER}
        monthly_projection.seal_configuration(conn, prior["generation"] + 1,
            prior["baseline_definition"], fingerprint(multi_facts.current_policy()), definitions)
        assert current.policy_document(conn) == multi_facts.current_policy()
        assert current.projection_state(conn)
        current.verify_acl(conn)
        assert not conn.execute("SELECT 1 FROM mf_multi_facts.transitions").fetchone()
        book_store._initialize_configuration(conn, version=5)
        book_store.verify_acl(conn, principal="mfdata_verified_reader")
        assert book_store.installed_configuration(conn)["document"] == book_sources.configuration(5)
        assert not conn.execute("SELECT 1 FROM mf_multi_facts.snapshots").fetchone()
        assert not conn.execute("SELECT 1 FROM mf_book_facts.batches").fetchone()
        assert conn.execute("SELECT count(*) AS n FROM mf_monthly_facts.snapshots").fetchone()["n"] == 2
        assert conn.execute("SELECT count(*) AS n FROM mf_fact_refresh.publications").fetchone()["n"] > 0
        assert conn.execute("SELECT count(*) AS n FROM mf.schema_migrations").fetchone()["n"] == 44
    return {"portfolio_selection": portfolio_selection}
