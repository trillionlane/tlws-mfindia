"""Deterministic evidence classification and conservative exact-variant mapping."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from copy import deepcopy
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from mfdataindia.review.policy import fingerprint

MAX_BYTES = 8 * 1024 * 1024
DIRECTORY = "https://www.amfiindia.com/online-center/download-factsheets"
_POLICY = {
    "id": "official-enrichment-evidence",
    "version": 1,
    "engine_version": "official-evidence-1",
    "source_writes": "none; new immutable private sidecars only",
    "coverage": "all-AMC discovery; bounded imports; no inferred completeness",
    "parser": "UTF-8 static HTML table with exact unique headers; PDF retained but not parsed",
    "headers": ["amfi scheme code", "scheme name", "isin", "plan", "option"],
    "mapping": "one source row per code; exact normalized AMC/name/plan/option plus checksum-valid matching ISIN",
    "normalization": "Unicode NFKC, casefold, collapsed whitespace; no alias or sibling inference",
    "source_dates": "operator-declared document date, not independently verified by this parser",
    "source_provenance": "operator-supplied bytes and acquisition metadata; not provider-authenticated by this parser",
    "rights": "source-specific reported authority and conflicts retained; evidence is not legal clearance",
    "publication": "evidence and candidate mappings only; no accepted facts, NAV, portfolios or analytics",
    "maximum_bytes": MAX_BYTES,
    "maximum_rows": 20000,
    "maximum_batch": 10,
    "raw_documents": "private; never returned by API",
}


def policy():
    return deepcopy(_POLICY)


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def public_url(value: str, hosts: set[str] | None = None) -> str:
    """No credentials, IP literals, ports, query secrets or control characters."""
    if len(value) > 2048 or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise ValueError("invalid official URL")
    parts = urlsplit(value)
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.port
        or parts.fragment
        or parts.query
        or "\\" in value
        or "," in value
        or "@" in parts.netloc
        or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,63}",
            host,
        )
        or parts.netloc != host
        or not parts.path.startswith("/")
        or (hosts is not None and host not in hosts)
    ):
        raise ValueError("unapproved or unsupported official URL")
    return value


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell, self.ignore, self.in_table = [], None, None, 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignore += 1
        if self.ignore:
            return
        if tag == "table":
            self.in_table += 1
        if tag == "tr" and self.in_table:
            if self.row is not None:
                raise ValueError("nested/invalid source rows")
            self.row = []
        if tag in ("th", "td") and self.row is not None:
            if self.cell is not None:
                raise ValueError("nested/invalid source cells")
            if any(k in ("colspan", "rowspan") and v != "1" for k, v in attrs):
                raise ValueError("merged source cells unsupported")
            self.cell = []

    def handle_data(self, data):
        if not self.ignore and self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignore = max(0, self.ignore - 1)
        if self.ignore:
            return
        if tag in ("th", "td") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        if tag == "tr" and self.row is not None:
            if self.cell is not None:
                raise ValueError("unclosed source cell")
            self.rows.append(self.row)
            if len(self.rows) > _POLICY["maximum_rows"]:
                raise ValueError("too many source rows")
            self.row = None
        if tag == "table":
            self.in_table -= 1
            self.rows.append(None)  # No header leakage across independent tables.


def classify(content: bytes, media_type: str):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValueError("bounded nonempty original bytes required")
    if media_type == "application/pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("PDF media type/bytes mismatch")
        return {"state": "unsupported", "reason": "pdf_parser_not_implemented", "rows": []}
    if media_type != "text/html":
        raise ValueError("unsupported document media type")
    text = content.decode("utf-8", errors="strict")
    if "<table" not in text.lower():
        return {"state": "unsupported", "reason": "no_static_scheme_table", "rows": []}
    parser = Tables()
    parser.feed(text)
    parser.close()
    if parser.row is not None or parser.cell is not None or parser.in_table or parser.ignore:
        raise ValueError("unclosed source markup")
    header, rows = None, []
    for row in parser.rows:
        if row is None:
            header = None
            continue
        labels = [normalized(cell) for cell in row]
        if set(_POLICY["headers"]) <= set(labels):
            if len(labels) != len(set(labels)):
                raise ValueError("ambiguous source headers")
            header = labels
        elif header is not None:
            if len(row) != len(header):
                raise ValueError("source row width differs")
            record = dict(zip(header, row, strict=True))
            rows.append({k: record[k] for k in _POLICY["headers"]})
    return {
        "state": "parsed" if rows else "unsupported",
        "reason": None if rows else "no_supported_scheme_rows",
        "rows": rows,
    }


def valid_isin(value):
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value):
        return False
    digits = "".join(c if c.isdigit() else str(ord(c) - 55) for c in value)
    total = 0
    for i, c in enumerate(reversed(digits)):
        v = int(c) * (2 if i % 2 else 1)
        total += v // 10 + v % 10
    return total % 10 == 0


def mappings(parsed, identities, amc):
    """Candidates are not reviewed official facts or source correctness certificates."""
    grouped = {}
    for row in parsed["rows"]:
        code = row["amfi scheme code"]
        if not re.fullmatch(r"[1-9][0-9]{0,9}", code) or int(code) > 2147483647:
            continue
        grouped.setdefault(int(code), []).append(row)
    output = []
    for identity in identities:
        code = identity["code"]
        rows = grouped.get(code, [])
        reason = parsed["reason"] if parsed["state"] != "parsed" else "code_absent"
        if len(rows) > 1:
            reason = "ambiguous_duplicate_code"
        elif len(rows) == 1:
            row = rows[0]
            if normalized(amc) != normalized(identity["amc"]):
                reason = "amc_mismatch"
            elif normalized(row["scheme name"]) != normalized(identity["scheme_name"]):
                reason = "scheme_name_mismatch"
            elif normalized(row["plan"]) != normalized(identity["plan"]):
                reason = "plan_mismatch"
            elif normalized(row["option"]) != normalized(identity["option"]):
                reason = "option_mismatch"
            elif not valid_isin(row["isin"]):
                reason = "invalid_isin_checksum"
            elif row["isin"] not in identity["isins"]:
                reason = "isin_mismatch"
            else:
                reason = None
        output.append(
            {
                "code": code,
                "identity": identity,
                "identity_hash": fingerprint(identity),
                "state": "matched_candidate" if reason is None else "unmapped",
                "reason": reason,
                "source_row": rows[0] if len(rows) == 1 else None,
            }
        )
    return output


class DirectoryLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.href, self.text = [], None, []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a":
            self.href, self.text = values.get("href"), []
        if tag == "img" and self.href:
            self.text.append(values.get("alt", ""))

    def handle_data(self, data):
        if self.href:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag != "a":
            return
        name = " ".join(" ".join(self.text).split())
        if self.href and "mutual fund" in name.casefold():
            try:
                link = public_url(urljoin(DIRECTORY, self.href))
            except ValueError:
                link = None
            if link and urlsplit(link).hostname != "www.amfiindia.com":
                self.links.append(
                    {"amc_display_name": name, "url": link, "state": "discovery_candidate"}
                )
        self.href, self.text = None, []


def directory(content):
    if not 0 < len(content) <= MAX_BYTES:
        raise ValueError("bounded directory bytes required")
    parser = DirectoryLinks()
    parser.feed(content.decode("utf-8", errors="strict"))
    parser.close()
    unique = {fingerprint(row): row for row in parser.links}
    return {
        "format": "official-amc-directory-discovery-v1",
        "url": DIRECTORY,
        "content_hash": hashlib.sha256(content).hexdigest(),
        "policy_hash": fingerprint(policy()),
        "population_basis": "recognized_links_in_this_document_not_all_registered_AMCs",
        "complete": False,
        "sources": sorted(unique.values(), key=lambda r: (r["amc_display_name"], r["url"])),
    }
