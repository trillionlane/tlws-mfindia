# FS-10 catalogue-discovery failure and preservation recovery

This records an unaccepted DEV attempt, not live Kotak acceptance. Backend/data
only. The original attempt, backup, restored clone, evidence and immutable
archives are retained. No UI/PROD work or source correction occurred.

## What passed and what failed

PR #90 merged as `0414e06bae7740e054f3213274024aba16e73674`; its tree matched
the green head. Head CI `39043196672` and main CI `39044832585` each passed
1,832 tests with one skipped, audit and image checks. Image preparation
`39044837914` passed; generic deployment `39044832518` correctly skipped.

The controlled attempt sealed manifest
`087d7e52d5544e386e009402f2debb220ba41c9ca175700363c333612918ec8d`.
Fresh backup, complete prior-layer baselines, an actual-image restored clone,
private staging, prior-compatible HTTP and restricted installation passed.
The clone covered all 205 Kotak variants, fetched real sources, verified 65
PDF/XML archives, original/prior-layer/prior-AMC preservation and actual
recovery/replay/reactivation. It mounted no live database credentials.
Prior-compatible certification checked 22 private metadata and 20 core responses.

The subsequent actual five-task execution
`tlws-mf-official-facts-refresh-dev-dj2jh` completed with four successful tasks
and one failed Kotak task. The existing SBI and ABSL receipts were unchanged.
Kotak rejected both expected catalogue acquisitions and retained only the valid
July original; it refused incomplete two-period publication. No acceptance
certificate was issued and the current Kotak projection was not activated.

## Source-access evidence, without bypass

Same-image, same-DEV-network database-free diagnostic
`mf-kotak-catalogue-probe-261017a-vcfz7` observed HTTP 302 for both catalogue
requests. A single additional metadata-only probe
`mf-kotak-redirect-probe-261017b-b8kft` identified the challenge hostname
`validate.perfdrive.com`. Neither probe followed a redirect, solved a challenge,
used cookies or retried automatically. No query tokens were disclosed.

The separately already-reviewed ordinary official CDN URL was accessible in
`mf-kotak-known-document-probe-261017c-52mr6`. Its 22,132,740-byte PDF matched
SHA-256 `fba08af16c51879d060c867759126b906698a54a2ff237fa7803cac11d760d36`
and existing immutable archive generation `1792252586232266`. This probe could
not publish facts and had no database credentials.

## Recovery and complete preservation

Both installed recovery handlers completed with empty failure lists and no
deletion. The subsequent independent read-only observer verified the restored
prior private service/source configuration and the separately accepted core,
NAV and audit configuration. It compared complete original preservation and
every pre-existing layer row; human-review ledgers were unchanged. This is
stronger evidence than the failed run's own `source_rows_changed: 0` claim.

Recovery preservation certificate:
`2f518efd455688c130198fbf6eb1ec74862baff1adc6789cde28b19316c6b97d`.
Full row-seal certificate:
`aefa9440d04938858cccedc50df63dd7f5f49425377b5281dacb1ab2f646799a`.

## Narrow known-document policy, not successful live discovery

The follow-up uses the actual retained 545-byte September catalogue original,
SHA-256 `2c9b82cb4584bd88506d906ba0e9330ea3714039a4690de881115f94dc1de111`,
only after ordinary acquisition itself fails. It is not reserialized JSON or
a fabricated fresh HTTP response. It cannot replace an acquired contradictory
catalogue. Only the original reviewed PDF hash can use this route, only for
September and from 2026-10-17 until before 2026-11-01 UTC. A changed PDF is
archived before rejection, never decoded/published under that authority.

The sealed source policy, batch, each fund observation and coverage record
identify `reviewed_known_document_not_live_catalogue`. The run must retain its
discovery-unavailable warning. SQL validates the exact catalogue/PDF pins,
policy/provenance, per-fund consistency and observation window. August remains
missing; July remains a single-fund historical disclosure. This is not proof
of latest catalogue discovery, consecutive whole-AMC books, human verification
or adjusted IDCW eligibility.

Earlier accepted registries/policies remain byte-identical. The failed,
unaccepted v3 authority and its receipt remain immutable history; a new sealed
v3 configuration/policy records the revised implementation. A fresh full
backup/clone/private/source/certification/recovery/timer/preservation chain is
required; earlier rehearsal does not certify the changed implementation.
