# All-AMC official facts expansion

## Approval and preservation boundary

Approved 2026-10-16 following FS-01–FS-05 acceptance. Backend/data only: plan,
implement, test, push, explicitly labelled self-review, merge, controlled DEV
deployment and certification per finite sprint. No UI, production, original
record deletion/correction, wholesale model replacement or invented evidence.
Official facts advance the existing API through dated reversible overlays;
originals, histories, NAVs and canonical analytics remain preserved. Publication
does not require fabricated human verification. Mathematical derivations need
their own versioned deterministic policies and do not belong in source parsers.

## Programme

| Sprint | Finite delivery | Acceptance |
| --- | --- | --- |
| FS-06 | Versioned official directory register and read-only private AMC/scheme census | Reconcile the union of directory entries and retained database AMCs; report unresolved records, source links, reporting periods, archival, extraction and current projection separately. No fetching/publication expansion. |
| FS-07 | Registry-driven bounded multi-AMC acquisition/publication | Preserve the pilot; two real source configurations, independent failure boundaries, exact identity, replay, permissions and recovery. No arbitrary caller URL or cohort overrides. |
| FS-08 | ABSL scheme/layout expansion | Every in-scope scheme accounted; published usable evidence or explicit gap; two reporting periods, private core/extension reads, actual schedule and preservation. |
| FS-09 | SBI integrated onboarding | Source/identity/layout oracles, immutable originals, bounded cohorts, dated current publication, real scheduling and recovery. |
| FS-10 | Kotak integrated onboarding | Same gates, separately verified units, dates and variant applicability. |
| FS-11 | UTI integrated onboarding | Same gates; missing/ambiguous evidence remains visible. |
| EXP-01 onward | Named remaining AMC cohorts | Exact memberships in the checked-in register; normally no more than four AMCs per cohort. Split complex layouts into finite sub-sprints before implementation; never declare a whole cohort complete because one AMC works. |
| Field-depth sprints | Additional useful facts and remaining section parsers | Existing five current fields are a starting point, not a ceiling. Inventory all fund-related sections; extracted values have units/scope/lineage or explicit unavailability. Top-ten holdings are not full portfolios. |
| Final closeout | Whole-universe reconciliation and consumer handover | Actual coverage denominators, no silent omissions, recurring acquisition, all private contracts, recovery and preservation certified; unresolved exceptions are not labelled complete. |

FS-06 register assignments are proposed work cohorts, not runtime permission.
SBI/Kotak/UTI retain their agreed priority. Remaining entries are grouped
deterministically by directory-name order into cohorts of at most four. Source
complexity is **not assessed** by that grouping. Resolve legacy/HTTP/missing
listings and inspect real layouts before committing to an onboarding batch.
The six missing links stay assigned and visible, not removed to improve coverage.

## FS-06 original evidence and denominator

The official AMFI factsheet directory was fetched through existing DEV egress
at `2026-10-16T19:51:43.601125+00:00`, execution
`mf-fs06-directory-capture-261016a-mqd45`. Original HTML: 291,795 bytes,
SHA-256 `83515327db20693cc23aaf730f7fe7a510d65257652b50e07ab5a7d401531ec2`.
Retained original and receipt: local operational ledger
`artifacts/mf-fs06-source-20261016/`; original bytes also bound to that immutable
execution's structured evidence. The derived register is
`config/official-amc-register-v1.json`.

There are **57 embedded directory entries**, of which **51 have factsheet
links**. The embedded Next flight JSON is decoded as JSON, never executed;
rendered factsheet-card names must exactly reconcile with nonempty listed
links. The older anchor-only discovery helper returns no entries for this
layout; it remains unchanged rather than silently reinterpreting an old policy.

These counts do **not** establish all legally registered or active AMCs.
The report uses the union of this dated directory snapshot and every retained
database AMC, including historical and unresolved records. It does not infer
launch/closure from a missing URL or absent database record.

An independent restricted-reader observation on dataset version 23 found
55 database AMCs, 14,405 retained scheme variants, 4,362 in-scope variants and
4,308 non-defunct in-scope variants. These are dated observations, not fixed
runtime assertions. NAV ingestion can legitimately advance this population;
the report reads the current denominator without modifying it.

Directory entries with missing links: AlphaGrep, Carnelian, IL&FS (IDF),
Lakshya, Monarch and Nuvama. Missing links do not prove no factsheets exist.
Listed HTTP URLs, fragments, query parameters and noncanonical characters are
retained exactly and flagged for source-adapter review. Census URLs are never
an acquisition allowlist and no URL is followed by the reporting API.

## Private consumer contract

Explicit opt-in profile `create_amc_coverage_app`: 46 path templates, preserving
all earlier 43 path/component contracts. Existing ingress, IAM, assertion trust
and read/admin roles are unchanged. No schema migration, new secret or per-AMC
environment variable is needed. The existing source job and schedules stay pinned
to their accepted configuration; FS-06 does not deploy its image to that job.

- `GET /api/enrichment/v7/policy`: versioned census semantics.
- `GET /api/enrichment/v7/coverage`: dated directory provenance, all reconciled
  AMC rows, scheme counters, actual reporting periods and planned wave membership.
- `GET /api/enrichment/v7/amcs/{amc_key}/schemes`: bounded code-ordered details,
  `limit=1..100`, `after_code`, `expected_revision`. Continuation requires the
  first response's revision; changed dependencies return 409. Unknown AMC: 404.

Keys are `amfi-<directory member id>` or `db-<unresolved database amc id>`;
these ID spaces are never conflated. Only exact Unicode/case/whitespace-normalized
AMC-name matches reconcile rows. Ambiguous names remain separate database-only
rows, and all database schemes must appear exactly once in the census.
This reconciliation is **not** approval of scheme/ISIN identity for publication.

All requests require the existing signed backend assertion and private Cloud
Run invocation. All responses are no-store. The census has no mutation or fetch
endpoint; raw originals, credentials and signer material are never returned.

## Honest measurement

Count source listing, retained monthly originals, extracted snapshots and fields
exposed by the active current projection independently. Monthly counts concern
the established monthly namespace, not every original in every historical layer.
An intake code association is archival evidence, not an approved identity match.
`accepted_field_observations` counts historical assertions, not unique current
usable fields. Each current field carries its own disclosure date.

No snapshot means no monthly-snapshot evidence; never manufacture zero-valued
facts. A missing month, stale field or unsupported layout does not erase the
last usable current value. `all_amc_data_complete` and current-month certification
remain false. Human verification is separate and is not asserted by this report.
No IDCW total-return, full-economic-overlap, UI or production readiness is added.

Capture is repeatable-read/read-only under the restricted reader. Explicit
population bounds fail rather than truncate totals. The coverage revision binds
the source register, dataset version, actual fund/AMC identity and scope fields,
monthly source/snapshot metadata, current selection and projection configuration.
SQL independent of the report implementation checks every database AMC and
retained/in-scope/defunct population during private HTTP certification.

## DEV acceptance still required after merge

Fresh backup/new restored clone; full preservation and actual populated census;
exact-image private candidate and current-profile compatibility; activation;
known prior traffic recovery and restoration; anonymous denial; final census
against independent SQL; final full source/prior-layer comparison. Verify source
job configuration and all existing schedules unchanged. Record exact merge SHA,
digests, immutable executions and sealed acceptance on the PR. CI success alone
does not establish this rollout or any broader source coverage.
