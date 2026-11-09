# HDFC installer: operation-local immutable construction

Base: `3437031e205e62202dcaa31e1290b78b88a31715` (PR147).

The first DEV source8 installation exceeded the existing 15-second transaction
limit. Fresh read-only state reconciliation found the original source7
configuration intact; no successful source8 COMMIT was persisted. The failed
attempt remains historical and is not retried by this change. A subsequent
read-only profile measured the current-projection guard at 6.08 seconds wall,
5.59 seconds Python CPU and 0.10 seconds SQL. It repeatedly constructed the
same immutable policy ancestry. The local constructor experiment alone does
not establish transaction viability.

## Change and retained boundaries

`book_sources.current_policy` constructs each parent once. The installer opens
`reuse_current_policies`, which constructs all eight recognized policy documents
before opening the owner transaction. A context-local dictionary lives only for
that operation; callers receive deep copies and every exit resets the context.
Nested operations are independent. Default registry v5 and explicit selections
remain unchanged. No process-global memoization or database verdict cache exists.

All actual owner identity, lock, catalogue, effective ACL, installed configuration,
function body, original integrity, pre/post preservation and history comparisons
remain inside the same owner transaction. The SQL, order, one COMMIT, 5-second
statement limit, 5-second lock limit and 15-second transaction limit are unchanged.
The change does not move database observations outside the transaction or select
a new source method by itself.

The complete policy and source-configuration documents for versions 1–8 were
captured from the base before editing. The regression compares whole documents
and their canonical hashes, including the unchanged source8 configuration hash
`dfe1c5931b49c841b707ab8f4f7e361422d72c7b38a3414d002b268976a42051`.

## Current source bindings

Historical allocations and authority documents remain byte-for-byte release
evidence. The current workload descriptor is version5 and pins actual changed
source, the same source8 configuration, six tasks and all original limits.
`official-hdfc-installer-policy-reuse-predecessors-v1.json` describes finite,
complete byte inverses for only `book_sources.py` and `hdfc_install.py` before
the existing historical inverse stage. Unknown bytes reject. It conveys no live
execution authority. The new ten-file reader descriptor binds the actual policy-
neutral source change; reader entitlement and SQL are unchanged. The test-only
current-change manifest composes whole-file historical assertions without
changing their expected hashes or weakening them.

## Verification

- `test_hdfc_policy_reuse.py`: all eight complete policies/configurations,
  construct-once behavior, copy isolation, selection, context isolation and reset
  after failure. The new operation tests failed against the base before the fix.
- Existing installer source/idle/profile boundary tests remain.
- Existing dynamic native test retains authenticated API reads, September
  preservation, actual grant checks, denied reader writes/DDL and repeat polling.
- New populated native case uses the supported migrated graph and publication
  seams: broad reader, real retained monthly/portfolio fixture values, two sealed
  Kotak batches, 551 original rows (including a synthetic original over 2 MiB),
  122 book receipts and 37 multi-source receipts. It also publishes the retained
  September HDFC original (plus its identity PDF, 7,181,300 bytes total) through
  the real bounded decoder and publisher. Installation therefore hashes actual
  non-empty HDFC originals and preserves a published snapshot and authenticated
  last-valid-period read. No trigger, role or policy is
  bypassed. This is a representative fixture, not an exact DEV data clone.
- The native case injects a failure after the actual transition and final guard,
  verifies rollback of all three configuration heads and complete business-row
  hashes, then runs the full public installer and fresh-connection re-entry.
  Every actual statement still executes. Both successful transactions must finish
  below 15 seconds with their configured limits intact. Measurements include
  whole installer, transaction, lock-hold, CPU, SQL and catalogue timings.
  Installation/re-entry are constrained to one hosted CPU; affinity is restored.
- Native results, full CI, contracts, audit, credentials and both-image results
  must be reported from this PR's final head. Hosted PostgreSQL is required;
  provider-free tests are not native evidence. No local PostgreSQL is run.

## Release and one controlled DEV attempt

This PR requires independent exact-head review and successful actual MAIN
certification before release. Reuse previous completed PR147 evidence as
historical; do not reinterpret its images as the optimized source. Bind any new
release to its own certified Git/image/config bytes.

Before one installation attempt, freshly reconcile installed heads, complete
business preservation, current owner sessions and the prior failed attempt.
If already installed, verify and do not resubmit. Stop on drift or unresolved
effects. Keep the recovered compatible source7 generator until the public
installer has a confirmed outcome and fresh source8/book8/current14/seal
verification succeeds. Unknown outcomes require read-only reconciliation, not
retry, automatic reversal or a larger timeout.

Only then switch the existing generator to the reviewed allocation. Check HDFC
through the actual worker's existing egress. Record HTTP 403/FETCH_FAILED and
retain the last valid period when access is denied; successful installation is
not issuer availability or publication proof. If acquisition succeeds, verify
actual PDF date, publication receipt and authenticated API readback separately.
Keep the existing daily schedule and unrelated sources, IAM, secrets, reader
permissions, core/review serving configuration and TI configuration unchanged.
No automatic installation, deployment or provider request occurs in CI.
