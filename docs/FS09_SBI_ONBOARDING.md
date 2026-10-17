# FS-09 - SBI active/passive monthly facts

Approved with FS-08 through FS-11 on 2026-10-17. Backend/data only. This document
describes implementation and required gates; it is **not a DEV acceptance claim**.

## Finite delivery

Account for every current in-scope SBI variant using two reporting periods,
combining each period's active and passive books into one complete AMC batch.
Publish usable dated facts or explicit per-variant/per-field gaps. The restored
dataset-24 observation is 258 variants; do not hardcode that denominator.

Retain the ABSL pilot, HDFC checked edition, ABSL whole-book facts, all original
funds/NAVs and all prior immutable layers. No source-record correction/deletion,
UI, production, human review event or adjusted-IDCW eligibility change.

## Official acquisition and exact identities

- The public SBI factsheet page uses an empty-body catalogue POST to
  `https://www.sbimf.com/ajaxcall/CMS/GetRecentFactSheets`. Select the latest
  two complete, distinct, nonfuture active/passive reporting periods. Preserve
  their catalogue and all four actual PDF originals. A newer incomplete pair
  must not be silently represented as a complete latest edition.
- AMFI's public SBI directory is
  `https://www.amfiindia.com/api/populate-scheme?MF_ID=22`. Names select a bounded
  discovery cohort only. Retain each actually returned scheme-document index
  before considering the exact returned issuer XML URL. No guessed download
  link, name-only/fuzzy join, investor token endpoint or authentication bypass.
- XML supplies only exact AMFI code, checksum-valid ISIN and labelled
  Regular/option identity. Never borrow its undated risk/expense values as current
  facts. DTDs, entities and external references are forbidden. Unsupported XML,
  missing acquisitions, conflicts and unsupported current variants stay gaps.
- Numeric IPv4 preference still checks **all** DNS answers, pins one public
  address, and verifies the original TLS hostname. No cookie, proxy, address
  rotation, challenge following, redirect exception or automatic source retry.
  Per-document, total-byte, family, worker and task bounds fail closed.

## Three explicitly supported evidence layouts

1. Active primary detail: explicit Benchmark Index, complete manager block,
   Regular BER column, dated fund AUM in crore and explicit scheme-risk sentence.
2. Newer passive primary detail: separately labelled BER, AUM, manager and risk;
   Additional Fund Details/performance continuations are not second primaries.
3. Older passive LITE Ready Reckoner: exact table headers, dated AUM footnote,
   exact scheme row, benchmark, fund AUM and Regular **BER/TER** pair. Select BER,
   never TER or Direct. Managers and scheme risk remain unavailable in that
   table. A primary detail takes precedence; otherwise duplicate rows are
   ambiguous. This is not a fabricated full-detail page.

Keep physical page/text hashes, XML locators/index hashes, raw printed tokens,
actual source URLs and each field's actual PDF hash. For example, a printed
`1.40` stays `1.40` in evidence while the existing SQL numeric projection returns
canonical `1.4`; certification requires separate independent references for both.
Rounded printed AUM is retained at its actual precision, not padded or inferred.
Only an explicit AUM date or page-wide reporting header dates other context-based
fields; a date in AAUM/performance text cannot date them accidentally.

## Additive authority and storage

Registry v2 adds one SBI task and retains registry v1/source specifications
unchanged. Current policy v5 has the accepted v4 policy as its parent. The
compiled authority pins SBI parser, compiler, transport, archive, runtime and
bounded decoder code. Historical authority hashes remain reproducible.

Optional owner migration 039 extends only `mf_book_facts`: XML original media
and the reviewed multi-document trigger branch. The historical ABSL branch of
038 is preserved byte-for-byte; 038 itself remains unchanged. Original relations
and their records are not migration targets. Existing restricted roles retain
their exact privileges; there is no new ingestion/default-grant writer.

V2 batches reference **actual** originals. Their primary content hash names an
actual active PDF; `identity_content_hash` names the captured actual AMFI directory
JSON with an explicit role, not a synthetic document masquerading as a PDF. All
monthly and XML/index edges remain explicit. Byte-identical originals served at several actually
returned official URLs retain one immutable byte row and its **first** URL;
all acquired aliases and their retained directory/index/catalogue parent edges
are sealed in batch references. No canonical URL or original row is rewritten.
Each field uses its exact monthly extraction URL, not an arbitrary same-hash
alias. The pinned interpreter is the semantic trust boundary; SQL independently seals complete current populations,
types/units/dates, source/specification hashes, source-parent links, original
references, archives, two periods and the live dataset receipt. Unreceipted
batches do not publish. Original PDF/XML objects are create-only, private and
generation-pinned; catalogue/index bytes are retained in the append-only DB.

Installing v2 does not activate v5. The active policy selects its exact sealed
source configuration, not the most recently installed configuration. Policy and
coverage HTTP responses describe the **installed** recognized authority, enabling
the compatible new image to serve the old state before upgrade and after recovery.
No reader/admin receives EXECUTE on the private canonical-JSON helper.

## Private API and genuine operations

Keep the same 52-path private app and unchanged old path/component contracts.
The existing v9 policy, coverage and bounded fund-evidence endpoints represent
SBI too. Existing private core scalar aliases consume the same dated reversible
projection. There is no HTTP fetch/write/raw-document endpoint. Publication is
`needs_verification`, not a human-verified claim or whole-factsheet completeness.

The existing daily 09:00 Asia/Kolkata source job gains task 3, SBI, for four total
tasks, parallelism one and zero retries. Preserve the previous three adapters,
identities, secret versions, VPC and source schedule. Grant the existing generator
only create/read on `official-monthly-facts/sbi-expanded/`; no overwrite/delete,
bucket-wide access, new invoker or per-AMC GitHub variables. NAV/audit schedules
must remain running and unchanged.

## Required sequential delivery gates

1. Focused positive/negative parser, transport, SQL, runtime, permission,
   upgrade/recovery and HTTP tests. All previous contracts. Explicit implementer
   self-review, exact-head full green CI, normal merge and digest-image builds.
2. Before merge, a labelled source-code rehearsal may use retained real originals
   on a new restored accepted DEV snapshot. Local archive-contract fixtures and
   local identifiers are **not** GCS/execution/image/DEV acceptance evidence.
3. After merge, fresh backup/new restored clone and final-image rehearsal under
   the real owner boundary, complete original/prior-layer preservation, actual
   immutable originals, independent raw/canonical oracles and restricted HTTP.
4. Stage and certify compatible 52-path code against v1 first; explicitly promote
   that compatible code before installing v2. Ensure no old source execution is
   in flight. Capture sealed prior graph, source authority/function, traffic,
   source job and protected runtime. Install v2 without publishing new scalars.
5. Run all four tasks on the exact new image. Verify completion, identity/image,
   same-execution receipts for **every** source, all archive generations/bytes,
   complete denominators and independent raw-field oracles from both active and
   passive originals in **both** periods. Current SBI Growth/IDCW and both book
   layouts must be represented; old-AMC-only samples cannot certify SBI.
6. Certify private current/core aliases, then activate v5 against the captured
   prior seal. Hash the entire prior-AMC field/provenance/scalar projection, not
   just a few protected canaries. Missing/unavailable fields never erase last-good
   usable dated values; conflicting/ambiguous source evidence remains visible.
7. Actually restore known prior traffic, graph, source authority/function and
   source job independently of candidate lookups; retain all appended evidence.
   Before activation, a sealed source-upgrade intent provides its own recovery
   path. Reject stale intents. Replay recovery, reinstall from a fresh intent,
   reactivate and recertify. The prior 52-path service still has v9 routes after
   recovery; certify restored authority, not fictitious 404s for existing routes.
8. Observe a genuine Scheduler-created four-task run, restore the normal daily
   timing, certify all 19 private core reads, private denial and unchanged protected
   runtime. Fresh after-backup and strict original/prior-layer comparison. Record
   SHA, images, PR/CI, executions, observed coverage, exceptions and acceptance seal.

Cloud Logging delivery can lag execution completion. Bounded polling re-reads
only the exact immutable execution and repeats all original metadata/credential/
network/image checks. Missing evidence alone is retryable; malformed/duplicate
certificates, drift and API failures fail immediately. No writer/deployment/job
execution is retried to manufacture evidence.

FS-10 Kotak and FS-11 UTI follow as separate reviewed and certified deliveries.

## Pre-merge evidence, not DEV acceptance

A fresh host-backed PG18 clone restored the exact accepted FS-08 after-backup
(`72c85a9b9c23b0a19163f7a65eb3f89d6345e260658d69fac5dfee12818901be`)
under the measured original owner/CREATE boundary, with restricted runtime roles.
It passed strict original preservation before the source-code rehearsal.

The retained real four PDFs, raw catalogue/directory/index originals and issuer
XMLs then exercised the actual parser, restricted writer, owner upgrade,
canonical/raw field references, activation, prior-source/graph recovery, recovery
replay and reactivation. Full original preservation, prior immutable rows,
whole prior-AMC projection and zero human events passed. Source-code rehearsal
seal: `c7bd1a2e12a1c4c7869fa451e51bf85fdef0d9357cddc667af93b409964994bf`.

The observed local denominator was 258: September 161 usable / 97 gaps, August
148 usable / 110 gaps. Counts are not semantic completeness. Local retained-corpus
misses do not prove origin absence; final real acquisitions may add evidence and
must be certified afresh. Archives used explicitly labelled **local contract
fixtures**, not real GCS generations. This is not a deployed-image, live HTTP,
Scheduler or DEV readiness certificate.

An earlier local publication stopped safely on byte-identical XML served at two
AMFI URLs. No partial SBI original rows committed. Its prior source authority was
restored using the sealed intent even after candidate-specification drift; all
failed evidence and the clone remain retained. A new untouched restored clone,
not a reset or overwritten database, produced the successful proof above.
