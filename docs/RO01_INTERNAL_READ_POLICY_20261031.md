# RO-01 — consistent internal reader policy

Status: implemented; completion is determined by final-head native/full/image
results linked from [PR137](https://github.com/trillionlane/tlws-mfindia/pull/137).
Not live access or internal-consumer acceptance.

Remote main inspected: `03f3932eb22218478f82e7985e1d0ba527f22cde` (PR135).
The separate dependency prerequisite is commit
`89af628c88f4b905d2136acc7a222683c672fa44`; RO-01 is isolated on top of it
so its policy diff is independently reviewable. Neither change is authorized
to merge or deploy by this feed. All earlier scheduled continuations were deleted.

## Policy and implementation note

Programme sequence: RO-01 policy/planner/startup/install compatibility; RO-02
reliable capture → grant → reader execution/diagnostics; RO-03 dependency and
CI/release gates (PR136 is an early, separately scoped prerequisite); RO-04
reviewed DEV enablement and actual consumer acceptance; RO-05 lifecycle/new-object
coverage and handover. This change resolves the existing reset conflict now;
it does not claim the full RO-05 deployment lifecycle.

| Capability | Existing restricted profile | Explicit internal broad profile |
|---|---|---|
| Runtime identity | Existing dedicated generator/reader/admin checks | Direct, non-elevated `mfdata_verified_reader` |
| Fund source-content SELECT | Existing generator-only boundary | All rows/columns of enrolled fund/research relations |
| Business writes, ownership, DDL, memberships, grant options | Existing prohibitions | Prohibited; generator/admin powers are not reader powers |
| Functions | Existing role-specific checks | Unchanged invoker helpers; only the reviewed pre-existing SECURITY DEFINER helper; never grant EXECUTE |
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
SECURITY DEFINER helper `mf_fact_review.dependencies(integer)` is checked against its actual catalogue
identity, complete committed declaration seals and existing callee boundary;
no EXECUTE is proposed and no helper is invoked by policy verification.

`rollout_database.install_and_reconcile(..., reader_profile=...)` is the supported
trusted composition point. Inside the existing repeatable-read transaction and
source locks it validates the before-state, reapplies only the selected profile,
compares the complete planned/actual catalogue and runs capability verification
before commit. Existing unrelated grants are not revoked. Unsafe capabilities or
a requested restricted downgrade of an already-broad reader reject/roll back.
Initial optional-role creation retains the original baseline role capabilities;
existing generator/admin grants are not rebuilt or expanded. Restricted
validation checks the complete current relation/column/schema/function/default
population against the historical restricted recipe, including a core-only
database; it cannot label leftover broad SELECT/defaults as restricted. The historical
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
attribution mappings and explicitly versions their current `privileges.py` pin,
including complete pins for the new profile and version modules. Their existing
controllers bind the new exact spec hashes. Other attribution sources, queries,
worker identities and runtime bounds are unchanged. Existing image smokes validate
the new source/spec fingerprints;
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
audit, credential scan and diff check independently passed. A second full run
exposed 23 additional dependent spec binding failures (5209 passes); those were
repaired, not waived. The latest corrected targeted set has 413 passes.
Independent review identified a P1 core-only restricted-downgrade gap; complete
catalogue/default validation and a fifth native regression address it. The
function-access P2 was withdrawn after reconciling the exact approved intent;
documentation now distinguishes unchanged invoker helpers from the single
approved privileged helper. No function grant or revoke was introduced.

Re-review additionally caught the existing Python-installed multi-facts prior
view missing from the restricted allowlist, and a restricted helper-attestation
gap. Both prior views now have positive/unknown-view negative regressions. The
complete restricted connection gate runs at actual API lifespan and before/after
installer reconciliation, including metadata-only helper declaration attestation
and existing callee seals. It does not repeat whole-catalogue work for every
nested feature/preservation guard. Historical restricted PostgreSQL-owner
installations remain supported; the broad profile still requires mfdata_admin.
Four additional native helper body/configuration/ACL/declaration drift cases
plus complete callee declaration and independent-owner regressions bring RO-01
coverage to fourteen disposable PostgreSQL cases. The existing full
`broad_reader_function_closure` SQL/validator is shared rather than duplicating a
weaker callee proof. Restricted namespace owners are independently bounded to the
two existing installer owners, and actual helper-owner callee EXECUTE is checked;
the reverse mixed-owner combination is not assumed valid. The old strict broad
closure entrypoint remains strict. No helper is invoked by these checks.
A local validation run
overlapped corrective edits and was invalidated by the byte guard; it is not
final-head proof. Candidate `67846cb` subsequently passed all 26 local checks:
5253 non-PG tests, 555 PG deselections, 38 subtests, 21 contracts, lint, audit,
credential scan and diff check in 207.349s. That is not evidence for the later
owner/complete-callee correction; final validation runs against its unchanging
replacement candidate and is linked in PR137.

The restricted helper gate requires actual helper-schema USAGE; a missing grant
is rejected by startup and restricted installation, with an explicit denied
invocation regression. Broad preparation alone may discover/reapply that missing
USAGE through its existing planner. The mixed-owner positive regression also
invokes the real read helper and reruns the supported historical restricted
installer as its existing postgres administrator, proving unchanged complete
catalogue and source rows. Ordinary current installations and broad grants use
mfdata_admin; no role is elevated to make a fixture pass. Historical fixture
baseline role reset runs only through the existing disposable administrator.
Candidate `cfbf815` passed all 26 local checks in 212.385s (5272 non-PG, 559 PG
deselected, 38 subtests); that predecessor result does not certify the final
schema-USAGE/bootstrap correction. The final exact-head outcomes are in PR137.

Hosted run `41151029340` failed CI-plan validation before substantive tests:
non-main workflow dispatch is not supported. PR137 now targets main so the
unchanged supported PR workflow can run. Its GitHub comparison includes the
separate prerequisite until PR136 merges; the focused RO-01 review range is
`89af628..HEAD`. This changes neither rulesets nor CI routing. Final exact-head
results, SHA and run links are maintained in the PR description/checks rather
than forcing another expensive unchanged CI run for a documentation-only update.

### RO-02 handoff seam and residual boundary

Use the selected-profile installer and `profiles.prepare`/`verify_selection` plus
the explicitly configured `create_review_app` composition; do not duplicate
raw-content decisions or replace API startup/route checks with `SELECT 1`.
RO-02 owns reliable capture/grant/reader orchestration and diagnostics. This
sprint does not authorize any live capture, grant, image deployment or consumer
acceptance. RO-05 must still verify deployment lifecycle/default privileges for
new schemas and other creators; this contract deliberately makes no such claim.

Final RO-01 head, exact CI outcomes and remaining blockers are linked through
PR137. Unit doubles are not native PostgreSQL or live consumer evidence.
