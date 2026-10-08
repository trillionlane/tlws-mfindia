# API performance programme

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
