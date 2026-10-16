# Official monthly fund facts: preservation-first process

## Release boundary

The approved FS-01–FS-05 programme is backend/data only. Establish the
process with Aditya Birla Sun Life Large Cap Fund, Regular Growth (103174)
and Regular Annual IDCW (103173), then expand through separately tested
AMC/layout adapters. Two variants do not establish all-AMC coverage.

Existing source records, NAV history, source identities, facts, analytics,
policies and review history must not be overwritten or deleted. Fresh backups
and new restored clones are retained. Source preservation is not a reason to
freeze stale values in the current API: later usable official disclosures
advance a reversible, per-field current projection.

## Five finite deliveries

| Sprint | Finite result | Acceptance boundary |
| --- | --- | --- |
| FS-01 | Current official benchmark, managers and BER over retained E06 disclosures; two private provenance reads | Exact-merge images, fresh regional backup, restored-clone switch/recovery, real private/core HTTP, actual live recovery and post-export preservation |
| FS-02 | Larger bounded book decoder, immutable monthly originals/snapshots and two private reporting reads | Two original reporting periods and both exact variants; independent values, units and availability references; immutable/replay/atomic-failure and effective-permission tests |
| FS-03 | Recurring capture/diff/publication into the existing current API | Bounded official discovery, exact originals, no late-month rollback, field-level missing/conflict handling, actual scheduled DEV execution, freshness and run outcomes |
| FS-04 | Backend change/history and evidence-bound human verification | Signed administrator, reason/evidence, exact source/dependency version, idempotency and optimistic concurrency; new evidence invalidates old verification; no UI or source-record edits |
| FS-05 | Certified process and consumer handover | Fresh restored clone, source-to-API oracle, prior/new contracts, effective roles, actual recovery/restoration, preserved source/layer inventory and explicit coverage/gaps |

Each release is pushed, self-reviewed, CI-checked and merged before its
controlled DEV rollout. Self-review is identified as such. Image preparation,
merge, schema installation, traffic promotion, activation and certification
are different evidence states, never interchangeable success claims.

## Original evidence and exact identity

The September publication contains August 31 reporting data; the October
publication contains September 30 reporting data. Publication month is not
silently substituted for a fund's as-on date. Retain the original PDF, URL,
observation time, SHA-256, page selection, extractor version/code/policy hashes
and exact text hashes. Retain the issuer summary used to bridge each AMFI code
and payout ISIN to the current AMC/name/plan/option/ISIN identity. No fuzzy or
name-only match and no Direct-plan borrowing are permitted.

The frozen older PDF policy stays at 8 MiB. The additive monthly-book policy
accepts at most 16 MiB, 400 total pages and 24 explicitly selected pages in a
resource-limited subprocess. It does not execute PDF scripts, OCR a gauge,
decode a riskometer pointer, or claim whole-book extraction coverage.

Official pilot sources:

- [September ABSL book](https://mutualfund.adityabirlacapital.com/-/media/bsl/files/resources/factsheets/2026/empower-factsheet---september-2026.pdf)
- [October ABSL book](https://mutualfund.adityabirlacapital.com/-/media/bsl/files/resources/factsheets/2026/empower-for-the-month-of-october-2026.pdf)
- [Issuer scheme-summary identity source](https://nsearchives.nseindia.com/trading_security/mf/pdf/Aditya_06042026165307_SchemeSummaryDocument.pdf)

The intake retains the user's reported authorization/use basis; this runbook
does not manufacture written permission or make a new legal determination.

## Field interpretation

- Risk: use the explicit **Scheme Riskometer / Current Month** text row on
  the same fund's change page. Never use Previous Month, a benchmark risk
  gauge, or the printed list of all possible risk labels.
- BER remains BER, not TER. The change table's current Regular value must
  corroborate the detail-page Regular BER. Preserve the original decimal token.
- AUM/AAUM become INR-crore values only when the source explicitly prints Cr.
  An unlabelled number is retained as raw reported evidence with unknown units,
  not silently normalized or used to replace an existing current number.
- A reported fund inception is not automatically a plan/variant inception.
  Reported month-end NAV is not an ingestion repair or a replacement NAV point.
- Minimum **additional** investment and exit-load terms retain their scope;
  do not relabel them as an initial minimum or a transaction-availability flag.
- AMC-reported ratios and Regular/Direct/benchmark performance remain
  publisher-reported information. Unresolved option basis is explicit. These
  are not distribution-adjusted IDCW returns or our canonical calculations.
- Top ten means top ten, not full holdings or full economic exposure. Use the
  separate monthly portfolio source for full reported holdings.
- Printed NA is not zero. Unsupported sections remain explicitly inventoried
  as retained-but-not-yet-parsed; the original document is not discarded.

Reported numbers use deterministic decimal strings. Any later mathematical
engine must use its own immutable policy/version/hash and results layer.
Extracted facts must not silently replace published calculated analytics.

## Storage and private reads

`mf_monthly_facts.sources` retains original books; `snapshots` retains sealed
exact-identity reporting results. Only the dedicated generator can append.
Runtime principals cannot update/delete/truncate these tables; ingestion's
production-style broad defaults are explicitly revoked. Readers cannot select
original PDF bytes. The owner installation is opt-in, not a default migration.

The 37-route profile preserves all previous 35 paths/components and adds:

- `GET /api/enrichment/v4/policy`
- `GET /api/enrichment/v4/funds/{code}/facts`, optionally pinned by `snapshot_id`

The new profile is private Cloud Run plus the existing signed backend
assertion. There is no browser/public source writer or raw PDF endpoint.
Responses are no-store. The snapshot's original `needs_verification` status
is not human verification and is never rewritten in place.

FS-02 reports whether its monthly snapshot is included in the current core
projection; that is false until FS-03 integrates the richer source selection.
Do not mistake sidecar availability for current-core activation or freshness.
The existing v1/v2/v3 contracts are not reinterpreted as a new monthly format.

## Safety and certification

Use the exact merged SHA and immutable images. Run the existing regional
MVCC backup/export and restore into a new isolated PostgreSQL server, never
the retained local production database or an existing rehearsal database.
Record before/after source and enhancement-layer inventories. Never replace
an artifact or rerun an ambiguous write just to obtain a PASS certificate.

Current projection DDL must be short: no whole-source hashing while holding
the hot view's exclusive lock. Whole-source inspection is a separate
read-only repeatable-read operation. Restore known prior traffic and the
captured view independently of candidate lookup/cleanup on failure.

The first live FS-01 installation attempt hit its 120-second statement limit
while copying `nav_history` for inline preservation. Read-only reconciliation
confirmed transaction rollback, absent new schema and the original view hash.
The repaired namespace installers therefore retain inline **structure-only**
DDL/grant/owner/sequence checks, using a distinct evidence format which the
full row-preservation verifier rejects. They do not hold bulk source SHARE
locks. Full row hashing remains mandatory in separate read-only inspection
and backup gates; those scans have a bounded 300-second statement budget.
Activation/recovery still have the short 5-second statement/15-second total
budget. Structure-only success is never full data-preservation acceptance.

Monthly certification requires **four** independent references: both variants
at both real reporting dates. Each required field reference binds value,
explicit unit and availability status. HTTP/identity denials, replay,
no-store, raw-byte restrictions and absence of user writes are checked.
Cloud execution success and the exact bound certificate are both required.

The restricted HTTP certifier must not run owner-only core SQL. It derives
active expectations from the sealed original-source reference and recovery
expectations from its already-authorized immutable baseline view, then compares
the actual private core detail and batch HTTP responses. Missing baseline facts
fail closed rather than guessing sibling fallback. Owner-side activation still
runs the actual core SQL inside the switch transaction. No reader permission
expansion is needed. The first candidate check exposed this distinction and
was rolled back to the prior revision before correcting the certification path.

Human verification remains a separate later action. Do not create a fake
human verification in DEV merely to make a certificate green. Positive
human-review mechanics can be tested on the restored/synthetic clone; live
reported fields remain honestly `needs_verification` until actually reviewed.

## FS-03 recurring pilot and recovery

The optional `035_official_fact_refresh.sql` adds immutable catalog originals,
run receipts, complete-cohort publication receipts and owner-only transition
history. It does not edit `mf` rows. The generator can append source/run
evidence but cannot insert publication or transition records directly, execute
owner functions, change the schema, rewrite originals or forge an owner
bootstrap. The publication trigger requires the exact two current identities,
one original, one disclosed date, the registered monthly policy, and retained
catalog evidence. Provisional snapshot insertion alone cannot update core facts.

`config/official-facts-dev.json` fixes the pilot, identity source and private
archive. The recurring worker discovers the AMC's current-year catalog with a
previous-year fallback, obtains at most one 16 MiB book, archives the exact
original before parsing, and selects the exact fund's adjacent detail/change
pages within the first 24 pages. It does not guess a page after layout drift.
Catalogs retain their original bytes. Archive uploads use create-only
preconditions; both new uploads and replays verify a pinned immutable generation
and its SHA-256 before publication. Archive access is prefix-scoped create/read,
not overwrite/delete or bucket-wide source access.

The fixed single-task/no-retry job uses Cloud Run's
[execution identity](https://docs.cloud.google.com/run/docs/container-contract#env-vars)
for receipt replay. Acquisition or parsing failure records an allowlisted error
phase/type without retaining exception bodies. A failed run cannot claim a
successful freshness check or advance current heads. An uncertain commit is
reconciled by the immutable execution receipt before recording failure; it is
not automatically retried. Existing source provenance is reused for identical
original bytes; a new acquisition alias belongs in the run, not an overwritten
intake record.

Current policy version 2 accepts five scalar fields: benchmark, managers,
Regular BER, explicit scheme risk, and explicitly denominated INR-crore AUM.
It preserves each original token and requires exact numeric round-trip through
the existing column type; it never rounds an unsuitable disclosure into
eligibility. Each field selects its own latest unambiguous usable disclosed
date. Missing, unusable and conflicting newer fields retain their previous
usable source/date with an explicit warning. Old archive arrivals cannot
roll back newer heads. Publisher-reported statistics/returns remain separate
from canonical analytics; neither IDCW eligibility nor NAV dataset version is
changed by this process.

The private 39-route profile adds only `/api/enrichment/v5/policy` and
`/api/enrichment/v5/status`; every earlier route/component schema remains
unchanged. Status shows the last ten immutable outcomes, not all-AMC coverage
or human verification. Current-fact provenance and the monthly inclusion flag
report which source snapshot contributes at least one selected scalar field;
they do not claim the entire factsheet enters the core API.

Before activation: fresh regional backup, actual restored-clone rehearsal,
two-period original oracle, effective-role checks and private candidate HTTP.
Activation has a 5-second statement/15-second transaction budget and checks
actual core SQL inside the owner transaction. The restricted HTTP certifier
uses independent source expectations, never owner-only core queries. Recovery
restores all six captured views and the exact prior core view, not merely the
two gates or an assumed raw baseline; append-only history is retained.

Recovery replays the current-values view from its pinned original migration,
not its saved deparsed SQL: PostgreSQL's UNION-literal roundtrip can add internal
aliases and otherwise invalidate the immutable view seal. Every restored view
must match the original seal, and effective ACL verification runs before commit,
including the already-recovered retry path. Missing original SQL or any mismatch
rolls back all partial view replacements; it must not append a recovery receipt.
The HTTP certificate checks the captured prior policy, all six view hashes and
the prior core-projection state as well as its actual core values. Matching
numbers alone cannot prove recovery. It reports observed core and monthly-v2
activation separately; recovery to the earlier active v1 overlay is not an
inactive core view.
Traffic restoration must run independently of lookup, verification or database-recovery
failure. Reapply the approved graph and recertify after recovery. Keep existing
NAV schedules unchanged. A new facts scheduler is accepted only after a real
timed execution is reconciled to its job SHA/image, archived source, receipt
and private core responses; manual execution is not a scheduled-run witness.

## FS-04 human verification and source history

The optional `036_official_fact_verification.sql` adds an isolated append-only
human-review ledger. Runtime roles have no direct table write, schema-create,
source-edit or rewrite privilege. Only the existing dedicated administrator
principal can execute the narrow review function. Ingestion and the source
generator cannot insert reviews or invoke that function. The function checks
optimistic review version, exact dependency hash and selected evidence at the
database boundary under the same publication lock used by source refresh.

The 43-route private profile preserves all previous contracts and adds:

- `GET /api/enrichment/v6/funds/{code}/history`: bounded retained monthly
  snapshots, explicit publication status and a stable immutable-ID cursor.
  The declared cursor order is not recency order.
- `GET /api/enrichment/v6/funds/{code}/changes`: compare two retained same-fund,
  same-identity snapshots. Report changed values/status/units/scopes/reasons;
  an unchanged risk value is not a risk change just because its date advanced.
  Nothing is applied to source records.
- `GET /api/enrichment/v6/funds/{code}/verification`: current dependency
  artifact/hash, optimistic version, per-field review status and bounded events.
- `POST /api/enrichment/v6/funds/{code}/reviews`: signed `review:admin` only,
  stable `Idempotency-Key`, explicit original-source human attestation, reason,
  exact named fields and selected source snapshot IDs. The delegated actor
  comes from the signed assertion, never the editable request body.

Example body after reading the exact current state and original source:

```json
{
  "expected_version": 0,
  "expected_dependency_hash": "<hash from verification read>",
  "decision": "verified",
  "attestation": "human_reviewed_original",
  "fields": ["risk_level"],
  "evidence_snapshot_ids": ["<selected source snapshot ID>"],
  "reason": "Inspected the explicit Current Month scheme risk row in the original."
}
```

The backend is responsible for authenticating that real human before issuing
the signed administrator assertion. The provider records the attestation and
actor; it does not independently prove physical identity. A caller/actor,
request or fund change under the same key is a conflict. Exact replay returns
the original receipt, even after source changes; it never renews verification.

Dependencies include the current identity and current policy, selected values,
dates and snapshot IDs, and the latest field heads even where an older usable
value is retained. New inputs therefore invalidate old human verification even
if the displayed risk/value is unchanged. Only the latest actual review naming
a field controls that field; reviewing another field cannot clear its stale
review. A conflicting/unusable field cannot be marked verified by this API.
Human review does not resolve a source conflict, edit facts, advance publication
or change canonical analytics eligibility. Existing v3 field status uses its
already-defined `verified`/`needs_verification`/`needs_revalidation` values;
review events do not change the source facts revision.

The human-review policy is immutable in the existing policy registry. Positive
review/replay/concurrency tests belong on synthetic and restored clones, clearly
labelled as tests. There is intentionally no DEV rollout CLI operation that
creates a human review. Live certification submits only an incomplete,
read-scope request which cannot create a review even if an authorization defect
exists, and requires a denial plus an unchanged ledger. Never create fake live
human approvals to close out a sprint. No admin UI is included here.

## FS-05 finite process certification

`scripts/inventory_official_facts_dev.py` is a restricted-reader, read-only
inventory, not a cloud acceptance shortcut. `scripts/certify_official_facts_core_dev.py`
tests every actual private core read template and real nullable-quality evidence.
`scripts/reconcile_restored_official_facts.py` is local scratch-only and requires
the original backup hash and full preservation evidence before replaying a
narrow known PostgreSQL view roundtrip. It cannot target the live database.

The final acceptance sequence still requires a fresh backup/restored clone,
all retained layers and original bytes, all 43 private contract paths with
read/admin denials, the source-to-current manual oracle, actual traffic recovery
and restoration, immutable scheduled execution/source evidence, no public
access, unchanged NAV schedules and zero synthetic live human reviews.
See [consumer handover](OFFICIAL_FACTS_CONSUMER_HANDOVER.md). Broader AMC adapters
remain deliberately outside this pilot's completeness claim.
