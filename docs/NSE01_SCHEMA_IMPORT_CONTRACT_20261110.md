# NSE-01 — schema and import contract (draft for review)

Status: proposed, not implemented, provider-verified or activated. Base:
`db07e53a509a39556214bec80cca084086c37b2a` (PR148). Scope is MFData reference
imports. This draft authorizes no NSE call, credential access, migration,
publication, role change, deployment, schedule or transaction/order operation.
HDFC issuer discovery is a separate issue; the existing daily AMC schedule stays
unchanged. Nothing here makes an NSE scheduler active.

## Authority and complete accounting

API Details v1.9.8 (August 2026), PDF p5 and p163, governs the request, common
authentication and response envelope. Web File Structure (December 2025), PDF
pp77–83, governs the documented positional layouts. Document hashes, exact
names/types/widths/remarks and every field disposition are in
[NSE01_FIELD_MAPPING_20261110.json](NSE01_FIELD_MAPPING_20261110.json).
These are 132 documented positions: SCH43, SIP27, STP26, SWP21, NAV8, SET7.
The observed SCH44 and SIP28/Step-Up layouts are separate schema versions,
not extra columns silently inserted into these 132 positions. Existing retained
UAT evidence cannot certify a production response or select a production parser.

NSE is authoritative for its reference identifiers and published facility
constraints. AMFI remains the authority for the existing MFData fund identity
and NAV series. NSE NAV is a separate source; it cannot overwrite AMFI NAV.
AMC facts, financial formulas, ingestion and existing source records are unchanged.
A reference flag is neither investor authorization nor proof of accepted orders,
registration, funding or execution price. No order endpoint belongs in this importer.

Documented reference request: `POST /nsemfdesk/api/v2/reports/MASTER_DOWNLOAD`
with the sole JSON member `file_type` equal to SCH, SIP, STP, SWP, NAV or SET.
The recorded production host is `www.nseinvest.com`; availability and the exact
production authentication/egress remain unverified. Do not invent historical-date
parameters, a seventh Step-Up request code, cookies or redirect-based fallback.
HTTP success must be distinguished from the documented JSON failure envelope
(`response_status: F`, including `NoRecordFound`) and from a valid text file.
The authentication headers/secret derivation remain private connector concerns,
never fields in source rows, fixtures, API responses or log/error messages.

## Family contracts

All original source rows use `(snapshot_id, row_ordinal)` as the evidence key. Candidate
business keys below are proposed ambiguity/duplicate checks, not undocumented
provider uniqueness guarantees. Store all original rows even if identical;
conflicting rows at a candidate key prevent active publication. A normalization
may expand one explicit combined mode into multiple derived rows, each retaining
the source-row identity; it must not lose the original mode token.

| Family / authoritative positions | Candidate identity and joins | Validation / deliberately unmapped meaning |
| --- | --- | --- |
| SCH / Web pp77–79, API p163 | Environment + AMC code + NSE scheme code + ISIN + source plan + disclosed applicable mode. Join to MFData only through an explicit AMC crosswalk and exact ISIN slot/plan/option evidence. Unique serial number is source evidence, not an AMFI code. | Preserve all43 positions. Purchase/redemption limits, flags, dates and cutoffs require a schema-specific interpretation. Position14 is `MAXIMUM PURCHASE AMOUNT` in the document but `ADDITIONAL PURCHASE MAX AMOUNT` in SCH44: do not apply it as either facility limit until resolved. Document positions19 and43 are marked BLANK; do not invent redemption quantity/reopening rules. AMC_IND, channel partner code and source-only identifiers remain separately labelled source metadata. |
| SIP / Web pp79–81, API p163 | Environment + AMC code + scheme code + ISIN, then frequency and mode. Join the exact SCH snapshot; preserve multiple rules/modes. No name-only or ISIN-only fallback. | Source fields1–18 supply identity/recurrence facts; fields19–22 (pause) have no types/widths in this layout and remain raw/semantics-unverified. Fields23–27 are reserved fillers. A missing Step-Up field means UNKNOWN, not unsupported or supported. CHAR1 versus observed DP is an explicit schema discrepancy. |
| STP / Web pp81–82, API p163 | Environment + AMC code + NSE scheme code + ISIN + mode + frequency, with distinct in/out applicability. Join exact SCH; source/destination transaction pairing needs a separate reviewed rule. | Preserve the literal ASTP field names. Keep IN/OUT flags, amount/unit limits and multipliers separate. Do not infer that every same-AMC destination is allowed. Schedule/gap units and allowed-token sets are incomplete in this positional document. |
| SWP / Web pp82–83, API p163 | Environment + AMC code + NSE scheme code + ISIN + mode + frequency; exact SCH mapping. | Amount fields8–9 are12,2; unit fields11–12 are12,3. Do not round units to currency scale. Preserve multipliers, counts and schedule values; unknown flags/modes/frequency grammar cannot confer eligibility. |
| NAV / Web p83, API p163 | Source NAV date + NSE scheme code + ISIN + RTA identifiers. The file has no AMC code: resolve against SCH only when code/ISIN/RTA evidence yields one unambiguous mapping. | Exactly8 headerless positions. YYYY-MM-DD is a date-only value. Preserve exact numeric text/decimal value; NUMBER14 does not specify a scale. Reinvestment flag Z includes Growth/Bonus/others, so Z alone cannot identify Growth. Keep this source independent of AMFI NAV and transaction prices. |
| SET / Web p77, API p163 | Environment + settlement type + settlement number + trading date; preserve pay-in/pay-out dates and immutable source version. No canonical fund FK. | Exactly7 positions. Keep settlement numbers as strings with leading zeros. SET type is described as MF, while SCH settlement codes are a different unresolved domain: do not join the fields by equal name. No inference of complete market holidays, eligible trading days or order settlement selection. |

### Exact MFData identity seam

`mf.funds.amfi_scheme_code` identifies the existing plan/option record.
`mf.funds.isin_growth_or_div_payout` and `isin_div_reinvest` preserve different
facility slots. The proposed mapping target is therefore `(amfi_scheme_code,
matched_isin_slot)`, retaining exact ISIN, plan and source option evidence.
`mf.fund_variants.group_key` and `mf.fund_family.tlws_mf_id` are family/grouping
information, not replacements for that exact target. Do not invent a canonical
UUID column, manufacture an AMFI code, or merge payout/reinvestment/Direct/Regular.

An AMC crosswalk is explicit/versioned; a display-name comparison is a suggested
review candidate only. One canonical variant can have multiple NSE facilities.
Multiple canonical candidates, disagreement between plan/ISIN/AMC, or missing
option evidence produce AMBIGUOUS/CONFLICT/UNMAPPED, never an arbitrary winner.
Store out-of-current-scope Direct and other source variants as reference evidence;
do not expand the current Regular-plan consumer scope through this importer.

The document's `D => Direct; else Regular` remark is retained verbatim. Production
normalization still requires a schema-specific token profile: an unrecognized
new token cannot silently become Regular. Synthetic fixtures can exercise chosen
normalizations but cannot establish which tokens production currently emits.

## SCH ambiguity effects for NSE01-A

The acceptance matrix contains the machine-readable policy and escalation for
all entries below. BLOCK_SNAPSHOT takes precedence over row withholding, then
field withholding. Structural/type errors do not become silently tolerated
unknown values. Semantic uncertainty alone does not reject unrelated valid data.

| Ambiguity | Primary effect | Boundaries |
| --- | --- | --- |
| Unknown43/44 layout, header/count/encoding/delimiter or malformed declared value | **BLOCK_SNAPSHOT** | Retain prior head/read model; no guessed schema repair. Incomplete44 value-profile evidence means reject that version explicitly; it is not a prerequisite for document43 offline acceptance. |
| Field14 maximum purchase versus additional purchase maximum | **WITHHOLD_FIELD** | Keep identity row; neither ambiguous maximum is asserted. Affected purchase constraint verdict stays UNKNOWN. |
| Document43 blank quantity field19 / reopening field43 | **WITHHOLD_FIELD** | Valid blank creates no rule. Nonblank contrary to selected profile escalates to **BLOCK_SNAPSHOT**; do not implicitly switch to44. |
| Unknown plan token or missing/conflicting exact AMC/ISIN/plan/option mapping | **WITHHOLD_ROW** | Retain original row/reason; omit from current_sch, accept independently resolved rows. Structural invalidity still blocks the snapshot. |
| Known Direct or other out-of-scope variant | **WITHHOLD_ROW** | Keep source evidence; current Regular consumer scope is unchanged. |
| Unsupported valid flag/mode or numeric-rule semantics | **WITHHOLD_FIELD** | Keep other verified fields; dependent facility verdict unavailable. Invalid type/overflow/known contradictory bounds blocks the snapshot; option identity conflict withholds the row. |
| Unspecified cutoff timezone | **WITHHOLD_FIELD** | No derived UTC instant/timing eligibility. Invalid declared time format blocks the snapshot. |
| SCH settlement code versus SET type domain | **WITHHOLD_FIELD** | No derived SET join; retain labelled source text. |
| Source-only serial/RTA/AMC IDs/taxonomy/AMC_IND/channel metadata | **WITHHOLD_FIELD** | No inferred canonical key or unclassified consumer exposure. |
| Unspecified source requiredness | **WITHHOLD_FIELD** | Optional blanks are valid. Missing identity facts withhold a row; malformed/missing cells block the snapshot. |
| Conflicting duplicate candidate key | **BLOCK_SNAPSHOT** | No arbitrary winning row; identical duplicates retain evidence and normalize deterministically. |

A structurally complete snapshot can be PUBLISHED_WITH_LIMITATIONS when rows or
fields are withheld. Report source/exposed/withheld row counts, withheld fields
and allowlisted reasons. Withheld rows cannot disappear from accounting or be
labelled current/eligible. A field hold preserves the row but nulls only the
unverified normalized/exposure field and dependent verdict. This never changes
canonical funds, AMC values or previous immutable snapshots.

## Proposed storage schema

Use a new `mf_nse` namespace; proposed names are not existing installed objects.
Do not expand `mf.source_snapshot_runs.source='AMFI'` or edit old migration bytes.
Select an additive migration number only when implementation begins.

| Object | Key / payload | Boundary |
| --- | --- | --- |
| `source_artifacts` | artifact UUID; environment/family; byte SHA256; byte count; filename/header hashes; source date; retrieved_at; schema/parser versions; credential-free retained-object reference | Private immutable evidence; no raw authenticated URLs/headers or credentials; not enrolled for consumers. |
| `observations` | observation UUID; artifact FK; original filename/source-file date; retrieved_at; response class; unchanged/revision disposition | Private append-only observation, including unchanged polls; never invent a newer business date from retrieval time. |
| `import_attempts` | operation UUID; unique publication work key; state, counts, allowlisted reason and strongest known commit effect | Private control. Bounded one-writer operation; append observations rather than reset uncertain history. |
| `snapshots` | snapshot UUID; artifact FK; FULL/UNKNOWN completeness; row/mapping/rejection counts; exact and semantic digests; validation version | Immutable validated source snapshot metadata. Source date is distinct from retrieval/import/publication time. |
| `sch_rows`, `sip_rows`, `stp_rows`, `swp_rows`, `nav_rows`, `set_rows` | snapshot UUID + row ordinal; exact original cell vector; all mapped source columns from companion mapping; explicit normalization state | Immutable history. Original provider serial/code strings are not integer identities. Unknown semantics remain represented and labelled. |
| `variant_mappings` | mapping UUID + mapping version + source row + AMFI code/ISIN slot + AMC crosswalk version + mapping disposition | One-to-many permitted. No canonical identity writes. Conflicting candidates retained, not discarded. |
| `family_heads` | environment + family; accepted snapshot UUID; comparison predecessor/version | Private compare-and-swap control, not a public permission or new credential authority. |
| `current_sch` (later other `current_*`) | physical read-model row UUID; accepted snapshot UUID/source date; safe identity/reference fields + readiness/reason | Updated atomically with the family head; only deliberately enrolled sanitized reference output. Avoid exposing control tables or adding a generic bypass around dependency checks. |

Use PostgreSQL built-in types and physical UUID/integer keys compatible with
existing dataset pagination. IDs are application keys, not new provider fields.
Codes/serials/ISIN slots stay text; exact amounts/units are numeric + original
lexeme. Only explicitly declared p,s values become numeric(p,s); unspecified
precision/scale is not filled from a neighbouring family. Unknown pause fields,
filler contents and conflicting source semantics do not enter typed eligibility
columns. Consumer projections exclude fillers, transport evidence, credentials,
control records and fields still awaiting exposure/classification review.

At supported installation, reuse `datasets.enrollment.enroll/reconcile` on the
trusted direct creator's existing transaction, and existing private `datasets.app` discovery/metadata/rows
routes. No consumer-supplied SQL/profile/role selection. Owner remains
`mfdata_admin`; separately reviewed importer privileges are narrow object writes
for the existing supported ingestion identity, not owner/elevated reader rights.
The normal importer does not become the owner or run enrollment/grants. Owner
installation and its reader reconciliation are separate from routine ingestion.
The reader gets only schema USAGE/SELECT on approved reference outputs. Never
grant CREATE, mutation, role membership, grant option or new function EXECUTE.

Dataset discovery is not a domain eligibility endpoint. This first slice needs no
new route family. Later list/detail/rules/batch capabilities should extend the
existing authenticated API after route-contract review. Keep page<=100 and the
existing2MiB response/cursor/binary limits, authentication and request-time reader
checks. Cloud Run OIDC/invoker/private ingress is the existing authentication
boundary, not a new token trusted by these ASGI routes; no custom auth header or
mocked ingress may be described as real authenticated acceptance. A reader reconciliation failure is recorded as not consumer-ready, not
allowed to break unrelated AMC ingestion or be misreported as successful adoption.
No view exposing private controls is grandfathered by this draft.

## File validation and import protocol

1. **Validate local candidate before effects.** Fixed reference-family allowlist,
   environment, schema ID, original-file hash/length, bounded input, parser version
   and unique operation ID. No fetch or database connection is needed for preflight.
   Proposed importer budgets for review: <=64MiB/file, <=1,000,000 rows,
   <=16KiB encoded row, <=256KiB diagnostic output; one file per operation.
   These are proposed local-import limits, not inherited runtime approvals or
   claims that an issuer publishes files within them. Measure the disposable
   representative import before choosing transaction timing; retain existing
   applicable limits rather than invent/increase them in a design.
2. **Select an explicit layout.** UTF-8 decoding, optional single BOM and LF/CRLF
   handling must be named in the fixture-bound transport profile. Original bytes
   never change. Pipe positions/empty cells are preserved. A terminal separator
   is accepted only by a version that explicitly declares it; never strip arbitrary
   trailing blanks (especially SIP fillers). No guessed quoting/escape grammar.
   Headered layouts require the exact declared sequence/aliases. NAV starts with
   data, not an invented header. Unknown header/count/encoding rejects the file.
3. **Validate rows without promoting meaning.** Types/width/precision, real date
   calendars, exact decimal grammar, flag/mode/frequency token profiles, bounds
   min<=max and positive applicable multiples/counts. Blank/null and0 are distinct.
   Reject nonfinite numbers, truncation, nonblank reserved fields, duplicate keys
   with conflicting payloads and contradictory identities. The positional layout
   does not mark every field mandatory: required-for-mapping and
   required-for-facility rules are separately versioned, never fabricated source M/O.
   Missing semantics produce unavailable rules; zero/blank is not permission.
4. **Stage complete evidence.** A200 JSON failure, HTML, empty/header-only or
   truncated file is not an empty business universe. Filename DDMMYYYY is parsed
   strictly and stored as source-file date, not proof of row effective date or
   freshness. Preserve every accepted original field and record every rejected or
   unmapped row. A malformed schema/row or unknown completeness blocks active
   replacement; valid unmapped rows can remain in a complete snapshot with their
   mapping status visible and no facility eligibility. No partial file may retire
   missing schemes.
5. **Claim once, then publish atomically.** A parse-cache key includes environment,
   family, original byte hash, schema/parser and normalization-policy versions.
   The separate publication work key also binds the AMC crosswalk version,
   canonical mapping-input digest and target identity. A changed mapping input
   can require re-mapping without another fetch. Duplicate identical publication
   work skips parsing/publication; append a new observation. A new byte hash for the same
   source date is a revision candidate, not silently UNCHANGED. Keep the artifact
   separately, then transactionally bind validated rows/mappings, compare the
   expected previous head, publish the sanitized read model and advance the head
   in one COMMIT. Data-sensitive equality/capability/head checks remain inside the
   transaction. No partial deletion/current-table exposure outside it.
6. **Re-entry and uncertainty.** Deterministic parsed-row digest includes preserved
   unknown fields; distinguish byte-only differences, schema drift, business
   changes and mappings changed by a new crosswalk/parser or canonical input. Do not overwrite history.
   A proven pre-publisher abort can be terminal and allow a distinct attempt;
   uncertain submission/COMMIT requires inspect-only reconciliation. Read the
   original operation's durable row/head; never reset intent, assume rollback,
   replay a COMMIT or open a new write to resolve ambiguity.
7. **Version/freshness isolation.** Families import independently; one failure
   cannot block other families or AMC work. Consumers requiring multiple families
   pin an explicit snapshot vector and compatibility policy. Import time or
   simultaneous retrieval does not establish source-period compatibility. Required
   stale/missing/incompatible families yield explicit unavailable/version-conflict
   reasons. Keep the last accepted evidence, but do not present its stale flags as
   current positive eligibility. A new filename/retrieval observation with identical
   bytes cannot silently advance business dates. An older source date does not
   automatically replace a newer active head; any historical import stays nonactive. Removals/disabled variants in an accepted FULL
   SCH replace current applicability atomically and retain historical rows.

Stable states separate FETCH_FAILED, INVALID_FILE, SCHEMA_UNVERIFIED,
ROW_VALIDATION_FAILED, VALIDATED_NOT_PUBLISHED, PUBLICATION_ROLLED_BACK,
COMMIT_UNKNOWN, COMMITTED_UNVERIFIED, PUBLISHED, UNCHANGED and
READER_RECONCILIATION_FAILED. Mapping and facility status are separate axes.
No status certifies actual consumer/order acceptance. Reasons are allowlisted,
value-free, with family/schema/operation/snapshot hashes and counts; no provider
body, raw SQL, authentication header or secret appears in errors.

## Fixture acceptance and evidence levels

[NSE01_FIXTURE_ACCEPTANCE_20261110.json](NSE01_FIXTURE_ACCEPTANCE_20261110.json)
is the explicit acceptance matrix. The six checked-in synthetic PSV examples
illustrate the documented cell counts, not real source success, production
header/token acceptance or runtime authorization. SCH44/SIP28 require distinct
sanitized retained captures plus provenance before production support is claimed.
Do not copy raw UAT bodies, tokens, member identifiers or continuation URLs.
Sanitization records source hash privately, transform version, retained layout
facts and sanitized-file hash; use synthetic AMC/fund/ISIN identities consistently.

Required layers for implementation acceptance:

- Provider-free: exact request allowlist, six layouts, headers/fillers, Decimal
  and date boundaries, contradictory flags/keys, incomplete/error payloads,
  schema variants, exact mapping/no name fallback, semantic digest/idempotency.
- Disposable PostgreSQL using repository-supported native CI: additive install,
  retained eight-field NAV precision, first import/re-entry/revision/removal,
  concurrent same work, failed preservation/COMMIT rollback, truthful ambiguous
  outcome plus inspect-only reconciliation; complete AMC/canonical/NAV preservation.
- Existing private API fixture: selected sanctioned reference dataset
  discoverable/paginated/readable, missing grants and unsafe database identities
  fail, writes/DDL denied; credential/control fields absent. In-process ASGI tests
  prove database authorization, not Cloud Run authentication. Retain the existing
  Google OIDC/invoker/private-ingress release checks; real unsigned/wrong-invoker
  denial is a separate authorized service-boundary test, not a fake ASGI401.
- Packaged entrypoint: actual main with os.environ mapping, strict payload
  validation, no-provider fixture import, missing parser/unknown family rejects;
  source/image binding regressions and all required final-head checks.
- Separately authorized later PROD reference read: actual environment/auth,
  immutable complete originals, schema/header/token verification, supported fetch,
  publication and authenticated API readback. Fixture success never substitutes
  for this evidence. No new schedule or production-support claim here.

## Proposed first implementation slice — NSE01-A

**One focused code PR:** offline SCH snapshot import and exact mapping foundation,
with a fixture-backed accepted reference dataset. Implement the additive tables
needed for artifacts/observations/attempts/snapshots/SCH rows/mappings/head/current_sch; a pure
schema-versioned parser and import service; a local retained-file entrypoint;
existing enrollment/discovery integration; and only cases with `gates_NSE01_A=true`. The matrix explicitly separates
26 SCH-only/foundation gates from8 later-family/live-activation cases. Static
six-family inventory accounting is documentation, not six executable importers.
Reject known SIP/STP/SWP/NAV/SET at the local execution boundary as
FAMILY_NOT_IMPLEMENTED before connection/claim; reject unknown families as invalid.
Keep six-family registry definitions so unknown families/layouts reject explicitly,
but do not claim executable SIP/STP/SWP/NAV/SET import until their subsequent
fixture-backed slices. A43-column document layout and44-column observed header
layout remain distinct. If captured-value profiles are insufficient, publish only
synthetic-fixture evidence and source-readiness diagnostics, not a live-ready
Regular/Direct/facility verdict. Do not invent tokens to unblock activation.

The vertical acceptance is: sanitized/original-hash-bound SCH input -> validated
snapshot -> exact canonical/ISIN-slot mappings (including explicit ambiguous,
unmapped and out-of-scope dispositions; unresolved rows are retained privately
and withheld from current_sch) -> atomic read-model/head -> authenticated
fixture API. Prove duplicate no-op, a same-date revision, disabled/removed scheme
handling and failure/unknown-commit preservation. No NSE connector, credential
migration, collector takeover, live DB installation, schedule or TLAM source change.

Follow with SIP/Step-Up and systematic-rule imports only after respective source
contracts/fixtures are reviewed; NAV/SET stay separate source domains. These are
subsequent implementation slices, not a claim that NSE01-A completes all NSE-01.

The decimal test gates A for SCH only; SIP/STP/SWP/NAV extensions are later.
SIP/Step-Up, STP, SWP, NAV, SET, cross-family snapshot vectors and later live
reference checks do not gate offline A. No NSE HTTP client, credentials, live
Cloud Run execution or actual service authentication test is introduced by A.
Native/API cases exercise SCH storage/mapping/read authorization; existing
authentication release controls remain intact.

## Review decisions / unresolved source facts

1. Accept proposed namespace/storage, exact AMFI-code+ISIN-slot mapping and
   explicit AMC crosswalk; retain all documented fields without silent loss.
2. Confirm SCH43/44 field14 meaning, flag/plan token profiles, document-BLANK
   fields, encoding/delimiter/width semantics and production fixture provenance.
3. Confirm SIP pause fields,27/28 schema and DP width; Step-Up operation/layout
   remains unresolved (no seventh request invented). Confirm systematic token,
   date-list/gap units and STP destination applicability before facility claims.
4. Confirm NAV decimal scale/ambiguous mapping and SET type-domain crosswalk;
   neither is a replacement for AMFI or a holiday/eligibility service.
5. Review proposed import budgets, snapshot-vector/freshness policy and actual
   per-file cardinality before later runtime activation. No limits change now.

This is a review draft. No implementation, native/API/provider execution or
NSE-01 production acceptance has been performed by producing these documents.
