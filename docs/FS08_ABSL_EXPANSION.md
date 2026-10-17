# FS-08 — ABSL scheme/layout expansion

Approved with FS-09 SBI, FS-10 Kotak and FS-11 UTI on 2026-10-17. Deliver each
sprint in a separate reviewed PR, merge, controlled DEV rollout and certification.
This document records implementation scope, not runtime acceptance.

## Finite acceptance

- Reconcile every current in-scope ABSL scheme, including retained or ambiguous
  variants, against two dated original official books. The dataset-24 restored
  observation is 192 in-scope variants out of 414 retained ABSL records; live
  denominators must be checked again, not hardcoded to improve coverage.
- Retain the accepted two-variant pilot and bounded HDFC source unchanged.
  Do not reinterpret their policies, snapshots, identities or archived originals.
- Broader parsed facts require an exact issuer-authored AMFI-code/ISIN bridge,
  current database identity agreement and an exact corresponding monthly heading.
  No fuzzy names, Direct/Regular/Retail borrowing, inferred risk or guessed units.
- Distinguish each fund/period/field's usable evidence from identity, source,
  layout and date gaps. Text-page coverage is not semantic factsheet completeness.
  Missing evidence never erases the last usable value or becomes a zero fact.
- Add immutable original/extraction/snapshot/run evidence and versioned current
  overlays, with restricted producers and explicit owner-only configuration.
  Preserve raw funds/NAVs and histories/calculations; no human approvals invented.
- Certify private core and extension reads, replay, real recurring source work,
  source identities/permissions, actual configuration/traffic recovery and full
  original/prior-layer preservation before claiming DEV acceptance.

## Following authorized sprints

FS-09 brings SBI through the same source/identity/layout and DEV gates; FS-10
does Kotak; FS-11 does UTI. Their approved scope is integrated source onboarding,
not an unsupported promise of complete extraction. Public-source failures remain
explicit gaps; no access-control bypass or substitute untrusted source is allowed.

No UI, production, original-record correction/deletion, full-economic-overlap or
adjusted-IDCW eligibility change is authorized by these deliveries.

## Implementation and trust boundary

Optional migration `038_amc_book_facts.sql` creates only `mf_book_facts`.
The direct migration owner installs its reviewed, hash-pinned configuration;
the dedicated generator appends originals, complete period batches and runs.
Default grants to ingestion are explicitly revoked on every new relation and
function. Readers/admins cannot read raw bytes or insert source evidence. Only
the owner can install configuration or activate/recover the current graph.
The denominator includes every current in-scope record for the exact AMC,
including unlabelled or unsupported plans/options as explicit gaps. The registry's
Regular-plan constraint controls usable facts, not silent population exclusion.

The pinned generator image is the trusted PDF interpretation boundary; SQL
does not pretend to parse a PDF or prove its factual meaning. Database checks
independently enforce identity population completeness, source hashes/pins,
field types/units, page-evidence presence, two distinct month-end periods,
archive-generation bindings, live dataset receipt and immutable replay.
Unreceipted batches are invisible to both current facts and the evidence API.

The issuer-authored April 2026 consolidated scheme summary supplies exact
code/ISIN pairs only. It is hosted on the exchange archive but is not licensed
exchange market/index data. It never establishes current fact dates. New or
renamed schemes require an exact bridge plus a current heading or the printed
same-page erstwhile name; an old or missing bridge is reported, never guessed.
Both original complete monthly books and the identity PDF are create-only,
generation-pinned private objects. Landing/catalogue originals are retained
byte-for-byte in the append-only database. Full-book text inspection does not
imply full semantic extraction; this delivery supports five current fields.

`create_amc_books_app` has 52 paths. All previous 49 path/component contracts
remain unchanged. New signed, private, no-store GETs:

- `/api/enrichment/v9/policy`: registry, parser/policy/code hashes and scope.
- `/api/enrichment/v9/coverage`: live denominator, exact population hash, latest
  attempt, last success and two period-specific usable/gap/field counts.
- `/api/enrichment/v9/funds/{code}/facts`: at most 20 immutable published fund
  observations, dates, page/identity evidence and current-identity agreement.

There is no HTTP fetch, source-write or raw-PDF endpoint. Existing v7 monthly
namespace counters retain their original meaning; v9 is the broader book
inventory, not a silent redefinition of those historical counts.

Current policy v4 retains the exact accepted v3 graph and its existing pilot
and HDFC values. Wider exact-identity assertions are grouped per fund/field/date:
equal assertions corroborate; conflicting or unavailable later evidence retains
the previous usable dated value, otherwise the legacy baseline. Metadata states
the unresolved conflict/gap. Existing core scalar aliases consume this same
projection. A book `snapshot_id` is its content-addressed `batch_id`, with
`source_namespace: amc-book` and fund code; it is not a monthly-v4 snapshot ID.
Human review remains dependency-bound through the existing v6 decision API;
automated publication creates no human event or verified flag.

One existing source job retains task 0 (ABSL pilot) and task 1 (HDFC) and adds
task 2 (ABSL expansion), serial parallelism and zero retries. Each adapter has
independent transactions/receipts. The retained adapters' policies and registry
are not edited. New authority is `official-book-sources-v1.json`, not ad hoc
URLs, cohort arguments or per-AMC environment variables.

## Controlled DEV drill

1. Merge only after exact-head CI and explicitly labelled self-review. Confirm
   generic auto-deployment remains off; build provenance-labelled digest images.
2. Fresh complete private backup and a new isolated PG18 restore under the
   original administrator owner. Reconcile every original/prior-layer record;
   preserve backup bytes and every failed rehearsal. Never use port 5432.
3. Rehearse opt-in installation, both real-book batches, projection activation,
   same-date conflict/unavailable fallback, full original preservation and exact
   prior-graph recovery. No live human review is invented.
4. Capture live owner intent/configuration. Install the optional layer, grant
   only create/read on the single new archive prefix, and deploy the source job
   with the exact image/SHA and pinned generator secret/identity/network.
5. Execute all three real tasks. Independently verify completion, immutable
   execution image/identity, exact per-source receipts and all archived bytes.
6. Stage the private 52-path candidate at zero ordinary traffic. Certify real
   dated original-field oracles, existing core aliases, retained pilot/HDFC,
   no assertion/raw/mutation access and unchanged contracts. Then activate the
   overlay and explicitly promote only the verified revision.
7. Restore the known prior database graph independently of candidate lookups,
   restore prior traffic/job configuration, and certify recovery; keep all new
   evidence. Reactivate from a fresh configuration seal. Old intents must fail.
8. Observe an actual Cloud Scheduler invocation under its dedicated identity,
   certify all 19 core read endpoints plus new extension reads, and take a final
   full backup/preservation comparison. Restore the normal daily source schedule.
   Never pause ordinary NAV/audit schedules to manufacture preservation evidence.

Ordinary NAV ingestion is separate authorized work, not permission for this
sprint to edit source rows. If it runs during evidence capture, retain its
immutable source receipt, prove every original fund/NAV row remains unchanged
and attribute additions precisely; then obtain a fresh settled strict rehearsal.
Do not claim strict before/after equality across an unrelated ingestion window.

Record exact SHA, image digests, PR/CI/jobs/revision, source dates and observed
denominators, backup/recovery seals and every unresolved exception on the PR.
Code, merge, runtime publication and DEV acceptance are distinct statuses.
