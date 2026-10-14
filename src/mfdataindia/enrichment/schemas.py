"""Bounded evidence-only contract, distinct from accepted fund facts."""

from datetime import date, datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from mfdataindia.enrichment.evidence import MAX_BYTES, public_url


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Intake(Model):
    source_url: str = Field(max_length=2048)
    final_url: str = Field(max_length=2048)
    official_hosts: list[str] = Field(min_length=1, max_length=5)
    amc: str = Field(min_length=1, max_length=200)
    observed_at: datetime
    document_as_of: date | None = None
    media_type: Literal["application/pdf", "text/html"]
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    codes: list[StrictInt] = Field(min_length=1, max_length=10)
    use_basis: Literal["operator_reported_authorization", "published_permission"]
    authority_reference: str = Field(min_length=8, max_length=500)
    source_conflict: Literal[False] = False

    @field_validator("amc", "authority_reference")
    @classmethod
    def safe_text(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("control characters not accepted")
        return value

    @model_validator(mode="after")
    def bounded(self):
        hosts = set(self.official_hosts)
        if len(hosts) != len(self.official_hosts):
            raise ValueError("duplicate official hosts")
        for host in hosts:
            public_url("https://" + host + "/")
        for url in (self.source_url, self.final_url):
            public_url(url, hosts)
        if any(
            urlsplit(url).hostname
            in {"amfiindia.com", "www.amfiindia.com", "portal.amfiindia.com", "api.amfiindia.com"}
            for url in (self.source_url, self.final_url)
        ):
            # General AMFI terms conflict with bulk retention/reuse. An AMC's
            # reported oral permission cannot be silently applied to AMFI.
            if self.use_basis != "published_permission":
                raise ValueError("AMFI document retention requires resolved source-specific scope")
        if len(self.codes) != len(set(self.codes)) or any(
            not 0 < c <= 2147483647 for c in self.codes
        ):
            raise ValueError("bounded unique codes required")
        if self.observed_at.tzinfo is None:
            raise ValueError("timezone-aware acquisition time required")
        self.observed_at = self.observed_at.astimezone(timezone.utc)
        if self.document_as_of and self.document_as_of > self.observed_at.date():
            raise ValueError("document date cannot follow observation")
        return self


class Identity(Model):
    code: StrictInt = Field(gt=0, le=2147483647)
    amc: str
    scheme_name: str
    plan: str
    option: str
    isins: list[str]


class Mapping(Model):
    code: int
    identity: Identity
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["matched_candidate", "unmapped"]
    reason: str | None
    source_row: dict[str, str] | None


class Document(Model):
    format: Literal["official-enrichment-document-v1"]
    document_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_length: int = Field(gt=0, le=MAX_BYTES)
    intake: Intake
    parser_state: Literal["parsed", "unsupported"]
    parser_reason: str | None
    parsed_rows: int = Field(ge=0, le=20000)
    publication: Literal["evidence_only_not_accepted_facts"]


class Coverage(Model):
    format: Literal["official-enrichment-coverage-v1"]
    code: int
    policy_hash: str
    source_document_as_of: date | None
    document_id: str | None
    mapping: Mapping | None
    state: Literal["not_captured", "matched_candidate", "unmapped", "identity_changed"]
    reason: str | None
    facts_available: Literal[False]
    universe_complete: Literal[False]


class DocumentPage(Model):
    items: list[Document]
    next_after: str | None


class PolicyResponse(Model):
    policy_hash: str
    document: dict
