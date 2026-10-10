# MFData API — data-quality & performance-methodology signals (contract v2)

This page documents the evidence-backed lifecycle, NAV-freshness, NAV-quality
and performance-methodology signals served by the private MFData API
(contract `mfdataindia-json-read-v2`, `contracts/mfdataindia-openapi-v2.json`).
Everything here is GET-only and served to authenticated backend consumers
(TrillionInsights BFF); the API stays private — no browser, no anonymous
portal, no public ingress.

## 1. Fund detail: `lifecycle`

```json
{
  "state": "active | redeemed | defunct | unknown",
  "evidence": "amfi_current_feed | amfi_redeemed_marker | amfi_defunct_marker | not_seen_in_latest_feed | insufficient_evidence",
  "last_seen_in_source": "2026-10-04"
}
```

Rules (precedence top-down):

1. `redeemed` — requires the authoritative AMFI REDEEMED marker: an **open**
   `LIFECYCLE_ENDED` quality flag with `source = AMFI` (the evidence the
   ingest writes when AMFI marks an ISIN column `REDEEMED`).
2. `defunct` — the existing defunct evidence (`mf.funds.is_defunct`:
   NAV 0/N.A. or Defunct naming).
3. `active` — presence in the **latest successful source snapshot**:
   `last_seen_in_source` equals the maximum recorded snapshot date.
4. `unknown` — anything else, with the evidence that got us there
   (`not_seen_in_latest_feed` when a last-seen date exists but predates the
   latest snapshot; `insufficient_evidence` when none is recorded).

Deliberate non-rules: stale NAV, a terminal ₹10.00 NAV reset, or a
close-ended scheme type **alone never produce `redeemed`/`defunct`**. There is
no top-level `matured` boolean anywhere in the contract; absence from a feed
preserves the last-seen evidence and classifies conservatively.

`last_seen_in_source` advances for **every** scheme present in a successful
AMFI refresh, even when all other metadata is unchanged (presence is
lifecycle evidence). Schemes absent from a feed keep their previous date.

## 2. Fund detail: `nav_freshness`

```json
{
  "dataset_as_of": "2026-10-07",
  "latest_nav_date": "2026-10-07",
  "lag_days": 0,
  "status": "current | delayed | stale | very_stale | missing"
}
```

Computed from `mf.dataset_summary.nav_last` (the dataset-wide latest NAV) and
the code's own latest NAV. Server-owned thresholds, in **calendar days** of
lag — consumers must not redefine them:

| status       | lag days |
|--------------|----------|
| `current`    | 0–7      |
| `delayed`    | 8–30     |
| `stale`      | 31–180   |
| `very_stale` | > 180    |
| `missing`    | no NAV at all (`lag_days` null) |

## 3. Fund detail: `nav_quality`

```json
{
  "assessment_status": "current | stale | not_assessed",
  "methodology_version": "1",
  "assessed_dataset_version": 2,
  "assessed_at": "2026-10-10T05:00:00+00:00",
  "observation_count": 1525,
  "distinct_nav_count": 1,
  "signals": ["constant_nav_series"]
}
```

Read-through of `mf.nav_quality_assessments` (written only by the governed
audit, §5). Closed signal set — **detection signals, not proof that a stored
NAV is wrong**:

* `constant_nav_series` — one distinct NAV across the full history (≥ 250
  observations). Never called "frozen", "invalid" or "corrupt".
* `duplicate_variant_series` — exact series duplicates within one guarded
  family identity (AMC + scheme type + scheme category + `group_key`),
  fingerprint-narrowed and value-for-value verified.
* `terminal_face_value_reset_candidate` — terminal observation exactly
  10.0000 after a materially higher prior observation (FMP redemption-at-face
  pattern).

`assessment_status` describes the **assessment's currency**, never the
fund's cleanliness: when the assessed dataset version is older than the
served dataset version the status is `stale` — the signal is what it last was,
not "clean". `not_assessed` = no assessment row (audit not yet run, or the
scheme has no NAV).

## 4. Returns & analytics: `methodology`

```json
{
  "basis": "nav_change",
  "distribution_adjustment": "not_applicable | unavailable",
  "comparison_eligible": true,
  "limitations": ["distribution_history_unavailable", "lifecycle_not_active", "nav_freshness_lag", "nav_quality_signal", "nav_series_unavailable"]
}
```

All numeric return/analytics fields are NAV-to-NAV changes
(`basis: nav_change`).

* **Growth** — `distribution_adjustment: not_applicable`;
  `comparison_eligible` is true only when the lifecycle, freshness and quality
  gates pass (state `active`, freshness `current`, no quality signals).
  Failed gates appear in `limitations` (closed reason-code list).
* **IDCW / DIVIDEND / BONUS / UNKNOWN** —
  `distribution_adjustment: unavailable`,
  `comparison_eligible: false`, and `distribution_history_unavailable` in
  `limitations`. Their raw NAV-based numbers are still served on a direct
  request, but **nothing claims an unadjusted payout, bonus, or unclassified
  NAV change is total return** and no Growth-twin value is ever copied into
  another option's response. Only a confirmed `GROWTH` option can be eligible.

### Ranking eligibility (documented v1 change)

Option-level comparison-ineligible series (every option except confirmed
`GROWTH`) are excluded from the populations of:

* `GET /api/movers` and `GET /api/movers/categories`;
* the peer population of `GET /api/funds/{code}/peers` (an ineligible
  subject still reports `peer_count` and its own raw `fund_return`, with
  `beats_pct`/`rank` null);
* the population of `GET /api/funds/{code}/risk-reward`;
* any other API-owned representative performance ranking.

`GET /api/compare` retains every explicitly requested identity but attaches
`lifecycle`, `nav_freshness`, `nav_quality`, and `methodology` to each item.
Consumers must omit normalized performance for items whose supplied
`methodology.comparison_eligible` is false; identity and non-performance facts
may remain visible.

All other v1 response fields remain compatible; only these population
semantics changed.

## 5. The NAV-integrity audit

`scripts/audit_nav_integrity.py` (logic in `mfdataindia.audit.nav_integrity`)
assesses the **complete** database and is the sole writer of
`mf.nav_quality_assessments` (migration 016):

```
PYTHONPATH=src python scripts/audit_nav_integrity.py            # assess + persist
PYTHONPATH=src python scripts/audit_nav_integrity.py --dry-run  # report only
```

Properties: deterministic and idempotent (same database → same signals and
rows on every run); read-only over `mf.nav_history` (a dev-snapshot run
verified zero NAV changes by full-content hash); finite sorted JSON summary
(methodology version, dataset version, counts, affected codes); its only
write target is the assessment table. The six-hour NAV refresh keeps only
cheap incoming-feed guards; the full-history audit is **not** scheduled until
its runtime is benchmarked and scheduling is separately approved (observed
runtime on the 14,368-scheme / 4.2M-NAV dev snapshot: ~6.5 s).

## 6. Legacy transaction facts — not a buyability signal

`facts.status` / `facts.transaction_status` (e.g. `ACT`/`ALL`) remain inside
`facts` for backward compatibility. They are **legacy, non-authoritative
facts**: not duplicated at the fund-detail top level, not translated into
open/closed/purchasable, no `transaction_available` boolean, and no
lifecycle/quality logic depends on them. A normalized transaction-availability
API requires a separately approved, current authoritative source.

## 7. Siblings

`GET /api/funds/{code}` → `siblings[]` now includes `periodicity`
(nullable), so variant navigation can distinguish e.g. DAILY vs WEEKLY IDCW
twins.

## 8. Contract

* v1 (frozen baseline): `contracts/mfdataindia-openapi-v1.json`,
  `mfdataindia-json-read-v1`,
  sha256 `ed00b0983be1855c4ee23316851f255cc7fdf8d5ec8b997268998412edc48916`.
* v2 (this sprint): `contracts/mfdataindia-openapi-v2.json`,
  `mfdataindia-json-read-v2` — regenerated by
  `scripts/export_openapi.py` (deterministic; `--check` guards it in CI).
  The eight changed endpoints export explicit response models
  (`FundDetail`, `ReturnsResponse`, `AnalyticsResponse`, `PeersResponse`,
  `RiskRewardResponse`, `MoversResponse`, `CategoryMoversResponse`,
  `CompareResponse`);
  responses are validated against them at request time.
* The API remains GET-only with **no authentication surface in the contract**
  (access control is network-level, owned by the deployment).

## 9. Rollout order (documented — not executed by this sprint)

1. Merge the MFData implementation **without deploying it**.
2. Pin the exact merged MFData SHA and the v2 OpenAPI hash in
   TrillionInsights (`contracts/mfdata/manifest.json`).
3. Merge and deploy the backward-compatible Insights backend (its wire models
   accept both the old v1 payload and the complete v2 payload, and keep
   `extra="forbid"` so unrecognized provider fields still fail closed).
4. Deploy MFData v2 (migration 016 is additive; the audit then runs as a
   bounded operator job).
5. Run bounded private contract certification only with separate approval.
6. Verify the already separate Insights UI consumer against certified v2
   golden payloads before enabling live consumption.
