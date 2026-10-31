# RO-02 — reliable internal-reader execution

Implementation in progress; not acceptance or live enablement. Actual merged
base: `69037c0ec078ad67a8dfd708bc3cf86040b655e6`. Its release verification is
blocked by the separately reviewed CI-only source-attempt repair in PR138.
Independent implementation proceeds here without claiming that base is CI green.
Final readiness requires reconciliation with verified merged main and the final
head's complete required checks. No RO-02 merge or live action is authorized.

The implementation reuses RO-01's selected broad policy, the direct grant's
full catalogue/source/function/recursive-lock/transaction checks and the existing
fixed DEV network, identities, secrets and budgets. One versioned operation ID
replaces repeated dated worker/controller copies. One bounded approved plan can
cover capture, deterministic grant and authenticated reader stages; this sprint
does not fabricate that future RO-04 approval.

Local validation creates no job, secret read, connection, journal or spent intent.
Submission is durable/create-only before ambiguous boundaries. Inspection is a
separate read-only path and never resubmits mutations or SQL. Unknown outcomes
stay stopped. A later reader/application failure cannot erase a known commit.

Worker checks establish execution-context database capabilities. The integration
harness exercises the real selected-profile application lifespan and fixed
authenticated feature reads. Internal-consumer acceptance remains pending RO-04.
No freshness, financial correctness, public redistribution or permanence claim
follows from SELECT access. DEV no-freeze limitations remain explicit.

## Command flow and composition

Use `scripts/reader_execution_dev.py validate --plan <private canonical plan>
--expected-git <final merged SHA>` for provider-free validation. It checks every
plan member, duplicate/canonical JSON, fixed target/profile/identity, complete
source/code/image binding, operation grammar, window and initial request budget.
It does not read a secret, connect, allocate an operation root, write a mutation
intent or spend an identity. Runtime observations and derived-stage capacity
remain explicitly unverified until fresh capture. Correct an invalid local
candidate normally; no code edit or deletion of historical records is required.

An operation ID such as **synthetic** `op-0123456789abcdef` selects exactly three
resources under `mf-reader-internal-{capture,grant,reader}-<operation>`. Project,
database, roles, sixteen schemas, existing service accounts/version1 secret
references, network, single task, zero task retries, 600-second task cap and
original SQL/15-second worker bounds remain fixed. No arbitrary resource, SQL,
source alias, identity or legacy consumed target can be supplied. The same
versioned worker is used for all stages; release identity is separately bound.

RO-04 may supply **one directly approved bounded plan authority** covering this
complete deterministic sequence, not three repeated human fingerprint prompts.
`execute --plan <...> --expected-git <...> --trust <...>` is dormant here.
Authority is loaded from the **fixed independently custodied**
`/private/etc/mfdataindia/reader-programme-anchor.json` and
`/private/etc/mfdataindia/reader-programme-approval.json`, not from an HTTP flag,
CLI-selected file/key, or caller-asserted approval hash. No such OS records are
installed or generated in this sprint. An unprivileged executor must read both
through per-component no-follow directory descriptors, with root ownership and
no executor/group/world write authority. Complete canonical closed schemas bind
the actual full plan, direct-human record provenance, existing operator, all
three stages, fixed journal directory identity/modes, externally provisioned
genesis, next epoch, and exact settled prior lineage/inventory digests. Missing,
changed, stale or self-asserted authority rejects before provider preflight or
operation allocation. The same opened-record seals/canonical bytes are rechecked
before every create; installing another approval cannot silently change an
in-flight chain. There is no executor API for provisioning/resetting genesis,
issuing an approval, obtaining a signer or changing custody.

Canonical public application trust and existing delegated read **and read+admin**
assertions/current configured reader DSN must be supplied before dispatch. This
sprint creates no runnable approval and provisions no credentials or network.
The operator preflight independently verifies clean merged Git, explicit
existing operator/project, both immutable images twice, protected profiles and
all three new target inventories. Later stages also verify each original
predecessor's complete task/UID/time/environment/secret/network profile.

The worker runs the actual capture and guarded grant kernel. Before owner
connection/COMMIT, it proves grant and future-reader certificate/request capacity
within unchanged limits. Only regenerable SQL/plan and duplicate planned-after
state are omitted from the wire: both regenerate exactly from the complete
capture, match the RO-01 selected planner and compare full hashes. Catalogue,
actual recursive lock witness, source/function proofs, full transactional
before/after equality, confirmed rollback and commit/readback rules are not
reduced. An already-compliant capture skips the grant job instead of inventing
a commit. Reader verification uses the independently authenticated dedicated
reader and RO-01 installed-policy validator, under the original worker wall.

The operator/integration harness then runs the real fourteen-feature FastAPI
lifespan, existing monthly/refresh/multi/book authenticated read routes,
unauthenticated denial and denied review mutations. It needs the separately
reviewed private reader connectivity and current delegated assertions; it
does not create a proxy, fetch a signer/DSN, invoke a helper or deploy an app.
Only bounded status/timing/schema/request hashes are retained, not assertions,
DSNs, response values or source bytes. This is execution-context compatibility,
not independent TI/TLAM/TAI consumer acceptance.

## Outcome and failure matrix

| Observation | Grant effect | Disposition |
|---|---|---|
| Local candidate invalid, no dispatch | No submission | Correct candidate; identity unspent |
| Capture verified | No grant | Derive the fixed grant, or skip it if already compliant |
| Grant fails, rollback confirmed | Rolled back | Stop; distinct future attempt only after reconciliation |
| Rollback/transport unconfirmed | Unknown where write could have started | Inspect only; no replay |
| COMMIT outcome unknown | Unknown | No second COMMIT, reconnect/write or automatic reversal |
| COMMIT known, verification failed | Committed-unverified | Never claim no grant; inspect only |
| Full committed ACL verified | Committed-verified | Independent reader checks still required |
| Authenticated reader/application verified | Preserve actual prior grant effect | Consumer acceptance still `PENDING_RO04` |
| Later reader/app/certificate/protected-state failure | Preserve committed/unknown effect | Stop with safe cause and original identity |

Typed/allowlisted reasons cover configuration, canonical payload, identity,
window, source/view/function/dependency proof, connect/authentication/permission,
locks, time bounds and certificate delivery. Exception strings, SQL, arbitrary
class names, credentials, provider bodies and business/source values are never
diagnostic output. The earliest useful safe cause is retained while commit or
rollback ambiguity dominates. A failed task can still deliver useful sealed
failure evidence; it is not silently promoted to task success.

## Durable submission and inspect-only recovery

A programme lock serializes current operations. Owner-only create-exclusive
files, file **and directory** fsync and a complete hash-chained journal precede
each create/run ambiguity boundary. Neither existing operation roots nor intents
are reset, resumed, overwritten or deleted. Unresolved prior work blocks a new
write, including a different operation nonce. No automatic create/run retry,
grant retry or reverse action exists.

Before allocation and every create, the backend makes two stable, bounded,
exhaustive inventories of the fixed `mf-reader-internal-` DEV programme prefix,
not just the proposed nonce. All cloud jobs/executions must exactly equal the
resolved durable journal seals, including deterministic progress in the current
operation; the next target must be absent. Extra/missing/changed/pruned records,
unknown prefix names, truncation (more than 1,000 jobs), unstable observations,
lost local history, `MUTATION_OUTCOME_UNKNOWN` or `COMMITTED_UNVERIFIED` stop a
new write. A new nonce or host cannot silently evade existing cloud evidence.
Settled lineage includes full validated inventory hashes and terminal event
hashes; merely appending a local “known” string is insufficient.

This is explicitly **`SINGLE_AUTHORITATIVE_EXECUTOR_REQUIRED`** and
**`NO_DISTRIBUTED_EXCLUSION`**. The file lock serializes that executor, not two
independently administered hosts. Fixed root custody authenticates approval;
the evolving journal remains at the existing fixed executor-owned artifacts
path. No administrator immunity or permanence is claimed. Privileged deletion
of both all cloud resources and all historical evidence is explicitly
**`PRIVILEGED_HISTORY_DELETION_NOT_RECOVERABLE_RO05`**, not an automatic first-run
reset or a safety guarantee. RO-05 owns full backup/provisioning lifecycle.

If allocation succeeds but a final read-only check fails **before any durable
CREATE intent**, the complete journal settles `STOPPED_BEFORE_DISPATCH` with
known no submission and an empty operation inventory. Read-only inspection may
confirm that local fact without pretending cloud absence or consumer acceptance.
The failed operation identity remains permanently spent. A corrected **new**
plan can proceed only at the next approved epoch with exact prior lineage and
fresh complete cloud inventory; no evidence is erased. Incomplete/corrupted
durable storage is different: it stays stopped, never auto-reset as a local
candidate correction.

`inspect --operation-root <original immutable root>` uses a read-only adapter.
It rederives the complete original fixed request from the sealed plan, verifies
job/execution/task identity, original certificate and final protected profiles,
and records a new immutable observation. It cannot call create/run/SQL or issue
COMMIT/ROLLBACK. Zero executions plus a pristine job is only useful when the
durable journal also proves no RUN was submitted; a running task, contradictory
evidence or unresolved COMMIT remains unknown. Inspection never resumes the old
operation or sets a live consumer acceptance flag. A future attempt is distinct
and must meet its own bounded authority/predecessor/fresh-state guards.

## Intentional source compatibility

The existing full44 migration ledger and every numbered migration/SQL fragment
remain unchanged. Three small post-DDL constructors are shared by historical
installers and the complete migrated disposable fixture: current-facts, multi
and book. They require the direct owner in an existing transaction and empty
configuration. Historical default installation behavior is preserved; the
fixture's explicit book-v5 constructor also requires the actual044 ledger pin.
No installed configuration is replaced or projection activated by a constructor.

`RO02_READER_SOURCE_AUTHORITY_20261031.json`/the complete embedded current
descriptor version bind all ten source files, including those three intentional
changes. The old RO-01 nine-file descriptor remains byte-identical. Source/view
admission uses explicit exact authority/profile pairs, never their cross-product
or a runtime-learned hash. Ordinary hosted CI recompiles the three existing
graphs and the populated fixture's complete new graph before its pin is adopted.

The existing diagnostic source-attribution v2 separately recognizes the changed
current-facts file. Its named historical RO-01 mapping and each original
historical attribution dictionary remain intact. Only the current complete
source/version/spec/controller pins are advanced coherently; queries, budgets,
consumed identities and old receipts are unchanged. Finite whole-file test-only
predecessor mappings preserve historical release assertions, reject byte tamper
and do not create another whole-repository freeze or runtime permission bypass.

## Verification and RO-04 prerequisites

Provider-free tests exercise the actual canonical parser, `main()` snapshots of
the real environment mapping, all three stage bindings, create/run ambiguity,
durability/tamper/spent identity, inspection's zero mutation calls and all outcome
classes. Both image smokes execute real parser/main boundaries with synthetic
inputs, not just `--help` or source hashes.

The hosted disposable fixture applies the actual44 numbered migrations once,
uses genuine registries/policies and the shared post-DDL constructors, publishes
synthetic monthly/refresh evidence, and seeds the exact committed multi graph
without claiming an activation transition. Multi snapshots and book batches are
explicitly empty, not fabricated source acceptance. It then runs actual worker
entrypoints/capture/grant/reader, real full application startup/reads, denied
reader mutations, post-adoption portfolio/distribution/IDCW publication/replay,
unchanged generator/admin capabilities and a distinct no-op operation. Only
provider/connection transport and the existing disposable-OID test isomorphism
are substituted; SQL/ACL/guard/application semantics run on real PostgreSQL.
Catalogue call counts and startup/read timings compare against the same RO-01
application; there is no cached verdict, new SLA or increased timeout.

Final readiness requires the final head's full local checks, original native
and image gates,21 contracts, dependency audit and credential scan, plus the
actual new source compilation and complete chain. Current results and residual
blockers are recorded in the PR; native collection alone is not a pass. No local
PostgreSQL/Docker, DEV action, consumer test or deployment is claimed here.

RO-04 must separately reconcile verified merged/released source, immutable images,
fresh protected state/inventory, prior operation effects, bounded DEV no-freeze
plan authority and private authenticated application connectivity. RO-03 owns
remaining enforceable release gates; RO-05 owns full lifecycle/new-schema and
other-creator coverage. Existing owner-specific defaults cover only enrolled
schemas and that creator; no global read-all shortcut or permanence claim.
