# FS-10 — Kotak DEV available-period acceptance, 2026-10-17

This records actual source/enrichment acceptance, not PROD, UI, human
verification, consecutive-month coverage or whole-AMC completeness.

## Accepted runtime and real executions

- Source/review SHA: `72cef0d2e93e2ebffb24b2d2e55c1ba0735b7381`.
- App digest: `sha256:c4eeb6a76b838cc19f95c83f1e1c1512168b7d4362d616bb26778ac08da86392`.
- Tools digest: `sha256:55450521f9b80eeaa85f63744f2b8f9d575818cf533e81d5cc08c642f181aa7e`.
  Both are immutable `asia-south1-docker.pkg.dev/trillionlane-dev/tlws-mf-data-dev`
  images; OCI configuration/source identity was independently checked.
- Registry 3 configuration:
  `addd97f4c2acbf7a66b72ca2c0ffa61b7bae5242f69c36c8b8ed7f2bbb3eec7f`.
- Policy 7: `4e9edd71a73c781603653164de24227b73f09fa2c369d8cf8b7ad68dd59c6fe3`;
  accepted generation 22. Previously registered immutable policy 6 is retained.
- Exact-image restored clone: `mf-book-clone-dev-kotak-261017d-lmlgf`.
- Manual source execution: `tlws-mf-official-facts-refresh-dev-cw9gk`.
- Genuine Scheduler execution: `tlws-mf-official-facts-refresh-dev-5fk8n`,
  started 21:46:05 UTC and completed 21:56:27 UTC, all five tasks successful.
  Creator: `tlws-mf-data-scheduler-dev@trillionlane-dev.iam.gserviceaccount.com`.
- All originals' immutable generations, lengths, SHA-256 and MD5 were checked;
  independent visually reviewed current/older canaries matched. Private reads,
  all 19 core routes/22 contract responses, actual recovery, replay and
  reactivation passed. The full coordinator subsequently exited successfully.
- Daily source schedule restored to 09:00 Asia/Kolkata. Protected core/NAV/audit
  runtime, schedules and private IAM remained unchanged.

Manifest: `ef2773944910de5a541a42f6dab0c8467f359e6f22b80cb5a02893c3974abf93`.
Acceptance: `4a287fcbc5b6e72fbbf0cd7a5b6cf2f6ff2f998d452435dce87d3cc2809030bf`.
Level: `DEV_KOTAK_AVAILABLE_PERIODS_ACCEPTED_NOT_CONSECUTIVE_MONTHS`.

## Full preservation and retained failures

Fresh full before/after backups and restored-clone/live row seals preserved
14,405 original funds and 4,248,805 original NAV records. Every prior-layer row,
the entire prior-AMC projection and human ledgers were preserved. Source-row
corrections/deletions and human verification events: zero. No UI or PROD work.

- Before dump: `ee0b2ff1fb85332aba1520450e95599b8e94c5a30c337ce2e28a786ba764b786`.
- After dump: `629eafc74aaaa8ae6277d56f9973642b8e3e760b7ff32355bc63951c19f98923`.
- Final complete prior-layer seal:
  `38972550851c8454501d26528c4f41b87ea5bbd3f7ce568be963485d1c6f1d77`.

Earlier failed catalogue, policy-revision and restored-clone timeout attempts
remain retained, not overwritten or counted as acceptance. Policy revision
numbers are now allocated independently of source waves, preserving the
already registered v6 document byte-for-byte. Diagnostic restored-clone runs
were not substituted for acceptance. The final fresh chain used the original
SQL/driver, 2 CPU/4 GiB/900-second clone bounds and full checks without tuning
or raised timeouts; only a stack-location heartbeat was added for visibility.

## Actual coverage and provenance

| Actual reporting date | Retained variants | Usable | Gaps | AUM | Regular BER | Benchmark | Managers | Risk |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-09-30 | 205 | 41 | 164 | 35 | 33 | 21 | 27 | 36 |
| 2026-07-31 | 205 | 2 | 203 | 2 | 2 | 2 | 2 | 2 |

The July one-pager is not an August book despite its URL. August remains
missing. Catalogue discovery was unavailable; the separately reviewed,
exact-hash public September document route is explicitly labelled, narrowly
dated and expires before November. No challenge was followed or bypassed.
See [the retained discovery/recovery record](FS10_CATALOGUE_DISCOVERY_RECOVERY_2026-10-17.md).

Usable does not mean every field complete. Observations remain
`needs_verification`; adjusted IDCW analytics remain disabled and
`all_amc_data_complete` remains false. Earlier ABSL/SBI evidence and projections
are preserved. The running core API remains the separately certified PR #89
revision, not the source/review SHA; source acceptance is not a consumer-UI
acceptance claim.

## Additional actual core-value witness

After source acceptance, read-only execution
`mf-core-facts-dev-kotak-261017d-b-gbnrx` checked seven independent source
samples through fourteen actual detail/batch responses on the running PR #89
core service. Every supported original-source value matched, detail v2 objects
validated, responses were `no-store` and below five seconds, anonymous access
was denied, and the protected runtime remained unchanged. No database secret
was mounted; data writes were zero. Certificate:
`1ffe7ec2e4423d5f91565315360db2697494adad61612c2da33b219745144eda`.

The first additional read witness failed because its observer incorrectly
required the compact batch contract to include the detail-only v2 quality
objects. That failed read-only attempt is retained. The correction preserved
strict detail validation and independently compared all batch identities and
supported values; no API contract or source data was changed to pass it.
