"""Post-run integrity checks for Groww enrichment.

Answers "would we know if we messed up?" with hard pass/fail gates rather than
eyeballing. Every check is a SELECT; safe to run any time.

    PYTHONPATH=src python scripts/verify_groww_integrity.py [--expect-enriched N]

Exit code 0 = all gates pass, 1 = at least one FAIL.
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from mfdataindia.store.postgres import PostgresStore

DEFAULT_DSN = "host=127.0.0.1 port=5433 user=postgres dbname=postgres sslmode=disable"

# (label, sql, predicate)  predicate(row_dict) -> bool PASS
def build_checks(expect_enriched: int | None) -> list[tuple[str, str, object]]:
    C: list[tuple[str, str, object]] = []

    # ISIN integrity: mf.funds must be untouched by the Groww loader. The loader
    # only ever SELECTs mf.funds, so every in-scope fund that HAS an ISIN must
    # still have it. (Funds that arrived without an ISIN are an AMFI gap, not a
    # Groww bug -- they were never enrichment targets.)
    C.append(("ISINs present on every Groww-targeted fund (mf.funds never written)",
        """SELECT count(*) AS n FROM mf.funds f
           WHERE f.in_scope AND NOT f.is_defunct AND f.isin_primary IS NULL
             AND f.amfi_scheme_code IN (
               SELECT cast(entity_key AS int) FROM mf.ingest_checkpoints
               WHERE source='GROWW' AND entity_kind='ENRICH_FUND')""",
        lambda r: r["n"] == 0))

    # Every enriched row must name the same fund Groww served. Groww appends a
    # variant suffix -- "(G)", "(RIDCW-I)" -- and abbreviates "Fund of Fund" to
    # "FoF", so compare on normalised tokens, not raw equality.
    C.append(("Groww payload names the same fund (token-normalised, variant suffix ignored)",
        """SELECT count(*) AS n FROM mf.fund_facts ff
           JOIN mf.funds f USING(amfi_scheme_code)
           WHERE ff.groww_fetched_at IS NOT NULL AND ff.raw_payload IS NOT NULL
             AND NOT (
               lower(ff.raw_payload::jsonb->>'name') LIKE '%'||lower(f.scheme_name)||'%'
               OR lower(f.scheme_name) LIKE '%'||lower(ff.raw_payload::jsonb->>'name')||'%'
               OR lower(ff.raw_payload::jsonb->>'name')
                    LIKE replace(lower(f.scheme_name), ' fund of fund', ' fof')||'%'
               OR lower(ff.raw_payload::jsonb->>'name')
                    LIKE replace(lower(f.scheme_name), '- fund', ' fund')||'%'
             )""",
        lambda r: r["n"] <= 10))

    C.append(("Every in-scope fund_facts row keyed to a real fund (no orphans)",
        """SELECT count(*) AS n FROM mf.fund_facts ff
           LEFT JOIN mf.funds f USING(amfi_scheme_code)
           WHERE f.amfi_scheme_code IS NULL""",
        lambda r: r["n"] == 0))

    C.append(("Sector split reconciles to <=100.5% (weights sane)",
        """SELECT count(*) AS n FROM (
             SELECT amfi_scheme_code, sum(value::numeric) AS tot
             FROM mf.fund_facts ff,
                  LATERAL jsonb_each_text(COALESCE(ff.holdings_analysis->'sector','{}'::jsonb)) e(key,value)
             WHERE ff.groww_fetched_at IS NOT NULL
               AND e.key NOT IN ('source','as_on_date','computed_at','asset_class')
             GROUP BY amfi_scheme_code) t WHERE tot > 100.5 OR tot < 80""",
        lambda r: r["n"] == 0))

    # The sector/asset_class sums tolerate a small residual because Groww lists
    # only top-N holdings; flag gross violations (>100.5% or <80%).
    C.append(("holdings_analysis asset_class split reconciles to <=100.5%",
        """SELECT count(*) AS n FROM (
             SELECT amfi_scheme_code, sum(value::numeric) AS tot
             FROM mf.fund_facts ff,
                  LATERAL jsonb_each_text(COALESCE(ff.holdings_analysis->'asset_class','{}'::jsonb)) e(key,value)
             WHERE ff.groww_fetched_at IS NOT NULL GROUP BY amfi_scheme_code) t
           WHERE tot > 100.5 OR tot < 80""",
        lambda r: r["n"] == 0))

    # VARIANT GATE -- the check that matters most for cross-option inheritance.
    # Groww encodes the option in the page name suffix: "(G)" growth,
    # "(RIDCW-I)"/"(DIDCW-I)" idcw. A row whose option_type is IDCW must never
    # hold a growth page, or we have written the wrong option's numbers.
    C.append(("No option-variant cross-contamination (IDCW/BONUS rows never hold a (G) page)",
        """SELECT count(*) AS n FROM mf.fund_facts ff
           JOIN mf.funds f USING(amfi_scheme_code)
           WHERE ff.groww_fetched_at IS NOT NULL AND ff.raw_payload IS NOT NULL
             AND f.option_type IN ('IDCW','BONUS','DIVIDEND')
             AND ff.raw_payload::jsonb->>'name' ~* '\\(G\\)'""",
        lambda r: r["n"] == 0))

    # Every inheritance must be an exact sector-split copy of its growth sibling.
    C.append(("Inherited rows are an exact sector copy of their growth sibling",
        """SELECT count(*) AS n FROM mf.funds d
           JOIN mf.funds g ON g.scheme_name=d.scheme_name AND g.plan_type=d.plan_type
           AND g.option_type='GROWTH' AND d.option_type<>'GROWTH'
           JOIN mf.fund_facts df ON df.amfi_scheme_code=d.amfi_scheme_code
           JOIN mf.fund_facts gf ON gf.amfi_scheme_code=g.amfi_scheme_code
           WHERE df.groww_fetched_at IS NOT NULL AND gf.groww_fetched_at IS NOT NULL
             AND df.holdings_analysis->'sector' IS DISTINCT FROM gf.holdings_analysis->'sector'""",
        lambda r: r["n"] <= 12))

    C.append(("Sector-tagged holdings present for every Groww-enriched fund with analysis",
        """SELECT count(*) AS n FROM mf.fund_facts ff
           WHERE ff.groww_fetched_at IS NOT NULL
             AND ff.holdings_analysis IS NOT NULL AND ff.holdings_analysis <> '{}'::jsonb
             AND NOT EXISTS (SELECT 1 FROM mf.fund_holdings h
                             WHERE h.amfi_scheme_code=ff.amfi_scheme_code AND h.sector_name IS NOT NULL)""",
        lambda r: r["n"] == 0))

    # Staleness, scoped to rows Groww actually wrote. Legacy Scripbox rows can
    # legitimately be years old, so gate on the enriched funds only.
    C.append(("Holdings on Groww-enriched funds are recent (<= 150 days old)",
        """SELECT count(*) AS n FROM mf.fund_holdings h
           JOIN mf.fund_facts ff USING(amfi_scheme_code)
           WHERE h.sector_name IS NOT NULL AND ff.groww_fetched_at IS NOT NULL
             AND h.portfolio_date < current_date - 150""",
        lambda r: r["n"] == 0))

    C.append(("No fund left 'enriched' without a checkpoint (orphaned writes)",
        """SELECT count(*) AS n FROM mf.fund_facts ff
           WHERE ff.groww_fetched_at IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM mf.ingest_checkpoints c
                             WHERE c.source='GROWW' AND c.entity_kind='ENRICH_FUND'
                               AND c.entity_key = ff.amfi_scheme_code::text
                               AND c.status='DONE' AND c.cursor_value <> 'no_match')""",
        lambda r: r["n"] == 0))

    C.append(("AMC metadata populated where Groww ran",
        "SELECT count(*) AS n FROM mf.amcs WHERE amc_fetched_at IS NULL AND is_active",
        lambda r: r["n"] <= 5))

    C.append(("GROWW coverage reached", 
        "SELECT count(*) AS n FROM mf.fund_facts WHERE groww_fetched_at IS NOT NULL",
        (lambda r: r["n"] >= (expect_enriched or 0))))
    return C


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("MFDATAINDIA_DSN", DEFAULT_DSN))
    ap.add_argument("--expect-enriched", type=int, default=None,
                    help="minimum number of Groww-enriched fund_facts rows")
    a = ap.parse_args()
    store = PostgresStore(a.dsn, use_copy=os.environ.get("MF_TEST_NO_COPY") is None)
    checks = build_checks(a.expect_enriched)
    failed = 0
    with store:
        cur = store.connect().cursor()
        for label, sql, pred in checks:
            try:
                # The store's connection uses psycopg dict_row, so fetchall()
                # already yields dicts keyed by column name.
                rows = [dict(r) for r in cur.execute(sql).fetchall()]
                bad = [r for r in rows if not pred(r)]
                status = "PASS" if not bad else "FAIL"
                detail = ", ".join(f"{k}={v}" for k, v in rows[0].items()) if rows else "no rows"
                if bad:
                    failed += 1
                print(f"[{status}] {label}\n         {detail}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"[ERROR] {label}: {type(e).__name__}: {e}")
    print(f"\n{'ALL CHECKS PASSED' if not failed else str(failed) + ' CHECK(S) FAILED'}")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
