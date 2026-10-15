# MF-E05 — official enrichment evidence foundation

## Scope and current evidence level

**Subsequent bounded bridge:** MF-E06/PR #58 is deployed and certified at
`074b126ba6574a2188cb6ca738dccece0bb45e41`, with an eighteen-route private profile.
ABSL 103174/103173 now have separately verified v2 benchmark/manager/BER
disclosures through an issuer code/ISIN identity bridge. The older v1 PDF
mappings below remain explicitly unsupported under their original policy;
no historical evidence was rewritten. [E06 evidence](../evidence/mf-e06-dev-fact-261015a.json)
and [consumer handover](MF_E06_OFFICIAL_FACTS_DEV.md#backend-consumer-handover).
Current all-AMC discovery/broader coverage remains open. The paragraphs below
describe the earlier metadata-only checkpoint, not the current service profile.

**DEV checkpoint:** PR #56's private provenance/coverage release is deployed at
`3e589fb529b59a61d7360dd7180d904b143d4ffd`. Fresh backup, operational restored
clone, restricted installation, document append/replay, candidate/fixed/restored
private HTTP, real traffic recovery and final whole-source/prior-layer comparison
passed. [Release evidence](../evidence/mf-e05-dev-evi-261014a.json). The service
at that checkpoint ran `create_enrichment_app` with fifteen route patterns, 100% untagged
traffic and the existing private IAM/ingress/assertion controls. The new four
patterns are `/api/enrichment/v1/policy`, `/documents`, `/documents/{document_id}`
and `/funds/{code}/coverage` under that prefix. Original-document bytes are not
served. Use the existing consumer-owned signing key and Cloud Run default-service
audience; this release does not wire an Insights application client or certify
its end-user RBAC. No new GitHub gate variables were added.

The two requested ABSL variants are 103174 (Regular Growth) and 103173 (Regular
IDCW). Their stored v1 mappings remain `unmapped/pdf_parser_not_implemented`.
The local text extraction under a newer policy is not silently substituted for
an accepted v1 mapping. This checkpoint completes the **metadata-only deployment**,
not exact source-field acceptance or the whole MF-E05/15-sprint goal. Continue
with verified identity/field publication; do not repeat this accepted rollout.

The evidence-index documentation commit may be newer than the deployed code SHA.
Do not redeploy identical runtime code solely to match a documentation-only head.

This is a backend-only, opt-in implementation. It introduces a private evidence
store and four read routes, not accepted official fund facts. Existing eleven-route
profiles and all previous contracts stay frozen. All-AMC coverage is the target;
the population comes from current official discovery, not a hardcoded count.
UTI/SBI/Kotak are the operator's reported confirmations, not the universe limit.

Local tests use **synthetic documents and a disposable PostgreSQL 18 database**.
They do not certify downloaded factsheets, legal permission, Cloud IAM, live
deployment, whole-AMC coverage or consumer business acceptance. At the initial
#53 implementation checkpoint, AMFI directory retrieval timed out on 2026-10-14;
no search snippet was substituted and no document had then been imported into
DEV. The completed metadata-only release above supersedes that no-import state.

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

### Approved next finite release: private DEV evidence rollout

Deploy provenance/coverage, not accepted fund facts. Preserve the eleven existing
routes, use `create_enrichment_app` for the fifteen-route candidate, retain one
exact captured ABSL PDF and its declared two-fund Growth/IDCW cohort. Both PDF
mappings remain explicitly `unmapped/pdf_parser_not_implemented` under policy v1;
the newer local extraction artifacts are separate evidence, not secretly accepted
by the old mapping policy. No whole-E05 or enriched-field completion claim.

Execution sequence:

1. Reconcile merged SHA, exact app/tool image digests, existing human operator,
   private ingress/IAM, secret versions and the eleven-route recovery revision.
2. Fresh regional immutable MVCC export; restore only into a new isolated clone.
   Rehearse SQL 027, effective restricted roles, original-byte append/replay,
   metadata reads and denial of raw blob access; compare original/prior layers.
3. Execute the hash-pinned optional installation with the direct migration owner.
   Existing source data/default privileges are not rewritten. Add one original
   document through the dedicated generator, never the legacy facts loader.
4. Source transfer is one private, create-only, exact-generation bucket object
   inside this rollout prefix. Only temporary object-scoped generator read access
   is permitted, with unconditional removal/verification after import, including
   failed attempts. No grant to the consumer signer or local owner-DSN retrieval.
5. Stage a private zero-traffic candidate. Run existing forward/historical checks
   against the original four-fund control cohort, and new signed consumer-owned
   HTTP checks for all four evidence route patterns, missing assertion, replay,
   cache control, unsupported/partial coverage and absence of HTTP intake/raw reads.
6. Promote only that exact candidate after all gates. Recheck the fixed URL,
   restore the prior revision and verify evidence routes disappear, then restore
   the candidate and recertify. Restore prior traffic before any candidate lookup
   on failure; clear tags without deleting retained revisions or records.
7. Take a second regional export. Prove every original source/prior-layer hash
   unchanged; only the one declared policy/document/blob and two mappings may be
   new. Keep backup generations, execution certificates and recovery proof.

`deploy_official_evidence_dev.py install|import` and
`certify_official_evidence_dev.py [--recovery]` are fixed DEV workers. They bind
the exact source manifest/hash/generation and direct principals before connecting.
HTTP certification states `accepted_facts_published:0`; this finite deployment
does not certify source correctness, mapping acceptance, all-AMC coverage, field
enrichment or Insights application RBAC. No additional GitHub gate variables.

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
scope. The legacy CLI required `pypdf`, initially absent from the lock/runtime.
Reuse discovery/parsing with versioned sidecars rather than invoke that loader.
The live ABSL discovery probe did not find the expected datasource identifier;
the old parser's passing fixture tests do not prove the current downloader works.

### Discovery repair and bounded PDF bridge

A later live probe found the datasource identifier, but the Sitecore API
returned `ReturnCode: "1"` with `Record Retrieved Successfully`, unlike the
legacy client's accepted `"0"`. Both observed success variants now require a
validated document-list schema. Titles must identify a unique known month in the
requested year. Links stay in the exact official ABSL factsheet media tree;
queries, unrelated hosts and path traversal are rejected. Period sorting is
year-first. No datasource GUID is hardcoded.

`capture_official_sources.py --archive PRIVATE_DIR discover-absl --year 2026`
uses pinned-DNS/bounded transport for two requests. Only the exact constructed
Sitecore datasource/year query is allowed; general acquisition rejects queries.
Original landing/API bytes are retained. No PDF or database import is automatic.
On 2026-10-14 this adapter retained nine documents, January–September 2026.
September is **latest listed**, not proof of current October or all-AMC coverage.
The initial bounded September-PDF attempt timed out; no partial document was
accepted. Following the legacy adapter's documented Referer requirement, the
new source-specific media adapter adds only that fixed public same-origin header
and a bounded 30-second timeout. It then captured the exact 7,633,719-byte PDF
(344 pages), without cookies, proxying, credentials or new host exceptions.
All redirects still require approved hosts/media paths; dot-segment paths fail.

`capture_official_sources.py --archive PRIVATE_DIR extract-pdf --document PDF
--expected-sha SHA [--absl-period 'September 2026']` checks the original hash and
archives text separately. The immutable policy records implementation/version,
page/text/decoded-stream bounds, 20-second CPU and 30-second subprocess deadline.
Linux additionally enforces 512 MiB address space; macOS has CPU/deadline/size
controls but no address-space cap. This is **not an OS security sandbox**.
Encrypted, malformed and oversized documents fail closed; image-only pages
retain empty text without OCR or inferred content. No scripts or external
document retrieval are executed. The lock now includes pypdf 6.20.1.
The optional `--page-start`/`--page-end` bounds permit resource-limited windows
while retaining original page numbers, whole-document page count and an explicit
partial-extraction flag. Candidate layout parsing also runs in a limited worker.
Policy version 2 defines those page-window semantics; earlier extraction artifacts
remain untouched. Fourteen ranges extracted all 344 ABSL pages, with source,
policy/version/engine seals checked and every original page accounted for exactly
once. They yielded 69 **unmapped** named candidates, not 69 accepted enriched
funds. Page coverage is not official field, portfolio or AMC-universe completeness.

The legacy ABSL pure parser emits **unmapped scheme candidates** only. Its
float-based numeric fields and name matcher are not used for fact publication.
Exact variant mapping and decimal/raw-token, field/date/unit evidence remain
E06 requirements. Extracted narrative stays private and is not served by APIs.

The SBI PDF produced 61 pages (60 nonempty); rendered cover inspection agrees
with text's `FACTSHEET AS ON APRIL 30, 2026`. This confirms the document date,
not every field. Some pages say `For Internal Circulation Only`; retain that
notice alongside the operator's reported SBI permission and resolve intended
use before accepted-field publication. Extraction is not source-use clearance
or fund identity proof. SQL 027/PDF classification and the 15-route contract
are unchanged. Local extracted text has not been imported as accepted facts;
this bridge alone does not close MF-E05's DEV acceptance.

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
