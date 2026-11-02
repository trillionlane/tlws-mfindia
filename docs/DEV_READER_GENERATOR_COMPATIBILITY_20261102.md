# Internal DEV reader / existing generator compatibility

Base: `84f6c91976ef6da0a8d8e4dfcd2f42a86a2632d2`.
Status: review candidate; no merge or runtime changes authorized. Final-head
hosted PostgreSQL/image/audit results are recorded in the PR, not inferred here.

## Minimum correction

RO-01 already separates a generator/admin caller's capabilities from the shared
reader's independently verified broad policy. Reuse it unchanged. Do not select
the broad reader profile for a generator, grant new generator powers, or alter
API scopes.

The remaining source-process incompatibility is deliberate: historical source
commands reject before I/O because their process allocation does not authorize
the patched dependency. A blind image replacement therefore cannot preserve the
existing six-task book refresh.

Add one explicitly allocated current entrypoint for that existing DEV workload.
It must bind the existing fixed job/connection/generator, actually installed six-task registry v4,
current installed configuration, unchanged source/financial/decoder policies and
bounded worker bytes, and the already locked patched pypdf runtime. Allocation is
a committed code contract, not an operator-supplied flag or fabricated authority
record. Historical commands and dormant successor decoders remain unchanged and
denied/unallocated. The new allocation does not activate new issuers, URLs,
financial fields, source versions, provider calls or schedules during this PR.

Use the existing runtime orchestration only after this allocation and ordinary
identity/input/installed-policy checks pass. Keep current acquisitions,
transaction/worker/resource limits, receipt/replay checks and source expiry
rules; in particular, do not extend the expired known-document fallback.
Record the patched dependency alongside its unchanged historical decoder
policy: do not relabel a successor extraction with a historical engine hash.

Fresh read-only connections found registry v4 actually installed. Its complete
configuration hash is `6fb6c0c804a1dac20e4e2c4aa25f3cd107ce7c58517164409ae2da52e97869d4`.
The current main v5 default is not adopted by this release. A trusted context-local
`book_sources.select_registry(4)` seam carries the existing version through the
unchanged orchestration and resets on exit, including exceptions. It neither
monkeypatches globals nor changes any explicit historical constructor. The
process allocation verifies the fixed installed v4 before source execution.
The new source-only descriptor records only this intentional Python change;
SQL/view/financial policies, numbered migrations and historical descriptors
remain immutable. No source-method upgrade or shared migration is proposed.

## Verification plan

- Prove the historical command rejects a valid-looking but unallocated request.
- Exercise actual new main/environment mapping and reject malformed, wrong-job,
  wrong-role, wrong-registry/dependency/source and retry inputs before I/O.
- Run representative actual bounded PDF subprocesses with the patched runtime;
  retain malformed/encrypted/oversized rejection and unchanged financial values.
- On disposable hosted PostgreSQL, reuse actual RO-01/RO-02 installation and
  publication fixtures. Adopt broad SELECT, verify generator/admin coexistence,
  authenticated API reads/denials, selected-reader write/persistent-DDL denial,
  installed allocation checks and repeated idempotent reconciliation.
- Exercise the new entrypoint in both existing CI-built images. Preserve all
  original workflow steps and checks; no new CI framework or live image push.

## Regression and authority changes

The base rejects the historical valid-looking command before I/O; the new
supported-allocation regression initially failed because that entrypoint did
not exist. Focused tests now exercise real `main()` with `os.environ`, actual
bounded PDF workers, context reset/thread isolation, dependency/source/config
drift, invalid roles/inputs and reporting failure without replay. The first
full-local attempt found one historical release assertion still reading the
new workflow as its old predecessor (5,633 passes, one failure); it was not
accepted as green. The correction applies a finite full-file inverse before
the original historical hash comparison and retains tamper rejection.

`DEV_READER_COMPATIBILITY_SOURCE_CHANGES_20261102.json` enumerates the ten
intentional existing-file changes and exact whole predecessors; it is a test
adapter, not runtime authority. The new current source descriptor changes only
the `book_sources` Python binding among its ten source files; all SQL and view
profiles remain identical. Old embedded descriptors remain immutable and are
still explicitly tested as historical evidence. No broad-policy, installer,
generator/admin privilege or API authorization logic is replaced.

The added hosted case builds the genuine installed-v4/43-migration graph using
committed constructors, adopts the real broad planner through installation,
then exercises allocated monthly replay and UTI-v4 failed-receipt replay. It
also checks portfolio publication/replay, the existing all-feature admin API
factory, selected-reader startup, actual authenticated feature reads and core
API reads, idempotent reconciliation, unchanged values and generator powers,
and denied reader DML/persistent DDL/elevation. These are synthetic fixture
receipts, not a fresh successful provider acquisition. Existing default-v5
connected-chain tests remain unchanged in meaning.

Persistent schema/relation DDL is prohibited for the reader. Existing PUBLIC
TEMP permission is not revoked by this work; do not describe this as denying
every temporary-object operation. Complete source freshness and all-AMC data
coverage are not certified by access tests. The older source fallback expiry
continues to apply without extension.

## Cutover preparation (not execution)

Enable compatible review/generator behavior before broad ACL adoption. Then
reconcile actual current ACLs, review the exact derived SQL, and change only the
producer connection to the existing dedicated reader. Any genuinely missing
secret-level accessor binding is conditional and separately enumerated in the
same private proposal. Do not alter the old application role or TI configuration.
Public PR documentation excludes private runtime credential-reference details.

Uncertain database/transport outcomes stop without mutation replay. A serving
connection switch may be reverted to the captured previous revision/configuration
only under the reviewed recovery action; successful reader grants are not
automatically revoked. Actual partner acceptance requires a normal signed-in DEV
browser and belongs to the later approved execution, not CI.

The compatibility checkpoint is the allocated entrypoint's `--check-installed`
mode in the existing job, before and after the ACL transaction. It verifies
direct generator identity, installed v4, current ACL guards and a synthetic
bounded PDF, without acquiring sources or publishing business rows. It does
not certify later scheduled provider availability. The normal allocated path
retains six tasks, parallelism one, zero retries, 600 seconds, one CPU and 2 GiB.

After a known grant commit, never restore the incompatible legacy guards as an
automatic recovery. Keep the compatible generator/review behavior and inspect
ACL state read-only. A core serving-connection rollback is distinct from
revoking successful grants; no automatic grant reversal is assumed. No update
to TI configuration, the former application role, schedules or source records
is included in this PR.
