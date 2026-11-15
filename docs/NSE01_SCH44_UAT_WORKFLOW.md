# SCH44 UAT workflow

## Rollout

The checked-in [schedule contract](../config/nse_sch44_uat_pilot.json) specifies
07:00 Asia/Kolkata on weekdays, one task and zero scheduler or task retries.
It is disabled pending rollout review. The existing TLAM collector is a
separate job and remains paused. This job does not invoke it.

Stage the MFData package and capture script alongside a clean, exact approved
TLAM connector checkout with Node, Git and the pinned connector dependencies.
Inject the existing UAT connector environment and private credential references
into this job. The capture entrypoint invokes the existing connector's
`downloadMasterOnce` with `SCH`, which sends the documented
`MASTER_DOWNLOAD` request and retains its transport and quota policy. Keep
the connector and publisher credentials separate. The Node child receives only
its UAT configuration and runtime settings.

The reviewed Cloud Run job takes the following non-secret settings:

```sh
python -m mfdataindia.nse.sch44_pilot \
  --bucket "$PRIVATE_UAT_ARCHIVE_BUCKET" \
  --connector-root "$APPROVED_TLAM_CHECKOUT" \
  --connector-revision "$APPROVED_CONNECTOR_SHA" \
  --capture-script "$MFDATA_CHECKOUT/scripts/capture_nse_sch44.mjs"
```

Use a dedicated UAT archive bucket with uniform access and enforced public
access prevention. Bind the job identity to bucket metadata and create/read
under `nse-sch44/uat/`. Keep delete and overwrite permissions outside the job.
The scheduler identity needs invoke permission only on this job. Confirm the
image, namespace, service account, private storage and connector egress before
enabling either the schedule or database publication. No resources are applied
by this PR.

Every execution creates a private attempt object before calling NSE. Every
completed retrieval gets a terminal receipt. Complete credential-free files are
stored create-only under their SHA-256. For an existing hash, verify the
generation and the original bytes. A changed file gets another object even with
the same export date. A failed request retains a safe failure receipt. When a
storage failure prevents recording the terminal receipt, the existing attempt
is the reconcile-only record. The job does not replay that execution.

The receipt has separate UTC retrieval and provider export date fields.
An undated, valid SCH44 response is a successful capture and records publication
held. A failed or invalid capture cannot advance the snapshot.

## Publish only with an accepted UAT package

Add `--approval` and `--crosswalk` pointing to separately reviewed, private,
read-only files. Set `NSE_SCH44_APPROVAL_SHA256` to the independent SHA-256 of
the canonical JSON approval. At rollout, apply
`sql/optional/048_nse_sch44_uat_publication.sql` through the existing explicit
owner path after 045. Supply a UAT-only publisher DSN through
`NSE_SCH44_UAT_DSN` and bind its exact DEV Cloud SQL instance
through `NSE_SCH44_UAT_CLOUD_SQL_INSTANCE`. The publisher uses only existing SCH
publication privileges and canonical reads. The capture child cannot read
this DSN from its environment.

The approval format is `nse-sch44-uat-publish-v1`. It binds environment
`UAT`, scope `FULL`, a private member scope reference, independent digests for
NSE date and completeness evidence, source date, exact bytes/SHA-256/row count,
crosswalk digest, canonical mapping input digest, connector revision and
expected current snapshot. Retain the referenced evidence for independent
review before configuring the pin. A current export filename or an `ALL`
label cannot independently establish scope. An undated download, missing
approval, changed content, stale canonical input or unresolved in-scope
mappings leaves the last good snapshot active. Approved out-of-scope rows
are retained as withheld evidence.

The existing SCH transaction writes source evidence, mappings, snapshot,
consumer rows and the head together, with one commit. Repeat bytes observe the
original publication and never advance the head again. Earlier export dates
cannot replace a newer head. On an uncertain COMMIT outcome, retain the
operation ID and inspect that original observation read-only. The job never
assumes rollback or resubmits the operation. SCH44 data remains reference-only
and derives no transaction eligibility.

## Production sync proposal

Production access is unverified. The exact approved runtime is configured for
UAT, and a successful UAT SCH request establishes that environment only. Before
proposing production activation, use the production-bound connector identity
and approved egress, confirm the member entitlement for consolidated SCH, and
retain evidence from an authorized read-only production request. A connector
host allowlist does not establish credentials or entitlement. No production
request or configuration change is part of this work.

Before replacing a production snapshot, require the issuer-provided effective
export date and intended member scope, including an authoritative count or
equivalent reconciliation of the complete export. Bind those receipts to the
immutable file hash. Independently review the AMC and scheme-code crosswalk
against a dated canonical catalogue and verify exact ISIN slot and variant
matches. Hold unresolved identities and scope mismatches. Once the approved
package and expected head agree, apply the same atomic snapshot transaction
with a separately reviewed production configuration and publisher identity.
Keep capture cadence and permission to replace the active snapshot separate.
