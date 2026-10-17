# FS-10 immutable-policy collision caught in the restored clone

This is an unaccepted rehearsal record, not a live Kotak deployment. No existing
policy, source record, constraint or history was deleted or overwritten.

## Actual failure boundary

PR #91 merged at `9a78fc5f0a5de1a470dbd6264d80c30832398de7`. Head/main CI each
passed 1,849 tests with one skipped, audit and all image checks. Immutable app
and tools images were verified against that SHA. The actual normal 18:30 NAV
execution committed dataset 26 and 306 additional NAV rows; the 18:40 audit
completed 14,385 assessments for that same dataset without changing its source
manifest. Prior source populations remained current. A valid lossless read-only
observer certificate bound that state before the new rehearsal.

The new attempt sealed manifest
`d0bc3101a1ca76497534036d8276b01cd06f7d10ca19dfb2bf31b27f86a044b8`.
Fresh backup and complete prior-layer baselines passed. Restored-clone execution
`mf-book-clone-dev-kotak-261017b-s6j6f` then failed at the policy insertion:
`policies_policy_id_policy_version_key` rejected a different document reusing
policy version 6. The earlier failed attempt had legitimately retained its
registered v6 candidate even though that candidate was never activated.

The rehearsal stopped **before private service staging or any live schema/source
operation**. A separate actual-execution check verified the exact tooling image,
program bytes, dedicated generator identity, network, retry/task bounds and
environment, with no live database credentials or mounted volumes. The temporary
exact-backup read grant was removed and its absence verified. Backup, failed
clone, original archives and all earlier evidence remain retained.

## Append a revision; do not repair immutable history

The registered v6 document is frozen verbatim with hash
`e8ce5b4ef4bca2ed59901a5df109d7ac191bf176e1a24e9a35cda92e2458c566`.
Its original source-configuration hash remains
`4a0ccc6fc01bacef173bc2631d5122a78d6dabc922c0259d7f83e0b1e8405d09`.
It remains a recognized historical policy, not an activated current policy.

The revised Kotak policy uses a separately allocated v7, records source-registry
version 3 explicitly and identifies the superseded unaccepted candidate. Its
accepted predecessor remains the source-v2/policy-v5 graph; the failed candidate
does not become a required activation. UTI must allocate v8 rather than reuse v7.
Replay recognizes the complete frozen book-policy set without inferring source
wave numbers from policy revision numbers.

Revised policy-v7 hash:
`4e9edd71a73c781603653164de24227b73f09fa2c369d8cf8b7ad68dd59c6fe3`.
Source-v3 configuration remains
`addd97f4c2acbf7a66b72ca2c0ffa61b7bae5242f69c36c8b8ed7f2bbb3eec7f`.
The accepted source-v2 configuration/policy and all earlier parser/source/SQL
bytes remain unchanged. Database uniqueness remains enforced. No policy is
updated in place and no version is freed for reuse.

## Required evidence before acceptance

A dedicated PostgreSQL regression seeds the actual frozen v6 candidate, installs
and activates the revised policy, checks both immutable documents, and performs
recovery twice while proving original source rows unchanged. The complete
repository CI and fresh exact-image restored-clone/live-source/private/core
certification/recovery/timer/preservation chain are still required. This document
does not certify a successful live rollout or authorize any source correction,
public access, UI change, deletion or production deployment.
