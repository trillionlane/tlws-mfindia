# Sprint 4 — production SCH44 source-native fund details

Implementation base: `d36363f44a312542c0a69726304c129ee3ce7fee` (PR165).
This is a source reference view, separate from FULL Scheme Master promotion.
No purchase/SIP eligibility follows from a source field or collateral acceptance.

## Storage and installation

Reuse the immutable 045/048 table templates through the explicit versioned
`nse-source-native-storage-v1` composition, then additive 049. The derived SQL has
its own hash: it is not represented as historical migration 045. The two original
migration files remain byte-identical. No live schema exists at preparation time.

A separately reviewed administrator proposal provisions only the fixed direct
identities `mfdata_nse_owner` and `mfdata_nse_ingest`, both NOINHERIT, non-elevated,
without memberships or database CREATE. The owner owns only a pre-provisioned
empty `mf_nse` namespace. The writer gets four canonical SELECT inputs and only
append-only source history plus UPDATE on the separate source head. No canonical
write/DDL or FULL-head/current_sch mutation is granted. Existing shared roles are
not changed. Fresh DEV inspection found those shared roles elevated; rebinding a
secret alone cannot satisfy their unchanged legacy importer guard. Credentials
and live provisioning require separate approval and are not created by this PR.

`nse.source_native.install` runs on the direct new owner in its outer transaction,
refuses an occupied legacy namespace, and verifies the exact derived configuration
on re-entry. Only `mf_nse.source_routes` is an eligible dataset from this creator;
private source/control tables cannot enroll. Routine publication never enrolls,
grants, migrates or resets roles. The existing SCH family head, current_sch and
FULL publication checks remain unchanged. A separate source head selects the
source reference. Original bytes and every source line remain versioned.

The reader gets SELECT on the safe view, not the private payload, archive binding,
operation records or source configuration. Existing broad/restricted profiles,
caller authentication and ingestion privileges remain unchanged. Deployment IAM
is the existing internal-ingress/OIDC boundary, not an HTTP header; ASGI/native
tests do not establish live authenticated consumer acceptance.

## Candidate preparation and review

`scripts/prepare_nse_source_native.py` is file-only, create-only and writes mode
0600. Provide the retained production payload, exact archive generation and
retrieval provenance, dated catalogue, distinct earlier NSE witness, both retained
AMFI column witnesses and independently hash-pinned product decisions. It prints
only the candidate hash. No Member ID, transport, environment DSN, database or
cloud action is used by preparation. Input paths and private references stay out
of Git. Recomputed identities use the [current v2 rule](NSE_SCH44_LINK_RULE_V2_20261120.md),
retaining PR165's production code/issuer/ISIN-slot and
explicit plan/option checks, not a unique ISIN or the literal NSE flag alone.
Product evidence can withhold a link, never create an exact identity.

The trusted operator separately reviews the complete candidate and supplies its
hash to `source_native.publish`, together with a fresh operation ID and expected
source head. Revalidate payload/rows and candidate hash before database work.
Inside REPEATABLE READ, lock the source head and verify the exact current catalogue
version and identity-input hash. Any drift stops. Verify both source identities cannot write canonical data before and after full
readback inside the transaction; retain
all 44 literal cells, fully read back every stored row/mapping and original bytes,
and commit once. Existing 5s statement/lock and 15s transaction limits apply.
A failed precommit check rolls back; COMMIT uncertainty is inspect-only with no
blind retry. Durable operation reservation remains the existing operator's duty.
`source_native.inspect` reads only the original operation and separate source
head. An absent observation is UNKNOWN and never authorizes replay. Source result
format identifies SOURCE_REFERENCE without claiming FULL-head advancement. The
legacy FULL importer retains its original canonical value-fingerprint scans; the
new source-only composition uses verified absence of canonical write capability,
avoiding repeated full NAV-history scans. Native tests compare actual canonical
values, including precise NUMERIC NAVs, before/after success and rollback.
A known replay creates no snapshot/route and never reactivates a retired revision.
Retrieval time and archive location/generation are observation provenance; changing
only those records another observation without duplicating source business rows.
The full candidate hash still binds all provenance for independent review.
A changed payload is a new reviewed revision; old snapshots remain immutable.

The owner's generation-date policy is filename-based. For the retained production
file `NSE_NSEINVEST_ALL_17112026.txt`, generation date is 2026-11-17 with basis
`FILENAME`. This is not an effective business date or full member-scope claim.
The source-native view permits explicitly held/unclassified rows; it does not
relax the separate FULL snapshot gate.

## Fund-details contract for TLAM

`GET /api/funds/{code}` gains `nse`, version `nse-sch44-source-native-v1`.
Status is `NOT_INSTALLED`, `NO_VERIFIED_LINK`, or `VERIFIED_LINKS`. Routes are an
ordered array, preserving every linked source line and route code. Each contains
all **44 required string properties** under `fields`,
`identity_evidence` with both AMFI witness hashes/lines and honest historical-route
state, plus the dated `source_sip_allowed` declaration from NSE field 29, using exact SCH44 header
names, plus snapshot/hash, generation date/basis, retrieval time, layout,
catalogue version, source line, verified AMFI slot/plan/option, disagreement marker
and field holds. `transaction_eligibility` is always `NOT_ESTABLISHED`.

Empty source cells stay empty strings, never fabricated nulls. Sentinel dates,
leading zeros, decimal spelling, literal flags and additional-purchase maximum
remain unchanged. A null AMFI slot on an unlinked source row means no verified
slot; it does not rewrite the source. SIF and other unresolved products remain in
the source dataset and cannot attach to a fund. The API checks the pinned linked
variant's current issuer, slot, plan and option again; it does not invent a new
link if the catalogue drifts. Existing response fields/calculations are preserved.

The complete OpenAPI is `contracts/mfdataindia-openapi-v2.json`, with explicit
`NseSourceSection`, `NseSourceRoute` and `Sch44Fields` schemas. The existing bounded
and authenticated dataset API discovers/paginates `mf_nse.source_routes`, including
unlinked dispositions. Core API access still requires the existing private service
invoker. No TLAM changes, route selection or eligibility inference are included.

## Validation and authority changes

`test_nse_source_native` checks source provenance, independent pins, 44 literal
fields, null/blank distinction, both flag disagreements, withheld products and
missing corroboration. `test_nse_source_native_postgres` uses the existing hosted
native fixture for installation/re-entry, real fund-detail and dataset reads,
multiple routes, SIF/held separation, replay, same-date revision, late rollback,
reader denials and unchanged canonical/family/generator state. The native cases
connect as both new direct identities and prove occupied namespaces, shared-role
callers and unsafe new grants are rejected. A real late INSERT trigger corruption
fails full readback and rolls back the source head and history. Existing SCH43,
SCH44 FULL and all legacy regressions remain enabled. Local native PostgreSQL is
not used. Both images import the new schema and check the packaged migration.

Sixteen intentional current files require a finite test-only historical inverse:
current Verify image smoke, additive OpenAPI, fund query composition, and response
models, explicit attribution v4 and its three spec/controller pairs, scoped
dataset creator/enrollment guards, the existing exhaustive early-test partition,
and the two test-only historical composition helpers. The early gate retains all
previous native cases and adds these source cases; the remainder is disjoint.
`NSE_SOURCE_NATIVE_CURRENT_SOURCE_CHANGES_20261119.json` pins their complete
before/after bytes. The existing historical helper verifies these bytes before
returning predecessor bytes to older release assertions. The two original dataset
workflow assertions explicitly compose that same finite source-native inverse
before applying their unchanged historical hashes and exact workflow comparisons. Historical authority
files are not rewritten. Current attribution v4 binds the new query and source-version
bytes in all three specs/controllers without changing SQL, scope, transaction limits
or spent-operation guards. The existing current-authority tests and packaged smokes
verify the bindings and reject drift. Current behavior has
independent execution tests. The importer change adds one trusted stage callback
to the existing transaction seam without changing default legacy behavior.

No merge, deployment, live publication, schedule/IAM change, API retrieval,
canonical crosswalk update, UAT deletion or TLAM activation is part of this PR.
The final exact-head CI and private candidate evidence are linked in the PR.
