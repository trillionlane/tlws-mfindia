"""Versioned discovery census, never a URL allowlist or acquisition authority."""

import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, StrictInt, model_validator
from mfdataindia.enrichment.evidence import DIRECTORY, normalized, public_url
from mfdataindia.enrichment.schemas import Model
from mfdataindia.review.policy import fingerprint

REGISTER_PATH = Path("/app/config/official-amc-register-v1.json")
if not REGISTER_PATH.exists():
    REGISTER_PATH = Path(__file__).resolve().parents[3] / "config/official-amc-register-v1.json"


class Member(Model):
    member_id: str = Field(pattern=r"^[1-9][0-9]{0,5}$")
    name: str = Field(min_length=1, max_length=200)
    listed_factsheet_url: str | None = Field(max_length=2048)
    source_state: str = Field(pattern=r"^(listed|missing)$")
    url_review_reasons: list[str]
    planned_wave: str = Field(pattern=r"^(FS-08|FS-09|FS-10|FS-11|EXP-[0-9]{2})$")
    acquisition_enabled: bool = False

    @model_validator(mode="after")
    def bounded(self):
        if self.acquisition_enabled or self.source_state != (
            "listed" if self.listed_factsheet_url else "missing"
        ):
            raise ValueError("census is not acquisition authority")
        if any(ord(c) < 32 for c in self.name):
            raise ValueError("invalid directory name")
        if self.url_review_reasons != url_reasons(self.listed_factsheet_url):
            raise ValueError("URL review reasons differ from retained discovery value")
        return self


class Register(Model):
    format: str = Field(pattern=r"^official-amc-source-register-v1$")
    version: StrictInt = Field(ge=1, le=1)
    directory_url: str = Field(
        pattern=r"^https://www\.amfiindia\.com/online-center/download-factsheets$"
    )
    directory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    capture_execution: str = Field(pattern=r"^mf-fs06-directory-capture-[a-z0-9-]+$")
    population_basis: str = Field(
        pattern=r"^official_directory_entries_not_all_registered_or_active_AMCs$"
    )
    members: list[Member] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique(self):
        if self.observed_at.tzinfo is None:
            raise ValueError("timezone-aware directory observation required")
        if len({r.member_id for r in self.members}) != len(self.members) or len(
            {normalized(r.name) for r in self.members}
        ) != len(self.members):
            raise ValueError("ambiguous directory identity")
        return self


def load_register(path=None):
    return Register.model_validate_json((path or REGISTER_PATH).read_text())


def url_reasons(url):
    if not url:
        return ["no_factsheet_link_listed"]
    try:
        parts = urlsplit(url)
    except ValueError:
        return ["not_approved_transport_url"]
    reasons = []
    if parts.scheme != "https":
        reasons.append("non_https_listing")
    if parts.query:
        reasons.append("query_requires_source_adapter")
    if parts.fragment:
        reasons.append("fragment_requires_source_adapter")
    if any(ord(c) <= 32 or ord(c) >= 127 for c in url):
        reasons.append("noncanonical_url_characters")
    try:
        public_url(url)
    except ValueError:
        reasons.append("not_approved_transport_url")
    return sorted(reasons)


class Directory(HTMLParser):
    def __init__(self):
        super().__init__()
        self.script = None
        self.scripts = []
        self.heading = None
        self.headings = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.script = []
        if tag == "h5":
            self.heading = []

    def handle_data(self, value):
        if self.script is not None:
            self.script.append(value)
        if self.heading is not None:
            self.heading.append(value)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.scripts.append("".join(self.script))
            self.script = None
        if tag == "h5" and self.heading is not None:
            self.headings.append(" ".join("".join(self.heading).split()))
            self.heading = None


def parse_directory(content):
    """Decode only JSON flight frames; never evaluate scripts or follow URLs."""
    if not isinstance(content, bytes) or not 0 < len(content) <= 4 * 1024 * 1024:
        raise ValueError("bounded original directory required")
    parser = Directory()
    parser.feed(content.decode("utf-8", errors="strict"))
    parser.close()
    frames = []
    for script in parser.scripts:
        match = re.fullmatch(r"self\.__next_f\.push\((\[.*\])\);?", script, re.DOTALL)
        if match:
            value = json.loads(match[1])
            if len(value) == 2 and value[0] == 1 and isinstance(value[1], str):
                frames.append(value[1])
    candidates = []
    for line in "".join(frames).splitlines():
        if "amc_monthly_mf_factsheets" not in line:
            continue
        _, separator, payload = line.partition(":")
        if not separator:
            raise ValueError("invalid directory frame")
        node = json.loads(payload)
        if (
            not isinstance(node, list)
            or len(node) != 4
            or not isinstance(node[3], dict)
            or not isinstance(node[3].get("data"), list)
        ):
            raise ValueError("unrecognized directory data layout")
        candidates.append(node[3]["data"])
    if len(candidates) != 1 or not candidates[0] or len(candidates[0]) > 200:
        raise ValueError("one complete bounded directory dataset required")
    records = []
    for row in candidates[0]:
        if (
            not isinstance(row, dict)
            or not {"mf_id", "mf_name", "amc_monthly_mf_factsheets"} <= row.keys()
        ):
            raise ValueError("directory entry missing required keys")
        identifier, name, url = (row[k] for k in ("mf_id", "mf_name", "amc_monthly_mf_factsheets"))
        if not all(isinstance(v, str) for v in (identifier, name, url)):
            raise ValueError("directory entry types differ")
        records.append({"member_id": identifier, "name": name, "listed_factsheet_url": url or None})
    if len({r["member_id"] for r in records}) != len(records) or len(
        {normalized(r["name"]) for r in records}
    ) != len(records):
        raise ValueError("duplicate directory identity")
    visible = {normalized(r["name"]) for r in records if r["listed_factsheet_url"]}
    if (
        len(parser.headings) != len(set(map(normalized, parser.headings)))
        or set(map(normalized, parser.headings)) != visible
    ):
        raise ValueError("rendered cards and embedded source population disagree")
    return sorted(records, key=lambda r: (normalized(r["name"]), r["member_id"]))


def policy():
    return {
        "id": "official-amc-coverage-census",
        "version": 1,
        "source_records_changed": 0,
        "directory": DIRECTORY,
        "population": "union of this dated directory snapshot and every retained database AMC; never a legal registered/active-AMC assertion",
        "scope": "all retained schemes accounted; existing generated in_scope unchanged; non-defunct in-scope is an operational target, not an active-lifecycle assertion",
        "mapping": "exact normalized AMC name only; unmatched database names retained explicitly; no fuzzy merge or scheme identity approval",
        "coverage": "source listing, monthly archival, extraction and projection exposure are separate; no snapshot is no evidence, not a zero field value",
        "freshness": "source dates and observation times distinct; no current-month or all-AMC data-completeness claim",
        "waves": "checked-in proposed cohorts only; never enable acquisition by changing this census",
        "verification": "source publication is not human verification",
    }


def register_hash(register):
    return fingerprint(register.model_dump(mode="json"))
