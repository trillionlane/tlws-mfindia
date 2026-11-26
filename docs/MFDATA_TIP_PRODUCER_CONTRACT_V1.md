# MFData native producer contract — architecture-confirmation draft

MFData owns the reference-master acquisition, canonical identity mapping and
producer contract. TIP is a future consumer. This uncommitted source-only draft
adds no route, listener, database adapter, real materializer, provider client,
publication, schedule or activation. Every positive example is synthetic.

Base/unchanged HEAD: `4b2244fbe45d90bc315b4ba48f2967d6869db002`.
The former TIP-I0 compatibility design is superseded by the owner decision of
23 November 2026. The normative contracts are:

- record: **mfdata-execution-reference-v1**;
- immutable bundle: **mfdata-reference-snapshot-v1**;
- immutable attempt receipts: **mfdata-reference-refresh-observation-v1**;
- mutable latest summary/pointer: **mfdata-reference-refresh-status-v1**.

The source of truth is the strict Pydantic definition and its JSON Schema
annotations in `producer_contract/schemas.py`. The exporter mechanically derives
standalone JSON Schema, bundle/refresh JSON Schema and OpenAPI 3.1. No independently
maintained wire schema or external TIP schema controls the producer. OpenAPI
`paths` is empty because this slice implements no HTTP service.

## Record and validity

The exact record fields are `contractVersion`, `executionReferenceId`, `snapshotId`,
`sourceAsOf`, `expiresAt`, `variant`, `mapping`, and `providerReference`.
`expiresAt` is **required literal JSON null**, represented by `const: null` and
`type: null`. Omission, date strings, numbers, booleans and objects are rejected.
`sourceAsOf` is the time MFData accepted/committed the mapping snapshot, not a
provider effective/publication date or the latest refresh observation time.

Validity follows explicit supersession/retirement or emergency revocation, not
age. There is no TTL, rolling acceptance time, invented far-future date or age-based
withholding. An old source file can remain the accepted mapping head while its
age and failed refreshes remain visible operationally. A future authorized
publisher/control plane must handle supersession, retirement and revocation;
this draft supplies no operational mechanism or consumer activation authority.

Strict serialization rejects coercion, unknown members, booleans/floats/strings
as AMFI integer codes, unsafe integers and malformed dates. Provider reference is
exactly `{provider: "NSE", schemeCode: ...}` for MAPPED, and null for all other
statuses. No transaction eligibility, purchase, SIP, NAV, facts or family fields
enter the execution item. All 16,384-byte checks use final standalone UTF-8 bytes.
Canonical encoding sorts keys, uses compact UTF-8 JSON, and rejects non-finite
numbers. Validation runs again after serialization, including constructed or
mutated model instances. Public serialization errors contain fixed codes only.

## Closed scope, codes and reasons

Initial eligible variants are active, conventional, canonical, in-scope REGULAR
AMFI variants. Identity is `(amfiSchemeCode, matchedIsinSlot)`. Exact derivations:

| AMFI plan | AMFI option | Verified AMFI ISIN slot | planCode | optionCode |
|---|---|---|---|---|
| REGULAR | GROWTH | GROWTH_OR_DIV_PAYOUT | REGULAR | GROWTH |
| REGULAR | IDCW | GROWTH_OR_DIV_PAYOUT | REGULAR | IDCW_PAYOUT |
| REGULAR | IDCW | DIV_REINVEST | REGULAR | IDCW_REINVEST |

`policy.py` versions the registry and reason table. No title parsing, label
uppercasing, NSE flag/route-suffix inference or fallback is implemented. Direct,
SIF, inactive, noncanonical, scope-excluded, unlabelled and unsupported populations
are manifest accounting only. Population buckets are mutually exclusive in the
explicit precedence implemented by `population_exclusion`; they are separate
from consumer mapping reasons. This is a pure contract rule over explicit facts,
not an independently verified source adapter or a real mapping builder.

- MAPPED: exact canonical identity and independently verified NSE route; each valid
  route is a separate record. Its reason array is empty.
- UNMAPPED: a complete accepted NSE universe was evaluated and no exact reference
  exists; `NO_EXACT_NSE_REFERENCE`.
- AMBIGUOUS: plausible routes lack sufficient proof;
  `MULTIPLE_PLAUSIBLE_NSE_REFERENCES`.
- CONFLICT: explicit AMC, ISIN, plan, option or duplicate-source disagreement,
  preserving its specific closed reason.
- OUT_OF_SCOPE: recognized in the standalone record schema, but excluded from
  initial execution pages; exclusions remain manifest accounting.

Coarse legacy labels such as MF_HELD or ISSUER_NOT_CORROBORATED are not silently
translated into stronger conflict claims. Unknown evidence produces a value-free
local policy error, not a guessed record. Native mapping reasons contain no
age-expiration outcome. Missing source/head is represented by an UNAVAILABLE descriptor and zero
items, never pseudo-UNMAPPED records. No real NSE execution items are produced.

## IDs, counts, completeness and immutable pages

Mapped ID is `mfer-route-v1:` plus SHA-256 of canonical JSON containing provider,
AMFI code, verified slot, exact ISIN, planCode, optionCode and literal NSE scheme
code. Assessment ID is `mfer-assessment-v1:` plus SHA-256 of canonical AMFI code
and slot. Neither includes source line, filename, snapshot ID or refresh time.
An assessment disappears when independently verified route IDs replace it.
Multiple routes for one variant remain a set; no preferred route is selected.
A single canonical key cannot carry conflicting ISIN/plan/option values or mix a
mapped route with a nonmapped assessment in the same section.

The root binds the ordered descriptors `variants`, `executionReferences`, `nav`,
`facts`, `family`. Each descriptor has a schema version, content digest, immutable
section ID, availability, sourceAsOf, null expiresAt and record count. Only
executionReferences has a typed page in this slice. The other sections are reserved
UNAVAILABLE/count-zero descriptors; there are no generic untyped domain pages.

Execution metadata pins the canonical and NSE source snapshots/digests, registry
and policy versions, acceptance time, separately labelled NSE filename-generation
date/basis, completeness, status counts and exclusions. Count definitions:

- input variants = eligible variants + excluded variants;
- eligible variants = evaluated variants + withheld eligible variants;
- exclusion buckets sum to excluded variants; withheld buckets sum to withheld
  eligible variants;
- `variantOutcomeCounts` has mutually exclusive MAPPED, AMBIGUOUS, UNMAPPED and
  CONFLICT buckets; their sum equals evaluated variants;
- `recordStatusCounts` sums to execution itemCount, which counts records;
- mapped records can exceed mapped variants because routes form a set, while
  each other assessment's record count equals its variant outcome count;
- the complete section's actual variant keys and record statuses are compared
  with both count sets before any page is sliced. A page alone cannot certify them.

Incomplete sources cannot assert UNMAPPED or a new accepted mapping. Their held
candidate has zero items/no acceptance time; a failed or incomplete refresh cannot
replace the last complete accepted head. Source generation date is provenance,
not an acceptance timestamp or proof of transaction eligibility.

Section content digest hashes records ordered by AMFI code, slot, reference ID,
excluding their root snapshotId pointer to avoid a hash cycle. Section ID hashes
the complete descriptor except sectionId. Root ID hashes the ordered full
section descriptors. Item root pointers are stamped/fenced against that root.
The synthetic page helper verifies all section contents/counts before slicing.
Pages use limits 1–100; canonical base64url keyset cursors bind root, section and
last full sort key. Wrong/unknown positions, duplicate keys, mixed snapshots and
noncanonical tokens reject. Cursors are positions, not authorization.

New complete snapshots can retire routes through absence. Historical snapshots
remain immutable; the fixture tests retain both complete versions and compare
route-ID sets. No physical deletion, tombstone or actual head-store operation is
implemented. Runtime retention duration and authenticated revocation transport
remain later design/activation work; no expiry or cache fallback is invented here.

## Immutable refresh receipts and mutable latest status

`RefreshObservation` is a standalone immutable terminal-attempt receipt. It has its
own contractVersion and deterministic observationId, attemptId and attemptAt,
source snapshot ID/digest and filename date/basis where available, disposition and
closed diagnostic, candidate mapping-content digest where available, canonical
universe pin, registry/policy versions, and previous/retained-or-accepted head IDs.
`attemptAt` records the terminal attempt observation time; it is not a provider
publication time. observationId is `mfrefresh-observation-v1:` plus SHA-256 of its
entire canonical body excluding observationId. Distinct attempt IDs preserve
separate attempts even with identical source bytes; any altered body invalidates
the receipt ID. Append-only storage is a later integration task, not claimed here.

`RefreshStatus` is only the mutable latest summary: activeSnapshotId,
lastRefreshAttemptAt, lastSuccessfulRefreshAt, lastVerifiedAt, refreshStatus and
`latestObservationId`. It references the exact immutable receipt rather than
embedding an anonymous, overwritable observation. Standalone schemas and pinned
fixtures exist for both contracts; `contracts/mfdata-reference-refresh.SHA256SUMS`
binds their schema and fixture bytes. No receipt or summary enters a mapping digest.

There are two distinct digests. The descriptor's `contentDigest` remains the
complete wire-record hash excluding only the root pointer; it retains sourceAsOf
integrity. Required `executionMetadata.mappingContentDigest` is instead the SHA-256
of the canonical `mfdata-mapping-semantics-v1` projection: sorted records containing
only variant (all five fields), mapping (status/reasonCodes), and providerReference
(provider/schemeCode or null), plus population (universe, recordStatusCounts,
variantOutcomeCounts, exclusions, withheldReasons). Canonical JSON uses UTF-8,
sorted object keys, compact separators and no NaN. Record sorting uses the existing
AMFI code/slot/reference-ID key; every distinct NSE route remains present.

This semantic projection excludes snapshot/root/section/derived record IDs,
sourceAsOf/acceptedAt, source IDs/hashes/dates, receipt/attempt times and envelope
labels. The required-null expiry remains independently validated. Canonical,
registry and policy pins are separate checks, never substitutes for changed
semantics. Semantic digests are non-null only for COMPLETE sections (including a
genuinely complete empty section); UNAVAILABLE and INCOMPLETE require null.
The receipt's `candidateMappingContentDigest` binds this semantic digest, not the
wire-content hash. Caller-supplied digests are never sufficient by themselves.

`VERIFIED_UNCHANGED` permits a new raw NSE source ID, digest or filename date when
the recomputed mapping-semantic digest, canonical-universe pin, registry version
and mapping-policy version equal the active accepted mapping. The original
manifest/root/records, accepted provenance and original sourceAsOf remain intact.
Raw provenance advances only in a new immutable receipt and latest pointer.
Pin-only changes cannot manufacture an UPDATED mapping; unsupported pin drift
fails closed and retains the prior head pending an explicit reviewed transition.

FAILED, INCOMPLETE and CONFLICT receipts retain the prior head and last successful/
verified times. UPDATED requires a new complete manifest and a recomputed semantic
digest different from the bound prior mapping. Rebuilding identical mappings
with new acceptance timestamps or labels is rejected even if every wire/section/
root/receipt hash was recomputed. First acceptance requires an absent prior head;
it does not invent a prior snapshot. The receipt's NSE source ID, hash and filename
date/basis must exactly match the new manifest. Both successful dispositions bind
canonical/policy/registry pins to the accepted manifest.

Successful `validate_refresh_transition` calls require accepted_records and,
when a previous head exists, prior_manifest/prior_records bound to that exact head.
Complete records/accounting, both digests and every record's timestamp are checked
before semantic comparison or page slicing. Missing or inconsistent prior context
fails closed. Exact replay requires the original predecessor context and identical
receipt/status; a fresh attempt cannot reaccept the same semantics as UPDATED.
Summary ID, disposition, time and head agree with the referenced receipt.
The validator reads or writes no state and validates supplied synthetic objects
only; it does not attest that a real materializer derived them. A rejected refresh
is distinct from a canonical variant's record-level CONFLICT assessment. Real
publication gates remain outside this draft.

This replaces the unmerged, unactivated draft v1 shape before first normative
publication. It adds required metadata and changes the receipt digest meaning;
there is no permissive fallback for the prior draft. Its reviewed commit, fixtures,
certificate and CI remain historical evidence. A replacement commit requires new
CI and exact-head review. Published/adopted immutable contracts require a separately
named version, as described below; no deployed-consumer compatibility is claimed.

## Validation and future compatibility

Run local provider-free checks only:

```sh
.venv/bin/python tests/fixtures/producer_contract/build_fixtures.py
.venv/bin/python scripts/export_producer_contract.py
.venv/bin/python scripts/export_producer_contract.py --check
.venv/bin/pytest tests/test_producer_contract.py tests/test_producer_contract_compatibility.py tests/test_producer_mapping_policy.py tests/test_producer_refresh_observation.py -q
```

Development-only jsonschema Draft202012Validator with RFC3339 format checking
validates actual emitted fixture bytes against MFData's own generated schema.
The schema enforces strict keys, null expiry, closed registry/reasons, ISIN shape
and status/provider/option-slot conditions. Producer validation additionally checks
primitive Python integers, derived hashes, accounting relationships, canonical
reason order and emitted-byte size; the evidence distinguishes those results.
The existing dev-only dependencies remain unchanged by this correction.

`contracts/mfdata-producer-validation-evidence.json` records generated, source and
fixture hashes, expected/actual results, and explicit NOT_RUN hosted/live status.
`contracts/future-tip-alignment.json` is **non-normative**. The retained TIP I0-v1
schema is historical input only: its string version and non-null expiry requirement
are incompatible with the native contract. No TIP parser qualification is claimed.
The future TIP adapter must pin a reviewed MFData version, consume null-only
validity, retain all routes per variant as a set and never select the first item
as implicit routing policy. TIP retains transaction authorization/orchestration.

V1 becomes immutable after publication. With additionalProperties=false, even a
new optional attribute cannot silently be added to v1. Any record shape or semantic
change requires a newly named version. A future domain/manifest section likewise
requires its own explicit version and review; if the bundle shape changes, publish
a newly named bundle. Additive delivery means a separate reviewed version/contract
alongside v1, preserving v1 for existing consumers, not permissive unknown fields.

This draft is returned for MFData architecture confirmation only. No commit, push,
PR, hosted CI, independent source review, database/provider access, credentials,
IAM, deployment, schedule, live publication or TIP activation is authorized.
