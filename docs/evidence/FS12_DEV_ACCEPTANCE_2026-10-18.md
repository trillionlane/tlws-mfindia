# FS-12 — Actual DEV read-only source operations acceptance

This sprint deployed **diagnostic jobs only**, not a source/schema/API revision.
The source/review SHA remains `1d1311cbe17e32493a47a6ab7a4fcd6eb7fdefb3`,
protected core `2f173e4abc4b05e2c4c20c69d96029c29646373d`. Both serving revisions,
their private IAM/traffic, the six-task source job, NAV/audit jobs and complete
normal Scheduler configuration were verified unchanged before/after.

- [PR96](https://github.com/trillionlane/tlws-mfindia/pull/96), merged
  `b560534ed25089bfc2952d2df0be71b283666950`; reviewed and merge tree
  `dadd6b7dacdc0cc81d95f861f2de19a531b21687`.
- Whole [head Verify39113482556](https://github.com/trillionlane/tlws-mfindia/actions/runs/39113482556)
  and [main Verify39114665091](https://github.com/trillionlane/tlws-mfindia/actions/runs/39114665091):
  **1,976 passed, one skipped**, audit, both builds and worker smokes. Generic
  [deployment39114665120](https://github.com/trillionlane/tlws-mfindia/actions/runs/39114665120)
  skipped; auto-deployment remained false.
- [Image-only preparation39114789067](https://github.com/trillionlane/tlws-mfindia/actions/runs/39114789067)
  succeeded, execute skipped. APP `sha256:335f7de44028cb4393cb108937d22c2ef24a8d67e28cc7b38caf49d88eb64d6c`;
  tools `sha256:c32e141b2f85c4f1e59118bec6c7501de7d14ef24dcda9c0ea378746fcfb644e`.
  Both exact Git labels, Linux/amd64 and immutable manifest/config bytes were
  independently checked. OCI configs respectively
  `sha256:05511859a692fcc50e70ba7eba0256d938c84111ac3932eb80040423feb28bd9` and
  `sha256:b9718e566a278f6c7c02b636bf04a7f3d1f03395e34eb2f7dbc019bd976622a1`.

Actual immutable executions, all terminal successful with one task, 1CPU/1GiB,
600s and zero retries:

| Diagnostic | Execution | Inner certificate |
| --- | --- | --- |
| Inventory | `mf-fs12-inventory-261018a-x26v7` | `9374b80fd1f25732f12f0bbdaf7d2bb33a9e559cb232faf145fe79df504c1945` |
| Ordinary Kotak catalogues | `mf-fs12-catalogue-261018a-2mv6x` | `7b5902a00700366369783a9c3774e416fed130d76a6d994fd0b3fe6212f86eec` |
| Independent SQL witness | `mf-fs12-independent-261018a-f5mqg` | `6f5021377f074ed0a51e41f42f5d74fce19a38498f4f637fc9d8616f447efa79` |

The actual authenticated/effective `mfdata_verified_reader` and repeatable-read,
read-only transaction were verified. The existing caller identity was used with
its existing reader secret; the catalogue job mounted **no database credential**.
No IAM grant, data write or human-review event occurred. Seventy-one local
operator/comparator/runtime-verifier tests passed separately from repository CI.

All four sources, eight retained periods, variant/field/reason counts and all
**980 primary gap code/identity memberships** matched independent SQL. Current
populations match their latest successful source receipt. These are variant-
period counts, not family counts or all-AMC completeness:

| Source | Period | Denominator | Usable | Gaps |
| --- | --- | ---: | ---: | ---: |
| ABSL | 2026-09-30 | 192 | 174 | 18 |
| ABSL | 2026-08-31 | 192 | 146 | 46 |
| SBI | 2026-09-30 | 258 | 115 | 143 |
| SBI | 2026-08-31 | 258 | 108 | 150 |
| Kotak | 2026-09-30 | 205 | 41 | 164 |
| Kotak | 2026-07-31 | 205 | 2 | 203 |
| UTI | 2026-09-30 | 208 | 80 | 128 |
| UTI | 2026-08-31 | 208 | 80 | 128 |

This is 863 current variants and 1,726 retained variant-period observations.
Field gaps are a separate denominator; e.g. UTI has 78 accepted reported TER
observations in each month, **zero BER overrides**, not 78 extra usable variants.

Ordinary Kotak September catalogue discovery succeeded. August transport also
succeeded but its original body was unrecognized. Both originals were retained;
no candidate PDF was downloaded/admitted, no retry/challenge/fallback used by
the probe and no known-document authority extended. August remains missing.

Latest genuine scheduled NAV `tlws-mf-data-nav-refresh-dev-nfp7c` and audit
`tlws-mf-data-nav-audit-dev-g8xs7` were re-read, including exact structured
application results: `DAILY_REFRESH_OK` / `NAV_INTEGRITY_AUDIT_OK`, both actual
dataset28. Their legacy normal-job OCI Git provenance remains **unknown**.
The historical 17-Oct12:30 run rejected nine future-dated ETF rows in validation;
no category/as-of exception or ingestion rerun was introduced to make it pass.

Private evidence root `artifacts/mf-fs12-dev-20261018-agbiS7/`; manifest
`fa97914a0bc85845b4acdba2718951096e1e0f210b1e5a7631b51de87cdeaaf8`;
finalizer exit **zero** and sealed `10-readonly-acceptance.json`
`f463a12430e7072940f2c8aa6abe2086b55a383867c29637206d65a7e1ff5a2f`.
[Implementer closeout](https://github.com/trillionlane/tlws-mfindia/pull/96#issuecomment-6248412479)
records the same actual evidence. No credentials or source originals are in Git.

Facts remain `needs_verification`; TER is not BER; adjusted IDCW stays disabled.
FS-13 and new-AMC publication are separate, still-required delivery stages.
