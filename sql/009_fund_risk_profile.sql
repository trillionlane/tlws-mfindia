-- 009_fund_risk_profile.sql
-- Derived, refreshable per-fund risk profile: the (volatility, return)
-- coordinates for the category risk-reward scatter.
--
-- This is NOT a source column — it is computed from mf.nav_history (the AMFI
-- daily NAV) by scripts/refresh_risk_profile.py, which runs the window
-- computation once per fund and upserts the result here. A single full pass
-- over all funds takes ~15s, so the table is refreshed on demand (e.g. after a
-- large NAV backfill) rather than per request.
--
-- annualized_return / vol use daily log returns, 252 trading days — the same
-- convention as queries.fund_analytics, so the scatter and the fund-page risk
-- card agree.

CREATE TABLE IF NOT EXISTS mf.fund_risk_profile (
    amfi_scheme_code     INTEGER PRIMARY KEY REFERENCES mf.funds (amfi_scheme_code),
    points               INTEGER NOT NULL,
    first_nav_date       DATE NOT NULL,
    last_nav_date        DATE NOT NULL,
    annualized_return    NUMERIC(10, 4) NOT NULL,
    annual_vol           NUMERIC(10, 4) NOT NULL,
    cagr                 NUMERIC(10, 4) NOT NULL,
    max_drawdown         NUMERIC(10, 4) NOT NULL,
    refreshed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE mf.fund_risk_profile IS
    'Derived per-fund risk/return coordinates for the risk-reward map; '
    'populated by scripts/refresh_risk_profile.py, not by any ingest source.';
