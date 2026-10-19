# FS13: version-bound preservation and short activation

Status: approved design scope; implementation and DEV acceptance are separate gates.

## Why change the protocol

The D restored-clone rehearsal failed before live changes: a selected-value
preservation query exceeded the unchanged five-second owner statement limit.
Whole-population original-query equivalence had passed, but that is not rollout
acceptance. Three subsequent read-only query alternatives matched exactly and
did not provide a meaningful speed improvement. Do not rerun A–D or reuse their
backups. The user approved a preservation/activation protocol redesign rather
than further speculative query tuning.

## Finite design

For source-method revisions with the **same accepted projection definitions**:

1. Require an idle caller connection. Never implicitly commit/roll back caller
   work; the initial owner/isolation lookup closes before preparation starts.
   Prepare on a separate owner connection in a repeatable-read, read-only
   snapshot, using the existing inspection limits (300-second statements,
   420-second transaction). This is not an extension of a writer transaction.
2. Evaluate the installed old graph and a statement-local candidate graph. The
   candidate reuses the exact sealed SQL definitions; only the active
   configuration relation is substituted with the proposed next policy row.
   No installed view, source table, policy or persistent cache is changed.
3. Compare whole prior-AMC financial rows and provenance multisets, all supported
   revised-AMC selected and dated primary/TER cells, and independent raw/canonical
   sample references. Bind both complete proof seals and the validation summary
   into the immutable transition; retain the complete independent source and
   projection captures in the existing rollout evidence workflow.
4. Bind that evidence to the database instance, exact prior/source configurations,
   reviewed request, code/policy/projection pins, complete dependency graph and
   every visible base-table row version. A dataset version alone is insufficient.
   Row-version hashes use table OID/storage identity plus ordered `(ctid,xmin)`;
   inserts, updates, deletions, truncation and update-then-restore invalidate it.
   These are ephemeral activation guards, not financial derivations or durable
   business identifiers. Vacuum/rewrite/restart may conservatively invalidate
   preparation and must never be ignored.
5. Enter the existing READ COMMITTED owner transaction with the existing shared
   operation lock and unchanged 5s statement/5s lock/15s transaction limits.
   Take NOWAIT SHARE locks on every discovered base table and ACCESS SHARE locks
   on the dependency views. Rediscover the closure and compare every binding.
   This blocks even direct SQL writes that do not honor the advisory lock. Any
   already-running writer, stale input, changed graph, unsupported dependency,
   row-security/context drift or timeout rejects before publication.
6. Append the sealed transition and next configuration atomically. Reuse—not
   reinstall—the identical accepted views. No full evidence query runs while
   holding the publication lock. A command failure rolls back both inserts.
   Owner callers cannot submit a certificate to skip preparation: the activation
   entry point creates and consumes it internally.
7. Keep exact known-prior recovery and replay, independent post-activation
   original-query proof, all sixteen rollout gates and the extra real API witness.
   Recovery must not depend on recreating a failed candidate or rerunning its
   expensive checks.

The preview is permitted only for a registered source-method revision with
identical prior/target pinned projection SQL and sealed core/selection graph.
New projection semantics, foreign/partitioned/inherited tables, RLS, custom view
functions, or unhandled configuration dependencies fail closed. They require a
separate design, not an approximation. Historical source1–4 activation retains
its existing implementation.

## Failure and concurrency acceptance

| Event | Required result |
| --- | --- |
| Wrong prior hash, source registry, policy, reference or SQL pin | No publication |
| Any input commit after snapshot, including no dataset-version increment | Fence rejection |
| Concurrent source, identity, NAV-derived baseline or configuration writer | Busy/rejection; no lost update |
| Direct SQL writer without advisory lock | SHARE lock excludes it until commit/rollback |
| View/schema/context changes after preparation | Closure/metadata mismatch; no publication |
| Candidate loses an old selected, dated primary or TER value | Preparation fails |
| Candidate disagrees with independent original or canonical reference | Preparation fails |
| Failure between transition and configuration insertion | Both rolled back |
| Failure after configuration insertion but before return | Both rolled back |
| Activation replay | Rechecked current evidence; no duplicate publication |
| Recovery/replay after later source failure | Exact sealed prior graph and authority restored |

Tests must include actual multi-connection PostgreSQL races, complete fixture
population proof, original-vs-preview equivalence after real activation, timeout
settings, role restrictions, malformed/drifting dependencies and injected command
failures. Synthetic tests are not actual source coverage. Full PostgreSQL18 CI,
audit, both images and every smoke remain mandatory.

## Non-goals and rollout

No raw correction/deletion, identity merge, policy/formula change, installed-view
change, migration, IAM expansion, UI, PROD, database tuning or resource/timeout
increase. Source5/policy11/SQL044 pins remain fixed. TER is not BER; adjusted IDCW
remains disabled; observations remain needs_verification.

Push, explicitly labelled implementer self-review, complete exact-head CI,
normal exact-head squash merge and identical tree, complete main CI, image-only
preparation and strict immutable provenance precede a wholly fresh safe-window
rollout. A–D backups and failure evidence remain untouched. Merge and image
preparation do not establish serving state or acceptance. EXP01 starts only after
actual FS13 acceptance, additional core witness and reviewed Git closeout.

## PostgreSQL deparser regression repair

Exact-head Verify39239640731 at `4aeca66` failed six focused cases at the same
candidate query, while five focused cases passed. The full remainder and image
gates therefore did not run; this is not passing CI or rollout evidence.

PostgreSQL's `pg_get_viewdef()` qualifies the unaliased latest-generation column
as `configuration.generation`. The initial preview renamed its FROM relation to
`activation_configuration` but left that qualifier unresolved. Preserve the
original exposed name by naming the statement-local candidate CTE
`configuration`. Both fully qualified live relation references are still
substituted; existing explicit aliases, implicit column qualifiers, quoted text
and every policy expression remain unchanged. No general SQL parser or broad
column-name substitution is introduced.

Add an exact pure deparsed-query regression and a real PostgreSQL
create-view/get-viewdef/preview round trip in a disposable database. The latter
must return only the candidate row for both higher and lower candidate
generations and leave the live rows/results unchanged. Retain all existing
complete-population, concurrency, preservation, atomicity and recovery tests.
Require whole CI again on the repaired exact head; no source request, backup,
live DDL, installed view, migration, data, policy or resource-limit change.
