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

### Public export settings

On the public NSE MF Desk, select **Masters → Scheme Master** and choose
**Consolidated Scheme Master**. Supply the Member ID for the intended
environment on each download. The repository carries no default Member ID.
Use the same value in a private, per-export JSON file to record the settings
alongside the downloaded bytes. Pass that file with `--export-context`.

```json
{
  "format": "nse01-sch44-export-context-v1",
  "environment": "UAT",
  "report_type": "CONSOLIDATED_SCHEME_MASTER",
  "member_id": "000000042",
  "source_sha256": "<SHA-256 of the exact export>"
}
```

The example Member ID is synthetic. The report includes the environment and
report selection and records that the Member ID was supplied. The ID itself
stays in the private settings file. The source hash binds these settings
to the export. This caller-provided metadata leaves full scope unverified.

## API retrieval from the approved NSE connector

NSE's [MF Invest API v1.9.7, section “Scheme Master, NAV & Settlement calendar Download API”](https://www.nseinvest.com/nsemfdesk/resources/upload/apidetails/NSEMF_API_Details_V1.9.7.pdf#page=191)
defines a POST to `/nsemfdesk/api/v2/reports/MASTER_DOWNLOAD` with
`{"file_type":"SCH"}` for the consolidated Scheme Master. The documented
filename contains `ALL`. That is a file-type label. The selector confirms
the report family without establishing the member's full coverage.

Run the adapter from the approved TLAM connector checkout with its existing
dependencies and UAT runtime configuration. Inject the existing private
credential references through the operator environment or secret bindings.
Keep credential values and the Member ID out of the command line and the
repository. Bind the operator-approved checkout to its exact revision. The
adapter checks that the checkout is clean and matches that revision before it
loads the connector. Use a new private directory path for each invocation.

```sh
cd "$APPROVED_TLAM_CHECKOUT"
TSX_TSCONFIG_PATH="$PWD/tsconfig.json" node --import tsx \
  "$MFDATA_CHECKOUT/scripts/capture_nse_sch44.mjs" \
  --connector-root "$PWD" --connector-revision "$APPROVED_CONNECTOR_SHA" \
  --output-dir "$PRIVATE_NEW_CAPTURE_DIR"
```

The existing connector owns authentication, endpoint allowlisting, session
setup, transport time and byte limits, and its configured quota policy. The
adapter calls `downloadMasterOnce` once for SCH, preserves the complete
decoded file bytes after checking the connector-observed digest, and records a
safe receipt. The receipt tracks the observed filename, provider-derived
export date when usable, capture time, SHA-256, bytes, header width and rows.
No downloaded value becomes a path. A missing or unrecognized filename leaves
the source date unknown and returns a nonzero status even when the valid
reference bytes were retained. A provider denial, interrupted download or
credential-bearing response leaves only safe failure evidence. No live import
or active-snapshot change is available from this adapter.

After capture, pass the local bytes and receipt to the existing dry run.
The SCH44 parser checks the full ordered header and every source row. It
rechecks the receipt against the exact file bytes and counts. A capture without
a usable export date can still produce an explicitly undated diagnostic
report. Separate the timestamp of retrieval from the date of the source file.

```sh
cd "$MFDATA_CHECKOUT"
uv run python -m mfdataindia.nse.sch44 \
  --source "$PRIVATE_NEW_CAPTURE_DIR/source.txt" \
  --api-receipt "$PRIVATE_NEW_CAPTURE_DIR/receipt.json" \
  --catalogue "$PRIVATE_DATED_CATALOGUE" \
  --crosswalk "$PRIVATE_REVIEWED_CROSSWALK"
```

### Scheduling proposal for review

Once NSE confirms the expected file date and member scope, pilot one UAT
capture **07:00 Asia/Kolkata each weekday** via the existing connector runtime.
Confirm that timing against the issuer's availability before enabling it.
Keep retrieval and dry-run results separate from any activation decision.

Use a stable private connector scope, environment, report type, provider export
date and content SHA-256 for duplicate detection. The same date and hash are a
no-op. Retain a changed hash on the same date as a revision for review.
Missing dates, unknown scope, layout changes or incomplete transfer hold the
candidate. Keep the previous active snapshot available. Stop and surface a
sanitized status on authentication, entitlement or egress failure, and reconcile
an uncertain capture before making another request. Do not replace the active
snapshot automatically.
