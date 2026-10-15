"""ABSL full monthly disclosures: exact rows, explicit unknowns, no source writes."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path

from mfdataindia.enrichment.evidence import normalized, valid_isin
from mfdataindia.enrichment.facts import decode, one, AMC
from mfdataindia.review.policy import fingerprint

MAX_BYTES = 8 * 1024 * 1024
_POLICY = {
    "id": "official-absl-complete-portfolios",
    "version": 1,
    "engine_version": "absl-monthly-xls-1",
    "source": "official ABSL monthly ZIP with exactly one bounded BIFF XLS; no macros/formulas executed",
    "scope": "exact declared sheets; full disclosed AUM holdings plus separate derivative note positions; not whole AMC",
    "identity": "exact current AMC/fund/regular Growth, issuer summary paired code/checksum-valid payout ISIN and explicit worksheet regular Growth NAV label; fund-level portfolio not variant-specific allocation",
    "numeric": "BIFF decoded numeric shortest-roundtrip text retained; Decimal precision 50; percent fractions times 100; no binary float arithmetic",
    "precision": "reported weights converted without rounding; market values in INR lakh quantized 0.01 HALF_EVEN; aggregate reconciliation precision 0.01 lakh per constituent rounding allowance",
    "totals": "each closed subsection market value reconciles within (rows+1)*0.005 lakh; all constituent values reconcile to grand total on same bound; no forced scaling to 100",
    "weights": "disclosed $0.00% is censored less-than-0.01 percent, never zero; absent remains null; numeric subsection and grand-total weights tolerate 0.0005 percentage points for publisher subtotal rounding",
    "identifiers": "checksum-valid ISIN or issuer-local/unverified identifier retained as such; never invent ISIN or silently discard zero-value rows",
    "cash": "TREPS/reverse repo retained separately; signed net receivable/payable and margin retained; not equities",
    "derivatives": "separate non-hedging futures long/short notes; margin is collateral not notional; no invented contract quantity/ISIN; other note layouts unsupported",
    "eligibility": "complete disclosed statement does not imply security-identity or derivative-lookthrough eligibility; overlap readiness reported separately",
    "dates": "explicit worksheet as-on date equals declared source date; latest retained not freshness certification",
    "conflicts": "same code/date distinct source snapshots withhold current; pinned originals retained; stale identity withheld",
    "bounds": {
        "archive_bytes": MAX_BYTES,
        "member_bytes": 16 * 1024 * 1024,
        "sheets": 200,
        "rows_per_sheet": 1000,
        "columns": 256,
        "selected_sheets": 10,
        "worker_seconds": 35,
    },
    "source_writes": "none; append immutable source archives and portfolio snapshots only",
}


def policy():
    return deepcopy(_POLICY)


def engine_hash():
    return hashlib.sha256(
        Path(__file__).read_bytes() + Path(__file__).with_name("_portfolio_worker.py").read_bytes()
    ).hexdigest()


def decode_archive(content, sheets):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValueError("bounded original archive required")
    if (
        not 1 <= len(sheets) <= 10
        or len(set(sheets)) != len(sheets)
        or any(not re.fullmatch(r"[A-Z0-9]{1,20}", x) for x in sheets)
    ):
        raise ValueError("bounded exact sheet selection required")
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-m", "mfdataindia.enrichment._portfolio_worker", *sheets],
            input=content,
            capture_output=True,
            timeout=35,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("portfolio extraction deadline exceeded") from None
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("portfolio extraction failed or exceeded bounds")
    value = json.loads(result.stdout)
    if set(value["sheets"]) != set(sheets):
        raise ValueError("extracted sheet selection differs")
    return value


def read_archive(content, sheets):
    """Worker-only BIFF reader. All cells including hidden columns are inspected."""
    import xlrd

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if (
            len(members) != 1
            or members[0].is_dir()
            or members[0].flag_bits & 1
            or not re.fullmatch(r"[A-Za-z0-9_-]+\.xls", members[0].filename)
            or not 0 < members[0].file_size <= 16 * 1024 * 1024
        ):
            raise ValueError("one bounded non-encrypted XLS member required")
        raw = archive.read(members[0])
    book = xlrd.open_workbook(
        file_contents=raw, formatting_info=True, on_demand=True, logfile=io.StringIO()
    )
    if not 0 < book.nsheets <= 200:
        raise ValueError("workbook sheet bound exceeded")
    output = {}
    for name in sheets:
        sheet = book.sheet_by_name(name)
        if not 4 <= sheet.nrows <= 1000 or not 10 <= sheet.ncols <= 256:
            raise ValueError("worksheet dimensions unsupported")
        rows = []
        for r in range(sheet.nrows):
            cells = []
            for c in range(sheet.ncols):
                cell = sheet.cell(r, c)
                if cell.ctype not in (
                    xlrd.XL_CELL_EMPTY,
                    xlrd.XL_CELL_TEXT,
                    xlrd.XL_CELL_NUMBER,
                    xlrd.XL_CELL_BLANK,
                ):
                    raise ValueError("unsupported typed cell in selected disclosure")
                value = str(cell.value) if cell.ctype == xlrd.XL_CELL_NUMBER else cell.value
                if len(value) > 5000:
                    raise ValueError("cell character bound exceeded")
                if c >= 10 and value != "":
                    raise ValueError("unexpected populated disclosure column")
                if c < 10:
                    fmt = book.format_map[book.xf_list[cell.xf_index].format_key].format_str
                    cells.append(
                        {
                            "value": value,
                            "kind": "number" if cell.ctype == xlrd.XL_CELL_NUMBER else "text",
                            "format": fmt,
                        }
                    )
            rows.append(cells)
        output[name] = rows
        book.unload_sheet(name)
    book.release_resources()
    return {
        "member_name": members[0].filename,
        "member_hash": hashlib.sha256(raw).hexdigest(),
        "extractor_version": xlrd.__version__,
        "sheets": output,
    }


def decimal(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"-?[0-9]{1,18}(?:\.[0-9]{1,30})?(?:e-[0-9]{1,2})?", value
    ):
        raise ValueError("finite bounded decimal token required")
    return Decimal(value)


def number(cell, *, weight=False, allow_null=False):
    if allow_null and cell["value"] == "":
        return None
    if cell["kind"] != "number" or (weight and cell["format"] not in ("#,##0.00%", "0.00%")):
        raise ValueError("numeric type or percentage format differs")
    return decimal(cell["value"]) * (100 if weight else 1)


def weight(cell):
    if cell["value"] == "$0.00%" and cell["kind"] == "text":
        return {
            "value": None,
            "basis": "censored",
            "upper_bound_exclusive": "0.01",
            "raw": "$0.00%",
        }
    val = number(cell, weight=True)
    if not -100 <= val <= 200:
        raise ValueError("disclosed weight outside supported bounds")
    return {
        "value": format(val, "f"),
        "basis": "reported",
        "upper_bound_exclusive": None,
        "raw": cell["value"],
    }


def verify_identity(identity, registry, rows):
    name = identity["scheme_name"]
    if (
        normalized(identity["amc"]) != normalized(AMC)
        or identity["plan"] != "REGULAR"
        or identity["option"] != "GROWTH"
    ):
        raise ValueError("exact supported AMC/regular Growth identity required")
    if normalized(one(r"(?m)^1 Fund Name (.+?)\s*$", registry)) != normalized(name) or normalized(
        rows[0][2]["value"]
    ) != normalized(name):
        raise ValueError("exact fund heading differs")
    sections = [
        one(r"(?s)\nISINs\s*\n(.*?)\nAMFI Codes \(To be phased out\)\s*\n", registry),
        one(r"(?s)\nAMFI Codes \(To be phased out\)\s*\n(.*?)\n30 SEBI Codes", registry),
    ]
    tables = []
    for section, pattern in zip(
        sections, (r"(?m)^(.+?) ([A-Z0-9]{12})\s*$", r"(?m)^(.+?) ([1-9][0-9]*)\s*$"), strict=True
    ):
        pairs = re.findall(pattern, section)
        if (
            len(pairs) != len([x for x in section.splitlines() if x.strip()])
            or not pairs
            or len({normalized(k) for k, v in pairs}) != len(pairs)
            or len({v for k, v in pairs}) != len(pairs)
        ):
            raise ValueError("ambiguous identity rows")
        tables.append({normalized(k): v for k, v in pairs})
    if set(tables[0]) != set(tables[1]):
        raise ValueError("identity sections differ")
    suffixes = {
        "aditya birla sun life large cap fund": "-Growth",
        "aditya birla sun life flexi cap fund": " - Growth - Regular Plan",
    }
    if normalized(name) not in suffixes:
        raise ValueError("unsupported exact issuer label policy")
    label = normalized(name + suffixes[normalized(name)])
    isin, code = tables[0].get(label), tables[1].get(label)
    if (
        not isin
        or not valid_isin(isin)
        or isin not in identity["isins"]
        or code != str(identity["code"])
    ):
        raise ValueError("issuer code/ISIN bridge differs")
    if sum(r[2]["value"] == "Regular Plan - Growth Option" for r in rows) != 1:
        raise ValueError("regular Growth worksheet label missing or ambiguous")
    return {
        "variant_label": name + suffixes[normalized(name)],
        "amfi_code": int(code),
        "payout_isin": isin,
        "scope": "fund_shared_portfolio",
    }


def derive(identity, archive, registry, *, as_of, sheet, identity_page):
    extraction = decode_archive(archive, [sheet])
    text, lineage = decode(registry, identity_page)
    with localcontext() as context:
        context.prec = 50
        result = parse(identity, extraction["sheets"][sheet], text, as_of=as_of)
    return {
        **result,
        "sheet": sheet,
        "member_name": extraction["member_name"],
        "member_hash": extraction["member_hash"],
        "extractor_version": extraction["extractor_version"],
        "identity_extraction": lineage,
        "sheet_cells_hash": fingerprint(extraction["sheets"][sheet]),
        "engine_code_hash": engine_hash(),
        "policy_hash": fingerprint(policy()),
    }


def reconcile(group, total):
    values = [decimal(x["market_value_inr_lakh"]) for x in group]
    if not group or abs(sum(values) - number(total[6])) > Decimal("0.005") * (len(values) + 1):
        raise ValueError("disclosed subsection market values do not reconcile")
    reported = weight(total[7])
    if reported["basis"] == "reported":
        exact = sum(
            decimal(x["weight_percent"]["value"])
            for x in group
            if x["weight_percent"]["value"] is not None
        )
        unknown = sum(x["weight_percent"]["basis"] == "censored" for x in group)
        target = decimal(reported["value"])
        if (
            not exact - Decimal("0.0005")
            <= target
            <= exact + unknown * Decimal("0.01") + Decimal("0.0005")
        ):
            raise ValueError("disclosed subsection weights do not reconcile")
    elif any(x["weight_percent"]["basis"] != "censored" for x in group):
        raise ValueError("censored subtotal differs")


def parse(identity, rows, registry, *, as_of):
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        return _parse(identity, rows, registry, as_of=as_of)


def _parse(identity, rows, registry, *, as_of):
    bridge = verify_identity(identity, registry, rows)
    observed = one(
        r"^Portfolio Statement as on ([A-Za-z]+ [0-9]{1,2}, [0-9]{4})$", rows[2][2]["value"]
    )
    if datetime.strptime(observed, "%B %d, %Y").date().isoformat() != as_of:
        raise ValueError("worksheet source date differs")
    if [x["value"] for x in rows[3][2:8]] != [
        "Name of the Instrument / Issuer",
        "ISIN",
        "Industry^ / Rating",
        "Quantity",
        "Market value\n(Rs. in Lakhs)",
        "% to AUM",
    ]:
        raise ValueError("full holdings header differs")
    grand = [i for i, r in enumerate(rows) if r[2]["value"] == "GRAND TOTAL (AUM)"]
    if len(grand) != 1:
        raise ValueError("one explicit full-statement grand total required")
    end = grand[0]
    holdings, group, totals, section = [], [], [], None
    headings = {
        "Equity & Equity Related": "equity",
        "Debt Instruments": "debt",
        "Government Securities": "government",
        "TREPS / Reverse Repo Investments": "cash",
        "Other Current Assets / (Liabilities)": "current_assets_liabilities",
    }
    allowed = {
        "Others",
        "a) Listed/awaiting listing on Stock Exchanges",
        "a) Listed/awaiting listing on the stock exchanges",
        "b) Unlisted",
    }
    for i, r in enumerate(rows[4:end], 5):
        label = r[2]["value"]
        if not any(x["value"] != "" for x in r):
            continue
        if label in headings:
            if group:
                raise ValueError("unclosed subsection")
            section = headings[label]
            continue
        if label in allowed and not any(x["value"] != "" for x in r[3:]):
            continue
        if label == "Total":
            reconcile(group, r)
            totals.append(
                {
                    "row": i,
                    "market_value_inr_lakh": format(number(r[6]), "f"),
                    "weight_percent": weight(r[7]),
                }
            )
            group = []
            continue
        if section is None or not isinstance(label, str) or not label:
            raise ValueError("unrecognized holdings row")
        identifier = r[3]["value"] or None
        if section in ("equity", "debt", "government") and not identifier:
            raise ValueError("security identifier absent")
        if identifier and (
            not isinstance(identifier, str) or not re.fullmatch(r"[A-Z0-9]{1,30}", identifier)
        ):
            raise ValueError("unsupported security identifier")
        value = number(r[6])
        if value < 0 and section != "current_assets_liabilities":
            raise ValueError("unsupported negative security value")
        amount = format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN), "f")
        qty = number(r[5], allow_null=True)
        if qty is not None and qty < 0:
            raise ValueError("unsupported negative quantity")
        holding = {
            "row": i,
            "name": label,
            "asset_class": section,
            "identifier": identifier,
            "identifier_kind": "isin"
            if identifier and valid_isin(identifier)
            else "issuer_local"
            if identifier
            else "not_applicable",
            "industry_or_rating": r[4]["value"] or None,
            "quantity": format(qty, "f") if qty is not None else None,
            "market_value_inr_lakh": amount,
            "weight_percent": weight(r[7]),
            "raw_cells": [x["value"] for x in r[1:10]],
        }
        if any(h["identifier"] == identifier for h in holdings if identifier):
            raise ValueError("duplicate security identifier")
        holdings.append(holding)
        group.append(holding)
    if group or not holdings or not totals:
        raise ValueError("unclosed or empty full portfolio")
    total = number(rows[end][6])
    if (
        total <= 0
        or number(rows[end][7], weight=True) != 100
        or abs(sum(decimal(x["market_value_inr_lakh"]) for x in holdings) - total)
        > Decimal("0.005") * (len(holdings) + 1)
    ):
        raise ValueError("grand total does not reconcile")
    known_weight = sum(
        decimal(h["weight_percent"]["value"])
        for h in holdings
        if h["weight_percent"]["value"] is not None
    )
    censored = sum(h["weight_percent"]["basis"] == "censored" for h in holdings)
    if (
        not known_weight - Decimal("0.0005")
        <= 100
        <= known_weight + censored * Decimal("0.01") + Decimal("0.0005")
    ):
        raise ValueError("complete disclosed weights do not reconcile to AUM")
    notes = [
        {"row": i + 1, "cells": [x["value"] for x in r[2:10]]}
        for i, r in enumerate(rows)
        if i > end and any(x["value"] != "" for x in r[2:10])
    ]
    positions = parse_derivatives(notes, as_of)
    return {
        "identity_evidence": bridge,
        "holdings": holdings,
        "subtotals": totals,
        "grand_total_inr_lakh": format(total.quantize(Decimal("0.01")), "f"),
        "grand_total_row": end + 1,
        "derivative_positions": positions,
        "notes": notes,
        "statement_complete": True,
        "overlap_eligible": not positions
        and all(
            h["identifier_kind"] == "isin"
            for h in holdings
            if h["asset_class"] in ("equity", "debt", "government")
        ),
        "overlap_limitation": "derivative_lookthrough_or_security_identity_requires_separate_policy"
        if positions or any(h["identifier_kind"] == "issuer_local" for h in holdings)
        else None,
    }


def parse_derivatives(notes, as_of):
    date = datetime.fromisoformat(as_of).strftime("%B %d, %Y").replace(" 0", " ")
    positions, seen, consumed = [], set(), set()
    labels = {
        "(a)": "hedging_futures",
        "(b)": "nonhedging_futures",
        "(c)": "hedging_options",
        "(d)": "nonhedging_options",
        "(e)": "hedging_swaps",
    }
    texts = [str(n["cells"][0]) for n in notes]
    for prefix, kind in labels.items():
        indices = [i for i, t in enumerate(texts) if t.startswith(prefix)]
        if len(indices) != 1:
            raise ValueError("complete five-category derivative notes required")
        i = indices[0]
        if date not in texts[i]:
            raise ValueError("derivative note date differs")
        if re.search(r"\bis Nil\b", texts[i], re.I):
            seen.add(kind)
            continue
        if prefix != "(b)" or not texts[i].endswith(":"):
            raise ValueError("unsupported derivative disclosure; retain evidence unaccepted")
        header = notes[i + 1]["cells"]
        if header[:6] != [
            "Underlying",
            "Long/Short",
            "Futures price\nwhen purchased Rs.",
            "Current price\nof the contract Rs.",
            "Margin maintained\nin Rs. Lakhs",
            "% to Net Assets",
        ]:
            raise ValueError("futures note header differs")
        j = i + 2
        while j < len(notes) and not str(notes[j]["cells"][0]).startswith("For the month ended"):
            cells = notes[j]["cells"]
            if cells[1] not in ("Long", "Short") or not isinstance(cells[0], str):
                raise ValueError("unparsed futures note row")
            positions.append(
                {
                    "row": notes[j]["row"],
                    "underlying_name": cells[0],
                    "direction": cells[1],
                    "purchase_price_inr": format(decimal(cells[2]), "f"),
                    "current_price_inr": format(decimal(cells[3]), "f"),
                    "margin_inr_lakh": format(decimal(cells[4]), "f"),
                    "reported_exposure_percent": format(decimal(cells[5]) * 100, "f"),
                    "identifier": None,
                    "quantity": None,
                    "notional_inr": None,
                }
            )
            consumed.add(notes[j]["row"])
            j += 1
        if j == len(notes) or not positions:
            raise ValueError("incomplete futures notes")
        seen.add(kind)
    if len(seen) != 5:
        raise ValueError("incomplete derivative categories")
    # Closed-period trading totals are retained as notes, not current positions.
    # Any other numeric note row must be parsed, never silently ignored while
    # claiming the active derivative disclosure is complete.
    for i, note in enumerate(notes):
        if (
            any(
                re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", v)
                for v in note["cells"]
                if isinstance(v, str)
            )
            and note["row"] not in consumed
        ):
            prior = notes[i - 1]["cells"] if i else []
            if (
                len(prior) < 5
                or not prior[0].startswith("Total number of contracts")
                or not prior[4].startswith("Net Profit/Loss value")
            ):
                raise ValueError("unparsed numeric derivative or note row")
            for value in note["cells"][:5]:
                decimal(value)
    return positions
