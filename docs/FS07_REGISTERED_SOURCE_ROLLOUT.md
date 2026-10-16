# FS-07: bounded registered multi-AMC refresh

## Finite scope

Approved backend/data-only expansion: retain the complete accepted ABSL pilot
and prove a second real, independent source configuration. This is not whole-AMC
onboarding or whole-factsheet extraction. SBI's public catalogue returned a
site rejection during research; no access-control workaround was attempted.
SBI's broader onboarding remains FS-09. HDFC is the second bounded proof.

`config/official-facts-sources-v1.json` is acquisition authority, unlike the
read-only AMFI directory census. It permits exactly two adapters:

- Existing ABSL Large Cap Regular Growth 103174 and Annual IDCW 103173, with
  its unchanged public Sitecore discovery, monthly parser and publication gate.
- HDFC Flexi Cap Regular IDCW 101763, one reviewed September 2026 book and
  the issuer's exact AMFI/payout/reinvestment ISIN bridge. The URLs, complete
  original hashes, physical pages, date and independently inspected values are
  checked in. A changed edition or layout requires reviewed registry/code work;
  this adapter does **not** discover or certify the latest month automatically.

The HDFC book SHA-256 is
`ab3d561bbacbc40aeb4ac5630f6e85b64e22a3433094b0312e279636907dcab4`,
7,059,815 bytes, 140 pages. Inspect physical pages 7–8, not search-result text.
The issuer identity PDF is
`884ef54c8e0ffb4097652cccbbf72f863e8f45ee319064cf85e9b0d41d1eb3e3`,
121,485 bytes, page 1. Originals and visual research receipts are retained in
the local FS-07 source ledger, never committed or served by the API.

Accepted HDFC fields: benchmark `NIFTY 500 Index (TRI)`, Regular **BER** 1.27%,
fund AUM INR 108,546.98 crore as of 2026-09-30, and Amit Ganatra, Bhagyesh
Kagalkar and dedicated overseas manager Gopal Agrawal. The latter is in the
continuation-page footnote; roles are retained separately. Average AUM and
Direct BER are explicitly not selected. Riskometer pages are unparsed; risk
remains unavailable in this adapter, not inferred from a gauge legend or an
undated SSD. No source-reported performance is repurposed as IDCW analytics.

## Isolation and publication

One existing source job, two fixed tasks/parallelism two, zero retries, existing
generator identity, 600 seconds per task. Task index selects the checked-in
adapter, never a caller URL, AMC or cohort. Each task commits its own receipts.
Cloud Run tries all tasks and marks the job failed if any task fails; one task
does not consume the other's timeout. Existing daily 09:00 Asia/Kolkata source
schedule and six-hour NAV/audit schedules are retained.

HDFC originals use a separate create-only/read prefix:
`gs://tlws_mf_data_source/official-monthly-facts/hdfc-flexi-cap/`.
Verify full original bytes and generation before parsing/publication. Do not
grant overwrite/delete, bucket-wide read, public access or a new invoker.

Optional owner installation 037 creates only `mf_multi_facts`. Configuration
generations, originals, snapshots, runs and transition intents are append-only.
Revoke administrator default grants from all unintended runtime roles, including
ingestion. Only the dedicated generator can append originals/snapshots/runs;
readers/admin cannot fetch raw bytes or write this ledger. No runtime receives
DDL, transition or configuration mutation rights or executable owner functions.

The database seals complete exact registered inputs, original hashes, identity,
reviewed values, archive bindings and a successful run receipt. A provisional
snapshot committed without a receipt is **not published**. Publication checks
the live exact identity again. Replay preserves the existing source/snapshot;
a new unchanged check creates only its own immutable run receipt.

Current policy v3 retains the exact frozen pilot assertion graph and adds the
disjoint registered HDFC cohort. Missing risk cannot erase the baseline value.
All five prior scalar aliases and numeric column types remain unchanged.
No original fund, NAV, imported facts, calculation, identity, association or
human-review record is rewritten. Human verification stays needs_verification;
publication alone never creates a human event or enables adjusted IDCW returns.

## Private consumer addition

Explicit `create_multi_amc_app` profile: 49 paths, preserving all 46 old path
and component contracts. Existing internal ingress, IAM and signed assertions.
All responses no-store; no HTTP source fetch or publication writer.

- `GET /api/enrichment/v8/policy`: source acquisition/publication policy.
- `GET /api/enrichment/v8/refresh/status`: independent per-source status;
  the HDFC checked-edition limitation is explicit.
- `GET /api/enrichment/v8/funds/{code}/facts`: bounded registered snapshots,
  publication status, exact-current-identity match and page/hash lineage.

Continue consuming current scalar facts from the existing private core routes;
v3 provenance/verification metadata applies to the new cohort too. v4/v6 source
history remain the frozen pilot namespace; HDFC registered source history is
v8, not an invented v4 monthly snapshot. v7's monthly-original counters still
describe that explicitly scoped old namespace. No all-AMC completeness claim.

## Controlled DEV gates

1. Exact-head focused/full tests, contract checks, audit, secret scan, explicit
   labelled self-review and green CI. Push/PR/merge and exact-merge image builds.
   Generic auto-deploy remains disabled; no production/UI changes.
2. Capture actual serving SHA, image, IAM, network, source job/scheduler and all
   original/prior-layer seals. Fresh full backup, restore into a new guarded
   isolated clone, verify preservation and test real HDFC publication there.
   Synthetic fixtures alone cannot establish issuer or DEV acceptance.
3. Install only the restricted additive schema under the existing migrator;
   add only the exact private archive-prefix permissions. Deploy the pinned
   two-task source runtime. Independently verify actual execution SHA/digest,
   task count, identities, archive generations/bytes and both database receipts.
4. Stage the private 49-path candidate, certify new metadata and all retained
   contracts before traffic. Promote the compatible new code **before** the
   short owner v3 view/configuration transition. Certify HDFC against independent
   page values and real core detail/batch, and ABSL unchanged.
5. Actually restore the captured prior graph/configuration **before** old API
   traffic, without consulting failed candidate views first. Replay recovery,
   check core previous values, then reactivate and recertify. All appended inputs
   remain retained. On failure restore source job configuration independently;
   if necessary pause only the owned official-facts source schedule.
6. Observe a genuine scheduled two-task execution, then restore daily timing.
   Confirm unchanged NAV/audit schedules, private denials, all 19 core routes,
   human events zero and full original/prior-layer preservation. Record exact
   immutable execution evidence and a sealed acceptance artifact on the PR.

Do not call deployment complete until these live gates pass. A newer ordinary
NAV ingest can legitimately advance the database while this work runs; never
hide that as preservation success. Reconcile and capture a new stable baseline
instead of pausing ingestion or altering original records.
