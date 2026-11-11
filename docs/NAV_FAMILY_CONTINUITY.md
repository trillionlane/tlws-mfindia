# NAV and fund-family continuity

The November 2026 Zerodha CASE rename exposed a mismatch between scheme identity
and name-derived family keys. AMFI kept the 25 scheme codes and ISINs. The old
loader moved the variants to new keys and the builder left 25 old family rows
with no variants, causing the full NAV and catalogue transaction to roll back.

## Recovery

Run `scripts/recover_nav_gap.py --from-date YYYY-MM-DD --to-date YYYY-MM-DD`
with the existing verified reader connection to calculate the missing in-scope
keys, exact AMFI source hashes and current dataset version. It reads each of the
three scheme-type reports once. Windows are limited to 14 days.

The DEV write uses the existing ingestion connection. Reuse the exact dates,
three ordered hashes, row count and version from the reviewed result with
`--apply`, `--expected-version`, `--expected-rows` and `--source-sha256`. Any changed input, competing
operation, conflicting source observation or non-insert outcome fails closed.
All missing NAVs and their existing calculation/summary updates commit in one
transaction. A successful replay plan shows zero missing keys. Check the
committed daily counts and authenticated stats and NAV routes after application.

## Stable identity and automatic rename

Install the optional schema using `scripts/refresh_daily_v2.py --install` with
the existing MFData owner connection and the certified source. The additive
migration seeds each scheme-to-family binding from the current consistent
catalogue. The existing family UUID, canonical slug, tag associations, NAV
history and public table shapes remain intact.

The existing NAV job can then run `/app/scripts/refresh_daily_v2.py` from the
certified image using its current ingestion identity and FULL mode. Preserve
the scheduler, task count, retries, timeout, DSN and Cloud SQL binding. The
existing legacy deployment workflows select the v1 command. A continuity
release must retain the v2 command when replacing the job image. Verify it on
the resulting job definition.

The resolver accepts a one-to-one move only when the whole family membership,
issuer, plan, option and nonempty exact ISIN slots match. It rekeys the existing
family and variants in the same catalogue transaction, keeps the stable UUID
and URL slug, and records the former name, key, slug and source receipt in
`mf_identity.family_renames`. A duplicate code, conflicting identity or
potential split or merger holds the catalogue transaction. The approved
Zerodha CASE issuer spelling resolves to the existing Zerodha AMC identity
based on issuer Addendum 21/2026–27. Other issuer changes require verified
continuity evidence before adding an alias.

## Independent freshness

The v2 job validates the official NAVAll feed once. It first commits prices
for verified existing in-scope scheme identities, preserving the published
catalogue and FULL snapshot ledger. It then validates and commits the
catalogue and any newly verified scheme prices in a separate transaction.
Both use the existing NAV operation lock. A catalogue hold is durably recorded
and produces a failed job result even when verified prices committed.

`scripts/refresh_daily_v2.py --status` reads NAV date and dataset version
from the existing summary and the latest committed FULL catalogue date from
its receipt ledger. `/api/stats` continues to serve the NAV date. The private
hold records identify catalogue reviews. Replay of an already published
snapshot with the same date and source hash preserves the receipt and version.

Before completing the DEV cutover, verify that the 25 IDs and slugs match the
baseline, the catalogue has zero orphan or missing families, older NAV rows
remain intact and a scheduled execution advances both applicable freshness
signals. If a run fails after committing prices, inspect the summary and the
catalogue receipt before deciding on any replay.
