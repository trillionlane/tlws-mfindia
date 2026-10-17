# FS-09 API read safeguard — accepted DEV rollout, 2026-10-17

This closes the separate read-cost gate after
[SBI source acceptance](FS09_DEV_ACCEPTANCE_2026-10-17.md). No source migration,
source publication, raw data correction, UI or PROD deployment was performed.

## Exact delivery chain

- PR: [#89](https://github.com/trillionlane/tlws-mfindia/pull/89).
- Tested head: `a705dc1f0b438ce6a52cd48ddb680fa6c29892c0`.
- Normal squash merge: `2f173e4abc4b05e2c4c20c69d96029c29646373d`;
  the merge tree exactly matched the tested head.
- Head Verify: `39038807903`, 1,743 passed / one skipped, all audit and image
  checks successful. Main Verify: `39041595030`, same 1,743 / one skip and all
  remaining checks successful. Image-only preparation: `39041618062` passed.
  Generic DEV deployment `39041595021` was skipped; automatic deployment
  remains disabled.
- App image: `asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia@sha256:2b8de1ab8d5cccce81df8bcabbcadc0cb3b83f07299eed4e098e48590c0fb0c2`.
- Tools image: `asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev/mfdataindia-review-tools@sha256:6e9c445b86c33f0be8a746615ebc20a03c2b37790f72fddf50ea98bb32cb7f98`.
- Serving revision: `tlws-mf-data-dev-dev-fs09-page-261017a`, exact merge SHA,
  100% traffic with normal `latestRevision: true` semantics and no remaining
  temporary tag.

Acceptance certificate:
`27b585cb2094b3cb01365fbd8cce2b77e9ab09133cdf09b2a26f2db0e5c91eaf`.
Manifest:
`139f5f4e1516cecf39011b220707dfa29055f9bddf3a0b70701bc02720d3102e`.

## Actual private HTTP, not the earlier SQL-only proof

Candidate execution `mf-core-candidate-dev-paged-261017a-5jppd` and serving
execution `mf-core-live-dev-paged-261017a-gvfxc` each certified all 19 core
routes / 22 contract responses, the 14 additional detail/batch responses and
the three explicit performance paths. All existing response/identity and
`no-store` checks passed.

| Actual request | Candidate slowest sample | Serving slowest sample |
| --- | ---: | ---: |
| Two-fund name page, live/in-scope filters off | 0.424 s | 0.151 s |
| Two-family name page, live filter off | 0.256 s | 0.194 s |
| 103174 risk/reward | 0.739 s | 0.671 s |

The fixed readiness budget was five seconds per required sample. Ordinary
HTTP timeouts stayed at 30 seconds. These are finite real samples, not a
load-test percentile or a claim that every globally fact-ranked request is
subsecond. Source/review configuration and current graph, NAV dataset,
source/NAV/audit jobs and schedules, private service identity, network,
resource limits, secrets, runtime flags and IAM stayed unchanged. Anonymous
access returned 404. Zero database writes or source-job changes.

The existing source/review remains at the separately accepted `ff32de0`
image and source registry 2 / current policy 5; API code and source-runtime
acceptance are intentionally distinct.

## Recovery and next-wave prerequisite

Thirty-six local operator tests covered command failures before and after
promotion, traffic restoration before optional lookups, strict anonymous
denial, private identity/IAM boundaries, and rejection of missing, malformed
or over-budget readiness samples. Production data was not used by those
synthetic operator tests. The actual rollout armed recovery before candidate
creation and retained the approved prior revision rather than deleting it.

Kotak must verify both the source acceptance and this independent API
acceptance before its own fresh baseline. Its preflight replaces only the
prior protected snapshot's core spec/traffic with this sealed approved API
transition; NAV/audit, source/review configuration and IAM cannot silently
drift. The source sprint still requires its full restored-clone, actual-source,
recovery, genuine timer and preservation gates.
