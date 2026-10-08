# API performance programme

## Versioned API contract

`contracts/mfdataindia-openapi-v1.json` is the reviewed machine-readable contract for all 19
private JSON GET endpoints. `scripts/export_openapi.py --check` fails CI when the FastAPI route or
parameter schema changes without a reviewed contract refresh. TrillionInsights pins this artifact;
MFDataIndia remains the source of truth and does not expose its legacy HTML routes through the
platform integration.

This document records the measured DEV baseline and the finite acceptance gates
for API performance work. It is not a production-readiness claim.

## 2026-10-08 DEV baseline

The live revision sampled during diagnosis was `tlws-mf-data-dev-00005-bqb`
with source SHA `9a0545498fba7118c3b110305e57e2d2a5790b25`. Cloud Run request-log samples
showed:

| Endpoint group | Samples | p50 | p95 | Maximum |
|---|---:|---:|---:|---:|
| `/api/stats` | 24 | 1,233 ms | 5,145 ms | 5,496 ms |
| `/api/movers/categories` | 12 | 681 ms | 2,096 ms | 2,096 ms |
| `/api/funds` | 18 | 85 ms | 774 ms | 774 ms |
| `/api/funds/{code}` | 13 | 48 ms | 100 ms | 100 ms |
| `/api/funds/{code}/nav` | 17 | 24 ms | 37 ms | 37 ms |

Database CPU stayed below 23% and memory near 50%, so instance resizing is not
the first remediation. The dominant database costs were the exact full-table
NAV aggregation in `/api/stats` and the sorted aggregation in category movers.
Those query changes belong to later, separately gated sprints.

Representative responses were not compressed:

| Response | Identity bytes | Gzip bytes | Reduction |
|---|---:|---:|---:|
| Fund `100033` detail | 149,876 | 7,349 | 95.1% |
| Fund `100033` 20-year NAV | 43,142 | 8,181 | 81.0% |
| Category movers | 20,231 | 3,025 | 85.0% |

The workstation used for the HTTP probes was in Europe while the service is in
Mumbai, adding roughly 430–500 ms of network time. Cloud Run request latency is
therefore the canonical server-side measure; the client benchmark below is for
repeatable before/after comparisons from the same location.

## Bounded benchmark

Run the read-only benchmark against a local or approved DEV URL:

```bash
python3 scripts/benchmark_api.py --base-url http://127.0.0.1:8000
```

It performs one warm-up and five measured requests per endpoint and encoding by
default. Samples are capped at 100. Output is JSON so that a deployment evidence
run can be retained and compared without scraping console prose.

The bounded three-sample pre-change run is retained at
[`evidence/api-perf-01-dev-baseline.json`](evidence/api-perf-01-dev-baseline.json).
Every request that advertised gzip still received `content_encoding=identity`,
confirming the transfer problem on the unchanged DEV revision. Small sample
latencies are directional only; Cloud Run logs remain the server-side baseline.

## API-PERF-01 acceptance

- API responses of at least 500 bytes support gzip when requested.
- Valid response schemas and endpoint paths are unchanged.
- Oversized query text, ID lists, page numbers and time windows fail validation
  before database checkout.
- Each API request emits one JSON event containing route template, status,
  completion state, total duration, pool wait, database time and transfer bytes.
- Query values and fund identifiers from concrete paths are not included in the
  performance log route field.
- Unit, PostgreSQL integration, lint, dependency-audit, credential-scan and image
  build gates continue to pass.

Post-deployment measurements are deliberately deferred to the separately
approved deployment gate.

## API-PERF-02 exact statistics contract

Migration `015_dataset_summary.sql` moves the exact aggregates used by
`/api/stats` out of the request path. It creates one constrained singleton row
and refreshes it after repository-owned dataset mutations. The migration itself
performs the initial full scan; later daily refreshes perform the scan after the
bounded ingestion work, never during an API request.

The summary carries a monotonic `dataset_version`, the latest authoritative AMFI
content hash when available, the refresh reason and timestamp. The API adds the
version, hash and timestamp while preserving every existing response field.
These values are the invalidation input for the later response-cache sprint.

The governed daily refresh wraps scheme/NAV writes, family construction,
provenance, summary refresh and post-write validation in one outer transaction.
Any failed invariant therefore rolls the entire refresh back. Other supported
write commands refresh the same version after successful mutations; dry runs do
not change it.

### API-PERF-02 repository acceptance

- The persisted row exactly equals direct counts and NAV date bounds in
  PostgreSQL integration tests.
- Repeated refreshes increment `dataset_version` and retain the last known AMFI
  content hash when no replacement hash is supplied.
- A summary refresh nested in a failed outer transaction is rolled back.
- `EXPLAIN (FORMAT JSON)` for the API query references `dataset_summary` and not
  `nav_history`.
- The full PostgreSQL 18 suite, lint, dependency audit, credential scan and both
  production image builds pass before merge review.

Migration execution and live latency certification remain separate approval
gates. The live target is a warm `/api/stats` p95 below 250 ms without changing
the exact values returned.

## API-PERF-03 private Insights integration

MFDataIndia is an internal data service for the Trillion Insights backend. Its
Cloud Run ingress is `internal`, unauthenticated invocation is disabled, and the
service-level invoker policy contains only the Trillion Insights runtime and the
MFDataIndia runtime used by the bounded private smoke job. Browser code never
calls MFDataIndia directly.

The caller routes through `tl-dev-vpc` / `tl-dev-mumbai` and supplies a
short-lived Google-signed OIDC token whose audience exactly matches the service
origin. No API key, client secret or user-managed service-account key is part of
the contract.

The GitHub deployment runner cannot perform functional HTTP smoke requests after
the service becomes private. Instead, the deployment updates and executes one
zero-retry Cloud Run smoke job on the approved VPC, then reconciles the service
ingress, exact invoker set, image digest and source SHA. A request from the public
runner must not receive a successful response. Rollback may disable the Insights
consumer or restore an earlier private revision; it must never restore public
ingress or `allUsers` invocation.

Repository work, network-readiness verification, IAM activation, DEV deployment,
Insights activation and production remain separate gates.
