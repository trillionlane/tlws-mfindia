# RO-03 release admission

Status: source implementation for independent review. No deployment, grant or
consumer acceptance is performed by this control.

## Gap map

Reused without replacement:

- `scripts/ci_risk_policy.py` remains the authoritative change classifier and
  requirement selector. Its docs, targeted and full modes, exact Git plan,
  supported-event restrictions and step inventory remain authoritative.
- The two original verification jobs keep their current native PostgreSQL,
  contract, audit, credential and both-image gates. Their final per-step gates
  still fail closed.
- `scripts/run_ci_tests.py` and `scripts/run_ci_targeted.py` retain exact test
  inventory/partition evidence. No fixed test-count threshold was added.
- The run/attempt/head rules from current source-packet provenance remain the
  model for attempt isolation. RO-01/RO-02 source and journal behavior is not
  changed.

Missing enforcement supplied by RO-03:

- One `Release admission` aggregate always runs after the classifier and both
  original jobs. It emits a hash-bound machine packet and a human summary even
  for a rejection; a rejection leaves the aggregate check red.
- The packet binds repository name and numeric identity, workflow name/path/ref
  and workflow SHA, run ID and attempt, event/ref, candidate/head/base/checkout,
  the complete classifier plan/binding, selected checks, accepted exclusions,
  observed job results and concrete blockers.
- PR source verification is distinct from automatic MAIN release eligibility.
  Only a successful `push` verification for the exact current `main` commit in
  full mode can be a release candidate.
- The consumer reads the exact attempt-specific job inventory and its one
  current-attempt artifact. Missing, neutral, cancelled, timed-out, mixed or
  stale evidence is rejected. A failed-jobs-only rerun is deliberately not
  composable with successes from another attempt: rerun all jobs for one
  complete admissible attempt.
- DEV application deployment and verified-review image preparation/execution
  cannot reach cloud authentication until the shared consumer admits the exact
  automatic MAIN packet. Automatic DEV deployment remains controlled by
  `MFDATAINDIA_DEV_AUTO_DEPLOY`; RO-03 does not enable it.

The snapshot-restore workflow builds a dedicated recovery image under its own
typed object/generation and recovery authority. NAV audit/refresh workflows
reuse an already digest-pinned deployed application image. They are not release
or promotion entrypoints for the internal reader and are unchanged here.

## Gate truth table

| Evidence | Source verified | Release candidate eligible | Consumer mutation |
| --- | --- | --- | --- |
| Successful PR docs plan, only permitted exclusions | yes, docs scope | no | blocked |
| Successful PR targeted plan, only permitted exclusions | yes, targeted scope | no | blocked |
| Successful PR full plan | yes, full scope | no | blocked |
| Manual `Verify` full run | yes, manual scope | no | blocked |
| Automatic MAIN full run; all exact-attempt gates green; SHA still current | yes | yes | may continue to a separately authorized consumer |
| Required job/step missing, skipped, cancelled, timed out or failed | no | no | blocked |
| Wrong repository/workflow/event/head/base/checkout/run/attempt | no | no | blocked |
| Failed-jobs-only rerun with older successes omitted from current attempt | no | no | blocked; rerun all jobs |
| Correct packet but MAIN has advanced | historical success retained | no | blocked as stale |

Every packet states `permission_enabled=false`,
`consumer_acceptance=PENDING_RO04`, and `deployment_performed=false`. CI
eligibility cannot set any of those live states.

## Consumer sequence

1. An automatic MAIN `Verify` run produces the aggregate packet after all
   required gates have finished.
2. A release workflow starts with a read-only admission job with `actions:read`
   and `contents:read`; it has no OIDC permission. It checks out the immutable
   `github.workflow_sha`, not a caller's candidate ref, and verifies that clean
   checkout before running admission. Its validator, imported CI policy and
   step inventory all come from that same trusted main-workflow source.
  No candidate checkout or candidate-supplied Python runs in this job.
   Caller-supplied values enter the consumer and deployment contract as quoted
   environment data, never GitHub-expression interpolation into shell source.
3. The consumer fetches the latest run, exact attempt metadata, exact attempt
   jobs, exact attempt artifact and current MAIN ref, then validates them
   together. Run/attempt/workflow/repository identities must agree. Job IDs,
   run/repository URLs, workflow, branch and SHA must match; the four jobs must
   be distinct and successful with all required steps. Job-level `run_attempt`
   is not required by the documented response: it is checked only if present.
   Isolation is supplied by `/runs/{id}/attempts/{attempt}` and its `/jobs`
   endpoint, plus the exact-attempt artifact and packet, never a merged jobs
   inventory across attempts.
4. Only the downstream job has `id-token:write` and release commands. `needs:
   admission` guarantees a rejected/incomplete decision produces zero cloud
   authentication, image publication, deployment or grant calls.
5. Both verified-review operations (image preparation **and** execution) require
   caller expected SHA = admitted output SHA = workflow event SHA before
   candidate checkout, then require that exact clean checkout before tooling
   or authentication. Checkout explicitly uses the admission output; manifest
   validation, image labels/tags and execution use `RELEASE_CANDIDATE_SHA`.
   An event/main race is a rejection, not permission to use another revision.

## Acceptance correction at reviewed head `2b823d4`

The original consumer could execute the candidate's own admission script, and
the rollout's execution mode ignored the admitted SHA. New regressions run the
actual workflow shell against disposable Git: a candidate replaces the validator
with unconditional success, and mismatched admitted/input/event/checkout SHAs
try to reach tooling/authentication. Before correction both candidate scripts
returned success for an invalid run ID; execution mismatches reached tooling;
the validate/execute commands used the event SHA instead of the admitted SHA.
The documented attempt-jobs response (without job `run_attempt`) was rejected.
These are provider-free reproductions, not live release workflow executions.

The correction keeps all historical predecessor digests and assertions. Only
the two current release workflow digests and the current image-guard test digest
change in `RO03_CURRENT_SOURCE_CHANGES_20261101.json`; its pinned digest changes
accordingly. Current tests exercise the changed real boundaries; older release
proofs still receive their exact whole-file predecessor, not normalized bytes.
No producer packet meaning, native/image gate, timeout, grant, RO-01/RO-02
capability, or repository setting is relaxed.

Response-fixture provenance: the projection in
`tests/fixtures/github_attempt_job_documented.json` retains documented fields
from [GitHub's attempt-jobs response](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt).
Tests explicitly substitute synthetic repository/run/job/head bindings and the
required step inventory. A read-only observation of Verify `41334633884/1` on
1 November found job `run_attempt` present; it is still not a portable mandatory
field. Both documented absence and optional hosted presence are covered.

Final-head validation is reported in the PR handoff, not inferred from the
superseded `41334633884` success. PR140 remains unmerged pending acceptance;
no live workflow or RO-04 action is performed by these regression tests.

## Remote enforcement status

Read-only inspection on 1 November 2026 found repository rules for deletion,
non-fast-forward updates and pull requests, but no required status checks. The
source gate and real-consumer wiring are enforceable without that setting;
however, PR merge protection does not yet require the stable `Release
admission` check.

Pending repository-admin handoff: add `Release admission` as a required status
check for `main` and require branches to be up to date. Do not require a dynamic
run ID, job count or test count. No repository setting is changed by RO-03.

## RO-04 handoff

RO-04 still owns fresh live execution prerequisites:

- bind the admitted source SHA to immutable application and tools image digests;
- obtain bounded, current approval/custody for the exact target and operation;
- re-read fresh target state and reconcile prior/uncertain attempts;
- prove the real consumer identity and configuration;
- exercise allowed reader coverage and denied write/DDL paths through the real
  consumer; and
- record actual permissions and consumer acceptance independently of CI.

RO-03 does not provision, deploy, enable permissions or certify a consumer.
