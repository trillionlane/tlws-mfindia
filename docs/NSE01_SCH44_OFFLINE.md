# NSE01 SCH44 offline inspection

SCH44 has a separate historical dry-run profile,
`SCH-OBSERVED-44-DRY-RUN-v1`. The existing SCH43 fixture FULL manifest,
publication path, API dataset, and active snapshot rules still apply as
before. The SCH44 command accepts local files and writes an aggregate report
to stdout. It has no database connection, importer operation or publication
mode. Its source scope is always `UNVERIFIED`.

## Observed profile

The exact ordered 44-position header is defined in `nse.schema.HEADERS44`.
The sanitized fixture includes leading-dot decimals, zero numeric
placeholders, date and time sentinels, and optional fields. Retained originals
were inspected privately. Both used UTF-8 and CRLF, preserved the last empty
cell, and ended after the final data row. The parser also accepts LF and an
optional UTF-8 BOM or line terminator.

| Positions | Structural treatment |
| --- | --- |
| 1, 41 | Unsigned integer text. Preserve the source serial and lock-in text. |
| 2–6, 9, 26, 34, 39, 42, 44 | Literal text. Reject control characters and retain blanks. The ISIN at position 5 needs a valid checksum for matching. |
| 7, 8, 10, 11, 17, 18, 27–33, 38, 40 | Preserve exact tokens. Report unexpected tokens as field holds. |
| 12–15, 19–24, 35 | Unsigned decimal text, including leading-dot forms. Preserve original spelling and zeros. |
| 16, 25 | Validate clock text and preserve it. Keep zero time and cutoff semantics unresolved. |
| 36, 37, 43 | Validate calendar dates when present. Preserve blank or `00-00-0000` source values without inventing dates. |

Both originals contained only `NORMAL` at position 8. This does not
distinguish Direct from Regular. Positions 34 and 44 were blank throughout.
Positions 39 and 41–43 also included blanks. Positions 36 and 37 used the
zero-date sentinel throughout. The reinvestment column included `Y`, `N`,
and `Z`, and the parser holds `Z` as unresolved.

Position 14 is labeled additional-purchase maximum. It is preserved
without applying it as a purchase limit or eligibility decision. Position
19 represents a quantity in this layout and is parsed separately from the
SCH43 documented blank. The dry run calculates no purchase, redemption,
SIP, STP, SWP or switch eligibility.

## Dated dry run

Run locally with already retained inputs.

```sh
uv run python -m mfdataindia.nse.sch44 \
  --source /private/example/synthetic-sch44.psv --source-date 2026-09-18 \
  --catalogue /private/example/synthetic-catalogue.json \
  --crosswalk /private/example/synthetic-crosswalk.json
```

The catalogue uses `nse01-mfdata-catalogue-v1`, with `as_of`,
`dataset_version` and `funds` captured consistently by the operator. Each
fund contains the seven canonical identity columns used by the existing SCH
join in `nse.store.FUNDS`. The explicit crosswalk follows the existing
`nse01-crosswalk-v1` descriptor. The command records all three input digests.

A match requires the reviewed AMC and NSE scheme-code target, checksum-valid
source ISIN, exact canonical ISIN slot, canonical AMC, plan and option.
Duplicate or conflicting assignments are counted separately. Regular in-scope
rows require a confirmed source active flag. Direct and other excluded
canonical targets are counted as out of scope. Rows without a reviewed
crosswalk are accounted for with explicit reasons. The separate ISIN-candidate
counts describe exact slot lookups and do not certify matches. The historical
run may use a catalogue captured after the source date. The report retains both
dates so matches are interpreted against that catalogue version.

Keep originals, actual crosswalks and historical reports private. To certify a
new export, retain the exact downloaded bytes, date, environment, selected
filters and independent evidence of the intended complete scope. The
`CONSOLIDATED SCHEME MASTER` report selection identifies the report type.
`ALL` in the filename carries no scope authority.
