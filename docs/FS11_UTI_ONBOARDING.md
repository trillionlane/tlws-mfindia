# FS-11: UTI English monthly source coverage

## Publication boundary

Add UTI to the existing private, versioned book process, after acceptance of
ABSL, SBI and Kotak. There are six independent bounded source tasks: the two
retained pilots and the four expanded AMC sources. No raw fund, NAV, holding,
analytics, review or original-source record is edited or deleted. All prior
registries, parser bytes and executable database branches remain frozen.

This branch inherits the reviewed #91 Kotak known-document authority, including
its explicit unavailable-discovery provenance and expiry before November. The
entire Kotak/SBI/ABSL executable tail in migration 041 is byte-identical to the
reviewed migration 040; UTI does not relax or replace that authority. The
separate [failed-attempt recovery record](evidence/FS10_CATALOGUE_DISCOVERY_RECOVERY_2026-10-17.md)
remains retained. This implementation is not permission to push or roll out UTI
before actual Kotak DEV acceptance.

Current deterministic pins after that inheritance:

- Source-v3 configuration: `addd97f4c2acbf7a66b72ca2c0ffa61b7bae5242f69c36c8b8ed7f2bbb3eec7f`.
- Source-v4 configuration: `6fb6c0c804a1dac20e4e2c4aa25f3cd107ce7c58517164409ae2da52e97869d4`.
- Policy-v7 hash: `38be4edfda0fedfe2156c203b685e06b7e15589bf1c88b752d5e095b5c9786b7`.
- Migration 041: `ef9a081ed0e46df618aa8b66a8c412387662761e0b7cd91c62e16a26ef07c702`.

Use the production UTI catalogue and its exact **English Active and Passive**
PDF rows for each of two editions. Hindi is never fetched as a fallback or
translated. Retain all four original PDFs and returned AMFI identity XMLs in
create-only, generation/SHA-verified storage; retain catalogue/index originals
byte-for-byte in the append-only evidence tables.

Create-only PDF archival precedes decoding. A later parser, identity-discovery
or deadline failure retains already archived books in the failed run's evidence
without publishing observations. Archive failures are not retried ambiguously;
the later XML/originals phase skips PDF hashes already retained. Permissions,
publication atomicity and task/time bounds are unchanged.

The actual `Portfolio as on` date must agree across both books and equal each
expected last-closed month. Edition, URL, filename and PDF metadata dates never
stamp an observation. A missing source, unsupported identity or ambiguous
physical layout remains an explicit gap. No blanket all-AMC completeness or
whole-factsheet semantic completeness claim is permitted.

## Deterministic facts

Exact AMFI code, checksum-valid ISIN, Regular plan, option and complete
contradictory code-claim checks precede field publication. Discovery names only
select candidate identities; they do not establish a mapping. A source-authored
`Erstwhile` heading can identify an old name; database similarity cannot.

The new, separately isolated decoder keeps physical text coordinates. Its
explicit portfolio-column anchor and two-font-em gutter prevent the holdings
column from becoming a benchmark or manager. Ambiguous pages fail closed.

Field policies distinguish closing AUM from average AUM, require matching
explicit AUM dates, preserve complete manager lists and benchmark names, and
accept risk only from a unique same-fund cover with a matching explicit date.
Missing or conflicting newer fields never erase an older usable value.

UTI's month-end **TER is not BER**. Preserve it as
`observations[].result.reported_total_expense_ratio` in the private
`GET /api/enrichment/v9/funds/{code}/facts` response, with its percent unit,
Regular-plan scope, disclosure date, actual raw token/anchor and `expense_basis:
TER`. It is not projected onto `base_expense_ratio`, and no BER-to-TER conversion
is performed. No UTI-sourced BER override is published; any preserved legacy
BER is not newly validated by this sprint. Consumers must use source/provenance
metadata rather than interpreting TER as evidence for that legacy field. Coverage
counts this supplemental field separately from the five existing scalar fields.

Every observation remains `needs_verification`; source validation creates no
human review decision. Adjusted IDCW analytics remain disabled.

## Read-only breadth preview, not acceptance

On 2026-10-17 an ordinary GCP DEV network diagnostic attempted all 81 candidate
families, retaining 128 actual index/XML responses and 17 availability gaps.
It used the already-merged public AMFI transport without database credentials,
source publication, network retries or access-control bypass. The same larger
discovery attempted locally hit its unchanged 520-second bound; that failed
local attempt is retained, not counted as accepted evidence.

Compiling those actual GCP identity responses against the four retained English
books and the complete read-only local cohort produced, for **each** of the
September and August 2026 reporting periods:

- 208 retained variants; 80 usable observations and 128 explicit gaps.
- 78 closing-AUM values, 80 benchmarks, 80 manager blocks, and 78 separately
  labelled Regular TER observations. September has four explicit same-fund risk
  observations; the older book does not supply those matching cover dates.
- Gaps: 48 missing exact bridges, 40 variant-identity conflicts, two family
  conflicts, 15 missing/ambiguous monthly headings and 23 unsupported current
  identities. No matching or source-correction rule was weakened to clear them.

All independent current/older-book and TER/basis references passed. This preview
combines retained books/directory with actual GCP identity acquisition; it does
**not** refetch the whole source process under the final image, create verified
archive generations, exercise live publication, or establish DEV acceptance.
The full sequential gates below remain mandatory. `Usable` is not all-fields
complete, human-verified, economically complete, or permission for IDCW analytics.

## Sequential DEV acceptance

1. Review, full exact-head CI and normal merge, then verify both immutable
   final images. Generic auto-deployment remains disabled.
2. Fresh regional backup and restored-clone full preservation rehearsal using
   the final image, actual public originals and private create-only archives.
3. Confirm prior accepted source authority and all original/prior-layer row
   seals. Add only UTI's archive prefix to the existing restricted identity.
   Install schema 041/source registry 4 without activating pending facts.
4. Execute the six-task source job; require all exact-execution receipts and
   original archive verification. Certify both actual reporting months, Active
   and Passive originals, current Growth and IDCW samples, TER-as-TER, and no
   invented BER. Retain an independent prior-SBI BER oracle to satisfy the
   existing all-five-field certificate without weakening it for UTI.
   The version-4 certificate additionally requires independent supplemental TER
   references for every actual book/period and current Growth/IDCW samples.
   Matching API/database output without these references cannot pass.
5. Activate the existing projection, certify metadata and actual core reads,
   then perform actual prior graph/source/traffic recovery, replay and
   reactivation. No timeout relaxation or unapproved source correction.
6. Witness a genuinely timer-created six-task run; always restore the existing
   daily 09:00 Asia/Kolkata source schedule. Re-certify all 19 private core
   routes, preserve every original/prior-layer row and old AMC projection,
   verify zero human events and private ingress/IAM, and take the after-backup.

These gates, including actual-source coverage, must pass before this sprint is
called accepted. Tests and locally retained source previews are not deployment
or live certification evidence. UI and production are outside this sprint.
