# FS-09 — SBI DEV source acceptance, 2026-10-17

This is source/enrichment acceptance, not PROD, UI, whole-AMC completeness,
human verification or a blanket API-performance claim.

## Accepted runtime and real executions

- Source/review Git SHA: `ff32de06857699fd05f48b28858fe15f89b4484b`.
- App image: `asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia@sha256:3486d615c71eb91db4f852ef2f92ee35de15d927967cf6d012b72cea482818ee`.
- Tools image: `asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia-review-tools@sha256:0d8ebc9848b5b44a0f59497c152bb8d5fec253beb9beab9e7034137a383e3d1c`.
- Source registry 2, configuration hash
  `fa113f5e095fe5b7ad1e738401552d8ffbea15d44ebeb5e3fdf313680f6cf221`.
- Current policy 5, hash
  `151093a8798bb41803315b73bca944bb292adc661701173225173958d3b17302`,
  generation 19.
- Manual execution: `tlws-mf-official-facts-refresh-dev-dpgf9`.
- Genuine 13:50 UTC timer execution:
  `tlws-mf-official-facts-refresh-dev-wqw29`. Four exact-image task receipts
  and all immutable archive generations/full original bytes were verified.
- Independent current/older source canaries, private extension reads and all
  19 core routes/22 contract responses passed. Actual recovery, repeated
  recovery and reactivation were exercised; failed attempts remain retained.
- Daily source schedule restored to 09:00 Asia/Kolkata. NAV/audit schedules,
  protected core runtime and private IAM remained unchanged during this source
  rollout. No public ingress or broader invokers were added.

Acceptance certificate:
`248eeeaeab6df60fbed2f6e9dc7f917e76fcb058e0d464b9ffc6114fb44d1db1`.
Level: `DEV_SBI_EXPANDED_TWO_PERIOD_ACCEPTED`.
[GitHub closeout record](https://github.com/trillionlane/tlws-mfindia/pull/84#issuecomment-6235184509).

## Preservation and the explicit facade boundary

The fresh before/after exports retain 14,405 original funds and 4,248,499
original NAV rows. Full original row, function, trigger, privilege, ownership
and sequence seals matched. Every previously retained layer row and the whole
previous-AMC projection passed; human-review ledgers did not change. Zero
source-row edits/deletions or human verification events.

- Before dump SHA-256:
  `a065b44dba4f60d18728178ad7498ebb155b9bffdf895e55609db9cc51dbac53`.
- Final after dump SHA-256:
  `9d9e6097f5ccd34f9ac60a34a0a9b6ca45fd07ed225680a73c96b6ad74cde779`.
- Final complete prior-layer seal:
  `a1c29ec4b93bdeb9cbd4662f144b5ca3f436769968adc0232872a37f822733dc`.

Two operational observers initially failed closed. The earlier recovery
observer incorrectly expected the established v9 profile to disappear when
restoring an older source authority. The second compared the active read
facade as though it were immutable stored source structure, despite the
approved equivalent-read facade optimization from #86. Both failures restored
the known prior state; no failure evidence or source record was overwritten.

Actual immutable dump inspection with PostgreSQL 18.6, **without a database
target or restore**, proved 465 other original schema objects byte-identical.
Only `mf.current_fund_facts` changed, and its query exactly matched the
registered `mf_live_facts.resolved` query in each snapshot. The closeout binds
that exact inspected structural difference; it does not ignore arbitrary
structure hashes. Full raw-row/other-structure comparison, the original full
`current_facts_baseline=True` preservation comparison and all prior-layer row
checks still passed. Twenty actual-proof/negative comparison tests passed.

Forensic result SHA-256:
`6fc79e1c4ec3b55d1b5647a6bf37da95264fba3cbacb539dd584ad05f249494f`.
The original dumps, failed attempts, original helpers and independently bound
continuation remain retained. This exception is specific to the inspected
authorized facade transition, not permission to modify source tables.

## Actual coverage, not completeness

| Reporting period | Retained variants | Usable | Explicit gaps | AUM | Regular BER | Benchmark | Managers | Risk |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-09-30 | 258 | 115 | 143 | 109 | 115 | 92 | 86 | 115 |
| 2026-08-31 | 258 | 108 | 150 | 108 | 108 | 85 | 78 | 104 |

The main gaps are missing exact issuer bridges (124 per period), conflicting
variant identity (six), missing/ambiguous monthly headings (12/19) and an
unsupported identity (one). No fuzzy matching, source correction or guessed
values were used to clear them. Usable is not every-field complete. Every
observation remains `needs_verification`; adjusted IDCW analytics remain
disabled and `all_amc_data_complete` remains false.

## Separate API safeguard and remaining incident

The active-source read-timing sample exposed slow small requests despite
correct responses. [PR #89](https://github.com/trillionlane/tlws-mfindia/pull/89)
has complete ordered actual-data old/new response equality and a narrower
paged/peer facts lookup. It merged as
`2f173e4abc4b05e2c4c20c69d96029c29646373d` after exact-head CI passed
1,743 tests with one skip. Main CI, immutable images and actual private
candidate/live certification were a separate prerequisite before Kotak/UTI;
merge alone did not mean that API revision was serving. That subsequent
[API-only acceptance](FS09_API_PAGED_DEV_ACCEPTANCE_2026-10-17.md) now records
the actual serving revision, protected runtime and measured private HTTP.

The separately recorded normal 12:30 UTC NAV refresh failed on next-day ETF
records before database storage. This source sprint does not weaken its date
guard or certify NAV ingestion healthy. Preserve that incident and its
evidence as a separate data-availability item.
