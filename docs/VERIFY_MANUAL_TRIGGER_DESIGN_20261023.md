# Manual Verify fallback (CI only)

## Scope and plan

Base: `ef030e2149f4181d95e06ff10db85e0b6151b0b7` (merged PR115).
The existing Verify workflow has pull-request and main-push triggers, but no
run is observable for that merged commit. The cause remains unestablished.
Do not treat an absent run as a running or failed run.

1. Add only an empty `workflow_dispatch` trigger to Verify.
2. Retain both existing automatic triggers, workflow permissions, concurrency,
   both jobs, every step body/order, native PostgreSQL tests, audits, image
   builds and smoke checks unchanged. No new inputs, dependencies or secrets.
3. Add provider-free regression coverage, demonstrate failure before the
   workflow change, then run focused and full local non-PostgreSQL validation.
4. Push a scoped PR and obtain independent exact-head review and whole PR CI.
   Resolve findings before normal merge; no bypass or deployment.
5. Verify the resulting main SHA. Observe its automatic Verify run first;
   if none exists, dispatch Verify once on main and immediately reconcile the
   actual run event, attempt, head SHA and both original jobs. Do not duplicate
   an existing queued/running run. A dispatch is not a rerun of PR115.
6. Accept only complete whole-run success with its own full retained evidence,
   native test inventory, steps/cleanup and exact-main admission. PR CI and
   identical trees do not substitute for this main evidence.

## Manual procedure after this change reaches main

Confirm remote main and the expected exact SHA before dispatch:

```sh
git ls-remote origin refs/heads/main
gh workflow run verify.yml --repo trillionlane/tlws-mfindia --ref main
```

Inspect the returned/new run and require its `head_sha` to equal the freshly
confirmed main SHA. If main changed, the response is ambiguous, or a run was
already present, stop and reconcile; do not blindly dispatch again. Never
substitute a successful run from a different SHA or the old pull-request run.
The new trigger must be present on the default branch for manual dispatch.
GitHub reference: https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow

## Boundaries

Only `.github/workflows/verify.yml` gets a trigger addition. The deployment
workflow and `MFDATAINDIA_DEV_AUTO_DEPLOY=false` are not changed. A main push
may create the existing skipped deployment run; this is not permission to
deploy. No GCP, DB, IAM, provider/source, runtime, image publication or schedule
changes are part of this PR. CI image builds stay ephemeral original checks.
The operational checkout stays at its previously admitted commit until the
new main has independently passed all gates. Existing evidence is immutable.
This change neither establishes the missing-trigger cause nor retroactively
certifies PR115's old merged SHA, and does not complete S1 or FS13.
