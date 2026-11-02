# RO-04 admission artifact download correction

Base: `7bc9794b78f04c346c54f9510bf04b3630869ea8`, the normal PR140 squash merge.
This focused correction is for independent review, not merge or live enablement.
Final-head local and hosted outcomes are recorded in the PR description.

## Real provider failure

Automatic MAIN [Verify 41387097399 attempt 1](https://github.com/trillionlane/tlws-mfindia/actions/runs/41387097399)
passed all four jobs: 13 focused cases plus 6128 remainder cases, one existing
skip, 38 subtests, contracts, audit, credentials and both-image controls.
The packet was `MAIN_RELEASE_CANDIDATE_FULL`, SHA-256
`7121d22d34ec1f6a0284c9ecda596d009f0c7e11d12258c46be525607ac374e8`.
That producer result did not establish that the release consumer worked.

The actual trusted-source read-only consumer authenticated to GitHub's run API,
then failed with HTTP 401 at the artifact's Azure Blob redirect. The existing
urllib redirect handler forwarded the GitHub Bearer header to the storage host.
A bounded read-only transport comparison removed that header only at the
cross-origin handoff and received HTTP 200, archive SHA-256
`f5cea79b95ce98deb5ca6d7e3d925f16f53a4f359214fe246e504522efe6b72c`—identical
to the independently retained authenticated artifact. No signed URL, token,
provider error body or secret payload was retained. This comparison was not a
replacement release decision; the original consumer failure remains a failure.

GitHub documents artifact downloads as
[302 redirects](https://docs.github.com/en/rest/actions/artifacts#download-an-artifact).
Python exposes the
[redirect handler](https://docs.python.org/3/library/urllib.request.html#urllib.request.HTTPRedirectHandler)
used here. The regression exercises that actual redirect machinery, not only
the admission helper with manufactured success.

## Minimal correction and unchanged boundaries

The existing GitHub client now builds a dedicated urllib opener. API requests
still start only at the configured HTTPS API origin and retain authentication.
Same-origin redirects retain their API credential; cross-origin redirects remove
Authorization and never reintroduce it on a later hop. Downgrades and URL-embedded
credentials are rejected before reaching the target transport. Existing bounded
redirect behavior remains; HTTP failures do not trigger retries or anonymous
API fallback.

Repository, workflow, automatic-main event, current-main, head/checkout, exact
run/attempt, complete job set, step policy, artifact, packet digest and release
scope validators are unchanged. No workflow, CI exclusion, dependency, runtime
worker/controller, database permission, source record or cloud setting changes.
No consumer is declared accepted and no permission is enabled by admission.

The source-reference inventory found no packaged-source hash authority for
`scripts/release_admission.py`: PR140 introduced it as a new operator script.
It is not one of the historical modified files in the RO-03 inversion ledger.
No existing hash, historical evidence or validator was refreshed or weakened.
Future accepted execution/image bindings must use the corrected merged Git,
not the original failed consumer or an unmerged candidate.

## Regressions and review gate

Before the correction, the new provider-free suite reported 12 failures and
2 passes against unchanged production source. The actual CLI reached urllib's
redirect engine and reproduced the Blob 401. The failures also exposed HTTP
downgrade and URL-credential handoffs. No expected-failure marker is used.

The final matrix covers 302/303/307/308 and scheme-relative downloads; same-origin
authentication; multi-hop credential stripping; unsafe targets; initial API and
Blob failures without retry; inherited loop limits; and tampered packet rejection.
It invokes the real `main()`/`consume()` path with synthetic HTTPS server responses,
including the documented attempt-specific job shape without `run_attempt`.
Only GET is accepted by the wire transport; assertions count requests and reject
any mutation method. Neither test output nor admission receipts contain the
synthetic credential, signed-query marker or provider error body.

Hosted disposable PostgreSQL still supplies the connected capture → grant →
reader/application, mutation-denial, installer and generator/admin coexistence
proof. No PostgreSQL, Docker, shared DEV access or consumer reads run locally.

This PR stays **unmerged** for independent review. After acceptance and a separate
merge instruction, certify the actual new MAIN and exercise its trusted consumer
against the real API/artifact again before image publication. Do not use this
unmerged correction to manufacture a successful release decision for the base.

## RO-04 preparation remains separate

The prepared operation uses the accepted single operation contract, fixed DEV
target and three deterministic capture/grant/reader stages, at most three creates
and three runs, at most one derived grant transaction, and no mutation replay.
Final images, independently custodied history/authority, private reader connection,
short-lived assertions, concrete probe codes/window and actual TI producer-role
linkage must be bound before live approval. The existing no-freeze disclosure and
unknown/committed-unverified stop semantics are unchanged.

The observed TI path still targets an older MFData HTTP producer with a different
DSN reference; its actual DB role is not inferred from its Cloud Run identity.
TI owns its consumer implementation/acceptance. Its open PR273 two-read check is
not a substitute for complete intended coverage or authority to merge/deploy TI.
Repository required-check enforcement remains a separate admin handoff.
