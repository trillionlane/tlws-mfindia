# Metadata-only controller: authenticated OCI platform resolution

Design written before implementation. Direct human approval is **code-only**:
design, regressions, independent exact-head review and CI. No runtime allocation,
job/execution retry, IAM/DB/source/schedule writes or retroactive acceptance.

## Observed failure and preservation

The single consumed metadata execution `mf-core-access-metadata-261022a-ldxhh`
succeeded and produced a valid sealed metadata certificate. Its original
controller exited 1 and has no result.json. The initial execution response used
approved OCI index digest `2f871f98aeab945e11e1325346f5d872ead980642669a3fbb5c7365eff89e101`;
later execution/list responses used its already authenticated Linux/amd64 child
`8fd70a92520cd25c9dfa0b519fe4ef0aa59e967aee5c5c4d1b817435944eecbc`.
The whole-spec equality guard rejected that one image representation change.
Both retained provenance receipts bind the child config digest
`9e52e68a72212b3dcf448babaec323b8ac30fcc39b1702d1a9dd8a7fa8695c5f`.
These observed digests are evidence, **not hard-coded acceptance exceptions**.
All consumed approval/root/START/job/execution/failure records remain immutable.

## Narrow change

1. Extend the existing immutable OCI verifier with an opt-in resolution receipt.
   Its default return value and behavior for every existing caller remain intact.
   In opt-in mode, verify root bytes against the approved digest, require exactly
   one Linux/amd64 child for an index, verify child manifest and config bytes by
   digest, require config platform and Git label, and reject duplicate JSON keys.
   Return the approved image, resolved same-repository image, config digest,
   expected Git, exact platform and zero layer downloads. No extra fetch/retry.
2. Metadata backend preflight obtains this receipt twice and requires exact
   equality; expose a defensive copy only after both checks succeed. Namespace,
   original request, credentials, resource absence and write guards stay intact.
3. Metadata controller validates and durably retains a defensive copy of this
   narrowly shaped receipt **before create intent or provider mutation**. Bind it
   to the approval's exact image and Git; reject malformed/cross-repository,
   unsupported platform, nonzero layers or missing resolution.
4. Job creation and seals continue requiring the exact approved index image.
   Only execution-spec comparison may compare either that image or its verified
   child as equivalent. Canonicalize an in-memory deep copy of that single image
   field, then require whole-spec equality. Raw execution evidence is unchanged.
   Missing proof never permits child resolution. All metadata, parent UID/time,
   environment, pinned secret refs, network, resources and every other spec field
   retain exact equality. No arbitrary digest or repository equivalence.

## Verification and unchanged scope

Test the old failure first with synthetic provider-shaped responses. Positives:
index-to-child transition, child first response and direct manifest. Negatives:
unknown child/repository, changed Git/config/platform, root/child/config byte
tamper, duplicate keys/children, missing/disagreeing receipts, receipt aliasing,
and every unrelated task/metadata capability drift. Check full controller flow
and create-only receipt order, no second create/execute, exact final inventory
then protected-LAST read. All existing tests stay; alter byte-preservation
disposition only for the explicitly changed run/immutable/preflight bodies and
new receipt helpers. Other controller/backend function bodies remain exact.

Frozen metadata SQL/schema/inventory, worker/spec hashes, original query files,
dependencies/lock/security/PDF controls, CI jobs/step bodies/order, budgets,
1CPU/1Gi/600s/one task/zero retries and all admission boundaries are unchanged.
No local PostgreSQL, Docker or provider call. Full local non-PG validation,
collection reconciliation, named exact review and whole HEAD CI are mandatory.
Any merge/main-CI step uses the established exact-head process; this code never
authorizes new images, deployment or another diagnostic. Current failed run is
never rewritten as accepted.
