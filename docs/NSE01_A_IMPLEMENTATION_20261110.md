# NSE01-A — offline SCH import and existing dataset API

Implementation basis: reviewed contract `357c13e6c432aa890f5a96f5eff2ef829fcd56e0`;
current remote base `db07e53a509a39556214bec80cca084086c37b2a`. One focused PR,
no merge/live activation authority. HDFC discovery and the daily schedule remain
separate. The three reviewed contract drafts and other-family examples are
preserved byte-for-byte; this record describes the actual first slice.

## Composition and FULL gate

`nse.contract` strictly validates the separate manifest. The known-complete
synthetic seven-row fixture is bound to `sch43_full.manifest.json` by expected
SHA-256, byte length and data-row count (excluding the exact header). FULL also
requires the explicit synthetic completeness basis. A clean EOF after a valid
row is insufficient. Both expected hash/length and expected count are checked
before any database connection or create-only operation record. The regression
also updates the short file's hash/length while retaining the expected count,
to prove count is an independent gate. Native/API coverage checks that a clean
truncation cannot advance the head, remove schemes or change existing responses.

This is a **fixture-certified**, not production-certified, profile. An original
export must be retained privately and its intended complete scope established
independently; recomputing a manifest from an arbitrary download is not proof of
FULL. No original export, NSE login, credentials or download is introduced here.

`nse.parser` accepts only `SCH-DOCUMENT-43-FIXTURE-v1`: exact header, UTF-8 with
optional one BOM, LF/CRLF, no extra terminal separator, preserved final empty
cell, declared character widths, strict exact decimals and dates. The distinct
44-column header is known but returns `SCHEMA_UNVERIFIED` because a complete
reviewed value profile was not supplied. It is never treated as43. Non-SCH
families reject before connection/claim as `FAMILY_NOT_IMPLEMENTED`.

All43 cells remain private and lossless. The14 reviewed ambiguity policies are
implemented with block > row > field precedence. Unknown plans, unresolved,
ambiguous and conflicting mappings, Direct scope and disabled/unknown source
activity withhold rows; private evidence retains every row and reason. Field14,
BLANK19/43, timezone, settlement-domain and source-metadata interpretations are
not invented. Valid field uncertainty retains the mapped row with null fields
and limitations. Malformed structure/type, nonblank document-BLANK fields,
contradictory bounds and conflicting duplicates reject the snapshot. No positive
transaction eligibility is claimed: the dataset always labels it UNKNOWN.

`nse.mapping` requires an explicit versioned AMC crosswalk and scheme-code target,
exact source ISIN and slot, plan, option, source-name corroboration and dividend
reinvestment evidence, plus the actual canonical AMFI row. Names/ISIN alone,
source serial and family grouping cannot resolve identity. Missing or multiple
candidates stay private. A crosswalk is offline reviewed input, not a claim that
synthetic normalizations certify current production tokens.

## Storage and transaction

The additive **optional** `045_nse_sch_offline.sql` is installed only through the
explicit `nse.store.install(owner_connection)` hook inside the owner's existing
transaction. No core migration or runtime factory installs it. Owner installation
enrolls only the physical sanitized `mf_nse.current_sch` through the existing
`datasets.enrollment` seam and verifies the result; re-entry is idempotent.
Private tables are not enrolled and have no reader grants. No global default
privileges, membership, ownership, privileged function, writer elevation or
canonical/NAV write permission is added by this installer. The existing ingest
identity receives only the listed narrow NSE writes plus read access to the
canonical mapping/preservation inputs. No owner impersonation in the importer.

The importer runs as direct `mfdata_ingest` in `mfdataindia`. It verifies the
installed version/SQL seal and pins canonical inputs with REPEATABLE READ.
ACCESS SHARE protects definitions without expanding canonical write privileges
or blocking ordinary ingestion. The SCH head row serializes publishers; a stale
expected head fails. Mapping input digest and crosswalk are retained. This is a
versioned canonical snapshot, not a promise that identities never change later.

Artifacts, original rows, mappings, snapshot, publication record, observation,
read-model replacement and head advance commit together, once. Existing AMFI
funds/AMCs/NAV are read/fingerprinted before and after inside that transaction and
never written. Bounds:5-second statement/lock acquisition and15-second transaction;
no new timing expansion. The fixture reports measured vertical duration without
claiming a production throughput SLA. Full-scale measurement remains a later
activation prerequisite.

Identical work creates only a new observation; reference/filename changes cannot
force a duplicate business publication. A same-date byte revision keeps both
snapshots. Changed mapping inputs/crosswalk create a distinct version. Older dates
are retained as HISTORICAL_ONLY and cannot replace the active snapshot. Identical
bytes with a changed business date reject; retrieval is not source freshness.
Exact duplicate evidence rows are retained and normalized once, with explicit
source/exposed/withheld/duplicate counts and per-reason field/row accounting.

## Offline command and recovery

Use `scripts/import_nse_sch_offline.py --dry-run --file <local-file>
--manifest <separate-manifest> --crosswalk <reviewed-crosswalk>` for validation.
Dry-run opens no connection, creates no operation intent, and labels database
mapping/installation and production completeness UNVERIFIED. No HTTP input or
credential-service lookup exists.

A fixture import uses `--import-file`, a new UUID `--operation`, an existing
private `--journal-dir` and optional `--expected-head` UUID, with the existing
`MFDATAINDIA_DSN` environment reference. The CLI snapshots `dict(os.environ)`.
It durably creates/fsyncs one local operation intent before connection/submission;
any existing, partial or failed record remains spent. Never delete/reset it.
Connection failure and known local validation are distinct from publication
uncertainty. Before COMMIT, confirmed rollback is reported. COMMIT response loss
remains COMMIT_UNKNOWN; known commit followed by readback failure remains
COMMITTED_UNVERIFIED. Cleanup/output failure never causes mutation retry.

`--inspect --operation <original-uuid>` uses only a read-only transaction to
inspect the original durable observation. Absence is UNKNOWN, not permission to
replay. It neither resumes an operation nor resets journal/control state, grants,
reconnects-and-writes or reverses data. A later fresh operation requires resolving
prior uncertain effects; lost memory or durable history cannot authorize it.

## API, authentication and evidence levels

No new API routes. Existing `/api/datasets/v1`, metadata and bounded rows expose
only the enrolled current reference table, with existing page<=100, cursor,
response-size, direct-reader, dependency and mutation checks unchanged. Control
records, raw source vectors, mapping candidates and crosswalks are private.
Existing Cloud Run OIDC/invoker/private-ingress remains the authentication
boundary. Hosted ASGI tests establish database/route authorization, **not** actual
Cloud Run authentication or TI/TLAM/consumer acceptance.

The existing both-image dataset smoke additionally runs the actual offline
main/parser, valid fixture, clean truncation and non-SCH rejection without DB or
network. No workflow/protection/step inventory change or copied controller family.
The installer SQL is checked at the actual non-editable APP/TOOLS package path.

## Acceptance inventory and actual results

`NSE01_A_ACCEPTANCE_IMPLEMENTATION_20261110.json` maps all26 reviewed A gates and
the added FULL-clean-truncation regression to concrete tests. Later eight gates
stay deferred, including SIP/Step-Up, STP, SWP, NAV, SET, vectors, cross-family
failure isolation and live source/authentication acceptance. Source43/44 remain
distinct, and no runtime support is advertised for other families.

Provider-free focused checks currently pass; complete final-head hosted native,
API, image, contracts, audit, credentials and release-admission results will be
recorded in the PR and retained evidence. No local native execution is claimed.
No deployment, live installation, publication, scheduler or TLAM change occurs.
