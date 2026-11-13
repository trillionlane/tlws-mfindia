# TAG-04 sustainability and custom-values boundary

## Delivered source behavior

TAG-04 adds a private, read-only `/api/fund-values/v1` surface composed over the
dated TAG-03 portfolio exposure boundary. Policies are server-owned, versioned,
and evidence-addressed. Callers cannot submit or select an alternative policy.

The first source release defines three explicitly custom screens:

- `custom-green-v1`: at least 50% observed renewable-energy exposure and zero
  observed thermal-coal or oil-and-gas-extraction exposure;
- `custom-esg-risk-v1`: zero observed tobacco, weapons, thermal-coal, and
  oil-and-gas-extraction exposure; this is not an ESG rating and does not imply
  that unlisted environmental, social, or governance factors were assessed;
- `custom-sattvik-v1`: zero observed alcohol, tobacco, gambling, weapons, and
  animal-harm exposure; conventional-finance treatment is deliberately not
  decided;
- `custom-ethical-v1`: zero observed alcohol, tobacco, gambling, and weapons
  exposure.

These are deterministic custom criteria, not issuer, regulatory, religious,
impact, sustainability, suitability, or investment certifications.

## Evidence and decision rules

Every assessment retains the portfolio snapshot and date, exposure assessment,
screen registry hash, activity registry hash, and screen evidence hash. The
decision states are `supported`, `contradicted`, `insufficient-evidence`, and
`not-assessed`.

A supported result requires all of the following:

- one active reviewed activity record for every relevant mapped entity;
- a complete activity classification for each such entity;
- portfolio evidence within the screen's maximum age;
- complete security-to-entity coverage except identified non-issuer cash/net
  balance rows;
- required fund look-through and derivative classification; and
- every explicit inclusion and exclusion threshold proven by the retained
  lower/upper weight bounds.

Missing classifications, stale evidence, incomplete look-through, unresolved
derivatives, unmapped holdings, conflicts, and threshold-crossing intervals
cannot become zero exposure or a positive result.

## Initial coverage record

The packaged entity-activity registry is intentionally empty. Consequently this
source change claims no live positive assignments. A pre-policy portfolio is
`not-assessed`; an active-date portfolio without complete activity evidence is
`insufficient-evidence`. Adding reviewed entity activities is a separate data
operation and requires its own evidence and acceptance record.

Exact scheme-news associations are unchanged. TAG-04 does not infer a direct
scheme-news link from a broad topic, sector, AMC, symbol, or company name.

## Verification and release boundary

Native tests exercise the real PostgreSQL reader role, enrollment, retained
portfolio relation, repeatable read-only transaction, denial of mutations, and
preservation of generator capabilities and source rows. Provider-free tests
cover positive, negative, uncertain, stale, missing, ambiguous, look-through,
derivative, deterministic replay, API closure, and OpenAPI behavior.

Source completion, PR approval, merge, CI, deployment, database/data enrollment,
and authenticated TrillionInsights acceptance remain separate statuses.
