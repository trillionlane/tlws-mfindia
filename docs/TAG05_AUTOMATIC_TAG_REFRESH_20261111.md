# TAG-05 automatic refresh source boundary

TAG-05 supplies `scripts/refresh_fund_tags_after_success.py`, a wrapper for the
two successful core identity flows: the authoritative daily AMFI refresh and an
explicit fund-family rebuild. It executes the existing, byte-preserved source
script unchanged and attempts tagging only after a zero exit and a valid source
success receipt. The hook therefore runs after the source transaction and source
connection have completed. It uses the separate
`MFDATAINDIA_TAG_REFRESH_DSN`; it never reuses or elevates the source principal.

The existing TAG-01 publisher remains the only factual classification writer.
It reads the complete canonical census, binds the source and policy hashes,
retains prior snapshots/assessments, and reports `UNCHANGED` after verifying an
identical head. Revised source evidence, corrections, new funds, or changed
factual policy produce a new traceable snapshot. Every new fund receives all
dimensions; reserved thematic and values dimensions start `not-assessed`.

The post-success hook performs exactly one attempt and no retry. Missing writer
configuration is explicit. Any tagging connection, policy, installation,
validation, lock, or publication failure returns a bounded `FAILED` receipt;
it cannot roll back or mark the already committed source ingestion as failed.
Secrets, DSNs, SQL, source values, and exception text are not returned.

TAG-03 exposure evidence and TAG-04 values screens are derived at read time.
Their portfolio dates/evidence ages, registry hashes, derivation-policy hash,
screen hashes, and activity-registry hashes already change assessment identity
when retained documents, rules, mappings, corrections, or time evidence change.
They do not need or receive a second materialized writer in TAG-05.

This source release does not install the TAG-01 sidecar, create the writer role,
set `MFDATAINDIA_TAG_REFRESH_DSN`, switch a Cloud Run Job to the new wrapper,
enable a schedule,
deploy a service, populate activity/entity registries, or prove authenticated TI
acceptance. Source completion, CI/review, merge, deployment, configuration,
successful runtime receipts, database coverage, and actual TI acceptance remain
separate statuses.
