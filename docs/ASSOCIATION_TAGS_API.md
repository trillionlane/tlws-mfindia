# MFDataIndia private association-tag API (contract v1)

Contract: `mfdataindia-association-tags-v1` in
`contracts/mfdataindia-association-tags-v1.json`.

This is a private backend-to-backend write surface. The permitted path is:

`TrillionInsights backend -> private MFData association writer`

Browsers never call it. Cloud Run internal ingress and Google-signed OIDC from
the TrillionInsights runtime identity provide authentication; there is no API
key, shared secret or user-managed service-account key.

## Resource

`GET /api/fund-families/{tlws_mf_id}/association-tags` returns the current
version and typed association set. It does not create state.

`POST /api/fund-families/{tlws_mf_id}/association-tags` additively upserts one
or more associations. `Idempotency-Key` is required and `expected_version`
provides optimistic concurrency.

```http
POST /api/fund-families/{tlws_mf_id}/association-tags
Idempotency-Key: insights-family-alias-20261010-001
Content-Type: application/json
```

```json
{
  "expected_version": 4,
  "tags": [
    {
      "value": "nippon-india-taiwan-equity-fund",
      "type": "scheme_alias",
      "source": "trillion-insights"
    }
  ]
}
```

The initial closed sets are `type=scheme_alias` and
`source=trillion-insights`. Values are normalized to lowercase slugs. A request
may contain 1–50 unique tags.

Successful insertion returns `201` and increments the family version once.
Submitting only existing tags returns `200` without incrementing the version.
An exact idempotent replay returns the recorded body and status. Reusing the
same key for different content or supplying a stale version returns `409`.
Unknown families return `404`; malformed requests return `422`.

Deletion, replacement, bulk import and data-remediation semantics are not part
of v1.

## Storage and activation boundary

Association tags are normalized into dedicated tables. They are never written
to `mf.fund_family.tags`, because that array contains generated facets and is
replaced by the AMFI/factsheet family builder.

The writer uses its own database principal with no DELETE permission and runs
as a separate Cloud Run service. Deployment always sets
`MFDATAINDIA_ASSOCIATION_WRITES_ENABLED=false`; activation is a manual,
exact-SHA-bound workflow and a separate approval gate.
