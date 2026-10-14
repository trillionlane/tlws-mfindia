# MF-E05 — official enrichment evidence foundation

## Scope and current evidence level

This is a backend-only, opt-in implementation. It introduces a private evidence
store and four read routes, not accepted official fund facts. Existing eleven-route
profiles and all previous contracts stay frozen. All-AMC coverage is the target;
the population comes from current official discovery, not a hardcoded count.
UTI/SBI/Kotak are the operator's reported confirmations, not the universe limit.

Local tests use **synthetic documents and a disposable PostgreSQL 18 database**.
They do not certify downloaded factsheets, legal permission, Cloud IAM, live
deployment, whole-AMC coverage or consumer business acceptance. The first live
AMFI directory retrieval timed out on 2026-10-14; no document was substituted
with a search snippet and no new document was imported into DEV.

## Preservation and capability boundaries

- New `mf_enrichment` schema: immutable content-addressed bytes, acquisition
  manifests and exact-identity candidate mappings. Original `mf` data, prior
  facts, calculations, review history and policies are never overwritten.
- Optional SQL 027 is not part of ordinary source migrations. Installation uses
  a direct owner, consistent snapshot and source/prior-layer preservation checks.
  It does not record a migration in the source ledger.
- Production-style default grants are revoked on the **new objects**, including
  ingestion access. Existing restricted review identities receive only narrow
  AMC ID/name reads, evidence metadata reads, and generator-only evidence append.
  Readers/admin API identity cannot read raw document bytes or append evidence.
- A report of source-use authority is not a certificate of legal clearance.
  Source-specific conflicts must be resolved before acquisition/import; the
  framework neither obtains agreements nor authorizes bypassing access controls.
  AMC statements are not automatically extended to AMFI document retention or
  third-party TRI. No factsheet sale/republication, copied narrative or endorsement.
- Scope, hosts, URLs and dates are operator-supplied assertions. Bytes/hash are
  checked, but this parser does not authenticate acquisition provenance. Metadata
  does not claim independently verified source dates or source correctness.

## Deterministic policy and mapping

The immutable `official-enrichment-evidence` policy specifies normalization,
required headers, size/row/batch bounds, mapping rules and unavailable reasons.
Static UTF-8 HTML scheme tables require unique exact header names: `AMFI scheme
code`, `Scheme name`, `ISIN`, `Plan`, `Option`. Scheme/AMC/plan/option must match
after declared normalization and the source ISIN must pass its checksum and
match that exact existing variant. No family-name search, fuzzy aliases or
sibling borrowing. Duplicate rows are ambiguous even when identical.

PDF bytes are retained as **unsupported**, not parsed or declared accepted.
There is no PDF-to-facts fallback. JS-generated tables, merged cells, unsupported
columns/layouts and malformed inputs fail closed or return explicit coverage.
Real source parser adapters and field-level acceptance belong to the following
enrichment sprint; unsupported coverage is not success for that feature.

The import transaction locks the source-operation boundary and seals one document
plus up to ten requested fund mappings atomically. Retries replay exactly. A later
mapping/identity mismatch is a conflict, not an update to historical evidence.
Coverage checks current fund identity against the captured hash. `matched_candidate`
does not mean reviewed facts, portfolio completeness or comparison eligibility.
All responses explicitly retain `facts_available:false` and `universe_complete:false`.

## Operator commands

`scripts/official_enrichment_evidence.py --help` has no network or DB effects.

1. Discover candidates from bounded already-acquired official directory bytes:
   `python scripts/official_enrichment_evidence.py discover --directory PATH`.
   This prints recognized links and content/policy hashes; it is not a claim of
   complete AMC discovery or automatic approval of linked hosts.
2. After a fresh immutable backup, restored-clone rehearsal and reviewed
   deployment manifest, install SQL 027 using the separately authorized owner:
   `python scripts/official_enrichment_evidence.py install`.
3. Import one approved original document with a bounded intake JSON:
   `python scripts/official_enrichment_evidence.py import --manifest PATH --document PATH`.

Install/import CLI DSNs must point to `/cloudsql/trillionlane-dev:asia-south1:tlws-mf-data-dev`,
database `mfdataindia`, with direct `mfdata_admin` or `mfdata_verified_generator`
respectively. No automatic fallback to local/production/owner impersonation.
Owner credentials and consumer signing keys must not be fetched to the laptop.
Local restored-clone rehearsals call the tested installation/import functions
directly using explicit clone-only principals; they do not relax the live CLI pin.

The `Intake` model rejects duplicate/invalid codes, malformed/secret-bearing URLs,
unapproved final hosts, naive timestamps, dates beyond the observation, explicit
source conflicts and extra fields. It records declared media type, original
SHA-256, AMC, timezone-aware observation, optional source date, allowed hosts,
requested codes, source-use basis and a non-secret authority reference.

`enrichment.acquisition.fetch` is an operator transport helper, never called by
an API, application startup or import command. It pins verified public DNS
addresses, validates TLS hostname, bounds bytes/redirects/time, refuses proxies,
cookies, private addresses, unknown hosts, credentials, query strings and
compressed responses. It does not resolve source-use scope. If a real source
needs a currently unsupported URL/layout, add a reviewed adapter rather than
weakening these boundaries.
The transfer deadline accounts for elapsed connection/header/body time, but the
OS DNS resolver itself has no independently cancellable deadline in this helper;
run acquisition within a bounded operator job rather than a request handler.
TLS uses the existing Requests dependency's public CA bundle with hostname
verification enabled. A local system-store failure is not grounds for disabling
verification or trusting a provider certificate manually. On 2026-10-14 an SBI
factsheet HEAD request verified with this bundle; this is connectivity evidence,
not a retained source import or a DEV certificate. Kotak's tested URL redirected
to a third-party bot-protection challenge, which the transport refused to follow.

## Private read contract

Explicit factory: `mfdataindia.review.app:create_enrichment_app` (15 routes).
New contract: `contracts/mfdataindia-official-enrichment-v1.json`.
Cloud internal ingress/IAM and the existing signed backend assertion remain
required; the absent-trust factory stays dormant and never connects to PostgreSQL.

| New GET route | Meaning |
| --- | --- |
| `/api/enrichment/v1/policy` | Exact installed executable policy/hash |
| `/api/enrichment/v1/documents` | Bounded manifest pagination by sealed ID |
| `/api/enrichment/v1/documents/{document_id}` | Immutable manifest; never original bytes |
| `/api/enrichment/v1/funds/{code}/coverage` | Latest observed evidence or pinned document, candidate/unmapped/stale status |

All use `Cache-Control:no-store`. There is **no HTTP import, raw-document download,
source-field mutation or public acquisition endpoint**. Collection pagination is
an ordered live inventory, not a stable historical snapshot during new appends.

## Remaining live acceptance before closing MF-E05

Obtain real permitted source bytes and exact bounded source/date/fund manifests;
record failures/missing coverage rather than fabricate them. Fresh backup and
operational restored-clone rehearsal must preserve all source and prior-sidecar
rows, schema, ACLs, sequences and hashes. Deploy a zero-traffic private candidate
at an exact reviewed image/SHA; certify new metadata/coverage routes and all eleven
prior routes via the consumer's actual private identity, including public denial.
Promote only that candidate, recertify at the fixed URL, test traffic recovery to
the exact prior eleven-route revision and restoration. No data/schema deletion
as rollback. Verify final raw-document retention and whole-source preservation.
MF-E05 is not complete until these actual operations and evidence are recorded;
green unit/CI tests or an empty coverage response cannot satisfy that acceptance.

## Reused legacy adapters and bounded source captures

Existing source work was identified after #53: `research/amfi_amc_directory.json`
is a 2025 archived AMFI seed; `ingest/amc_factsheets/download.py` discovers ABSL
documents and `absl.py` parses extracted PDF text. Its legacy writer updates
`mf.fund_facts` and provenance, and must **not** be run under preservation-first
scope. The legacy CLI also requires `pypdf`, absent from the current lock/runtime.
Reuse discovery/parsing with versioned sidecars rather than invoke that loader.
The live ABSL discovery probe did not find the expected datasource identifier;
the old parser's passing fixture tests do not prove the current downloader works.

AMFI [NAV history](https://www.amfiindia.com/net-asset-value/nav-download) limits
each request to 90 days, not history retention to the latest 90 days. The existing
`AmfiClient`/history parser already support segmented older ranges. Direct history
requests and generated ranges now enforce <=90 **inclusive** days before fetching.
The legacy backfill's upsert can replace existing NAVs; do not invoke it, reset
checkpoints or run `--force`. Reconcile retained values and preserve conflicts
before a separately rehearsed missing-only addition is considered.

`scripts/capture_official_sources.py` acquires bounded samples into an existing
task-owned private directory. No database connection, identity mutation, source
upsert, deletion or overwrite. It reuses the history client/parser with a bounded
no-proxy/no-netrc/no-redirect session, retaining exact original bytes separately
from decoded parser text. Files and receipts are content-addressed/exclusive-create;
an existing checksum mismatch fails rather than replacing either object.
Requests are capped at 12 per invocation and spaced; a capped partial run reports
remaining coordinates explicitly. Bodies over 16 MiB fail; use shorter windows
instead of assuming every 90-day whole-market response fits that bound.

The first [acquisition checkpoint](../evidence/mf-e05-source-capture-20261014.json)
retains 14,412 observed NAV rows across four files and one official SBI PDF, about
8 MB of original source bytes. Current-date sample: 12 October 2026, all three
scheme-type slices. Older-history sample: 2 January 2020, open-ended slice only.
There were no quarantined parser lines or out-of-request dates in these samples.
These are actual local acquisitions, **not new database rows or full coverage**.
The SBI filename denotes April 2026, not a current-October factsheet; its content
date remains unverified and it is retained raw, not published as fund facts.
Original database data, serving profile and schedulers were untouched.
