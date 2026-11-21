# MFData source inventory and NSE production website capture

## Capture-only job

The capture job uses the official Consolidated Scheme Master web form and
lets the issuer's JavaScript submit the configured Member ID. The runtime
accepts the value through Secret Manager as `NSE_SCH44_WEBSITE_MEMBER_ID`.
The website form uses its own session. The eventual production API integration
is tracked in [issue #166](https://github.com/trillionlane/tlws-mfindia/issues/166).

The job records filename generation date and retrieval time separately.
A valid previous-date file is recorded as `CAPTURED` with `PRIOR_DATE`.
The date in the filename establishes generation-date evidence only. Member
scope and effective business date remain unverified. The worker validates the
entire SCH44 layout and rows before saving original bytes. The archive creates
objects with a generation precondition and verifies the retained generation
before reusing a duplicate payload. Every execution gets its own attempt and
terminal receipt, including failed downloads. When the archive itself cannot
be reached, Cloud Run supplies the execution status and the worker returns
an archive error. There is no database connection or publication code path.

The [schedule contract](../config/nse_sch44_production_website.json) specifies
`0 1 * * *` in `Asia/Kolkata` with zero retries and one task. The separate
website image is built from `Dockerfile.nse-sch44-website`. Verify its source
revision, immutable digest, Chromium sandbox support, private archive and
exact website Member ID binding before enabling the schedule. Keep the enabled
Chromium sandbox and issuer-origin restrictions. The archive must enforce public
access prevention and uniform bucket-level access. Use a dedicated job identity
with create-only payload/receipt writes and readback, bucket metadata read, and
secret-level accessor for the exact approved website binding. The scheduler
identity needs only the invoker scope for this job. Keep UAT definitions and its
archive independent.

Record the catalogue/SCH head before activation, then verify the first scheduled
Cloud Run execution by correlating the scheduler dispatch and execution IDs.
Read the attempt, terminal receipt and payload by object generation, verify the
SHA-256 and then observe the catalogue/SCH head again. Distinguish a concurrent
ordinary NAV refresh from a capture effect.

## MFData Admin inputs

The Admin app reads a private `mfdata-admin/v1/overview.json` evidence file
and a separately generation-pinned `mfdata-admin/v1/conflicts.json` file.
Overview records are labelled by observation time and retain unavailable values.
They use actual worker receipts and read-only catalogue counts. The control
plane independently reads current Scheduler states and the latest production
website terminal receipt. Data and approval decisions remain in MFData.

Run `python -m mfdataindia.nse.sch44_admin` against the retained 17 November
production export, catalogue v86 and pinned follow-up queue. The exporter
checks all three hashes and can add Approved List membership only from the
verified comparison and membership artifacts. It emits a private review file,
including named NSE rows and available candidates from both ISIN slots.
The queue remains pinned when new exports arrive. NSE Clearing membership
refers to the collateral list and confers no transaction eligibility.
