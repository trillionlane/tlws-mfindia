# RO-01 — consistent internal reader policy

Status: implemented; final-head native/full/image verification pending.
Not live access or internal-consumer acceptance.

Remote main inspected: `03f3932eb22218478f82e7985e1d0ba527f22cde` (PR135).
The separate dependency prerequisite is commit
`89af628c88f4b905d2136acc7a222683c672fa44`; RO-01 is isolated on top of it
so its policy diff is independently reviewable. Neither change is authorized
to merge or deploy by this feed. All earlier scheduled continuations were deleted.

## Policy and implementation note

| Capability | Existing restricted profile | Explicit internal broad profile |
|---|---|---|
| Runtime identity | Existing dedicated generator/reader/admin checks | Direct, non-elevated `mfdata_verified_reader` |
| Fund source-content SELECT | Existing generator-only boundary | All rows/columns of enrolled fund/research relations |
| Business writes, ownership, DDL, memberships, grant options | Existing prohibitions | Prohibited; generator/admin powers are not reader powers |
| Functions | Existing role-specific checks | Retain only the reviewed existing helper; never grant EXECUTE |
| Future objects | Existing installation contract | Schema-local defaults for `mfdata_admin`; not new schemas/other creators |

Reuse `broad_reader_policy.profile()` and its generated schema/table/default
grants. Add only a small versioned runtime/composition representation of the
restricted-versus-broad choice. Selection belongs to the trusted application
factory/installer, not an HTTP header, query parameter or permission-claim flag;
actual database identity and capabilities must independently verify the choice.

Reproduce the four named raw-content failures on an actual disposable migrated
fixture. Inspect and repair the equivalent enrichment/portfolio/distribution/IDCW
guards too. Preserve ownership, immutability, provenance, mutation and helper
checks; carry the selected profile into startup and affected real read paths.

Replace the current installer composition's reset call with a current additive
profile-aware reconciliation seam inside its existing transaction. Keep the
historical SQL/migrations immutable. Reconcile again, verify complete effective
capabilities before commit, and prove ACL/data idempotence. Do not clear unrelated
grants to make validation pass. Restricted selection must remain restricted.

## Installation composition and affected callers

`reader_access_profile` reuses the existing broad planner, its exact sixteen
schemas and owner-specific default-SELECT plan. The reviewed existing
`mf_fact_review.dependencies(integer)` is checked against its actual catalogue
identity, complete committed declaration seals and existing callee boundary;
no EXECUTE is proposed and no helper is invoked by policy verification.

`rollout_database.install_and_reconcile(..., reader_profile=...)` is the supported
trusted composition point. Inside the existing repeatable-read transaction and
source locks it validates the before-state, reapplies only the selected profile,
compares the complete planned/actual catalogue and runs capability verification
before commit. Existing unrelated grants are not revoked. Unsafe capabilities or
a requested restricted downgrade of an already-broad reader reject/roll back.
Initial optional-role creation retains the original baseline role capabilities;
existing generator/admin grants are not rebuilt or expanded. The historical
reset script and every numbered migration remain unchanged and are never called
by this current installer. The native fixture uses the old reset only to establish
the disposable restricted baseline that reproduces the original conflict.

Trusted `create_review_app(..., reader_profile=BROAD)` carries that choice through
lifespan, actual connection validation and monthly/refresh/multi/book read paths.
Monthly, book, multi, refresh, evidence, portfolio, distribution, IDCW, facts,
fact-review and overlap guards share the selection contract. Their other identity,
ownership, immutability, integrity, function and mutation checks remain in place.
An HTTP header/claim cannot select the profile or elevate an identity. The broad
application rejects administrative mutations even with an administrative JWT
scope; original restricted factory defaults and their behavior remain available.

## Intentional current source authority

`RO01_BOOK_SOURCE_AUTHORITY_20261031.json` is a new source-authority version:
eight unchanged book source pins, one intentionally changed `book_store.py`, the
same SQL/view-generation recipes, unchanged 44 migration ledger, and explicit
predecessor/reason/regression. Repository and embedded installed-image bytes
must match the new complete descriptor. The historical descriptor is unchanged.

`reader_policy_source_version` preserves the three diagnostic specs' historical
attribution mappings and explicitly versions their current `privileges.py` pin.
Other attribution sources, queries, worker identities and runtime bounds are
unchanged. Existing image smokes validate the new source/spec fingerprints;
consumed diagnostic receipts are neither replayed nor re-admitted.

Older release-preservation tests now validate each listed intentional current
file's complete bytes before evaluating its immutable prerequisite predecessor.
`RO01_CURRENT_SOURCE_CHANGES_20261031.json` records only those changed sources,
not a new freeze of unrelated repository files. New mutation tests reject any
unrecorded bytes; current authority and native/API regressions test actual current
behavior separately. This is not evidence that RO-01 source bytes are unchanged.
No controller family was copied, no assertion region ignored and no historical
source document, migration or consumed attempt rewritten.

## Verification and handoff

Native evidence runs only in the repository's disposable PostgreSQL CI fixture.
No local PostgreSQL, shared DEV database, secrets, cloud jobs or source calls.
The fixture must cover actual generated grants, old failure/new guard behavior,
authenticated feature reads, real FastAPI lifespan, denied mutation/elevation,
missing grants/wrong profiles, and repeated supported installation composition.

The baseline MAIN run `41141428621` is a release failure: its test/image phases
passed, but `pypdf==6.21.2` failed the dependency audit. The separate prerequisite
has a passing fresh local audit with 6.21.4; its native/image CI is not yet proven.
Current local observations: 176 targeted policy/source tests passed; after
diagnosing current-versus-historical attribution mismatches, 332 related tests
and all three provider-free diagnostic image-binding smokes passed. An initial
full local run reported 24 failures/5203 passes/550 PostgreSQL deselections; those
failures remain failures, not retroactive success. All 21 contracts, dependency
audit, credential scan and diff check independently passed. Full stable local
and final-head CI outcomes will be recorded after they actually finish.

### RO-02 handoff seam and residual boundary

Use the selected-profile installer and `profiles.prepare`/`verify_selection` plus
the explicitly configured `create_review_app` composition; do not duplicate
raw-content decisions or replace API startup/route checks with `SELECT 1`.
RO-02 owns reliable capture/grant/reader orchestration and diagnostics. This
sprint does not authorize any live capture, grant, image deployment or consumer
acceptance. RO-05 must still verify deployment lifecycle/default privileges for
new schemas and other creators; this contract deliberately makes no such claim.

Final RO-01 head, PR/run links, actual outcomes and remaining blockers will be
recorded here. Unit doubles are not native PostgreSQL or live consumer evidence.
