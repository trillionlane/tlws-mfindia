"""Private source history/difference and explicitly human-attested review contract."""

from typing import Literal
from pydantic import Field, StrictInt, field_validator
from mfdataindia.enrichment.schemas import Model


class FactReviewMutation(Model):
    expected_version: StrictInt = Field(ge=0, le=999999999999999999)
    expected_dependency_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["verified", "needs_evidence"]
    attestation: Literal["human_reviewed_original"]
    fields: list[
        Literal["benchmark_name", "fund_managers", "base_expense_ratio", "risk_level", "aum"]
    ] = Field(min_length=1, max_length=5)
    evidence_snapshot_ids: list[str] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=8, max_length=1000)

    @field_validator("fields", "evidence_snapshot_ids")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("duplicate fields or evidence")
        return value

    @field_validator("evidence_snapshot_ids")
    @classmethod
    def hashes(cls, value):
        import re

        if any(not re.fullmatch("[0-9a-f]{64}", v) for v in value):
            raise ValueError("exact source snapshot identifiers required")
        return value

    @field_validator("reason")
    @classmethod
    def safe_reason(cls, value):
        if len(value.strip()) < 8 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("bounded non-control human review reason required")
        return value


class FactReviewState(Model):
    format: Literal["official-facts-verification-state-v1"]
    code: int
    version: int
    dependency_hash: str
    dependencies: dict
    fields: dict
    policy_hash: str
    history: list[dict] = Field(max_length=100)
    next_after_version: int | None
    source_rows_changed: Literal[0]
    publication_requires_human_verification: Literal[False]


class FactReviewEvent(Model):
    format: Literal["official-facts-review-event-v1"]
    event_id: str
    code: int
    version: int
    dependency_hash: str
    dependencies: dict
    caller: str
    actor: str
    request_hash: str
    request: FactReviewMutation


class FactSourceHistory(Model):
    format: Literal["official-facts-source-history-v1"]
    code: int
    snapshots: list[dict] = Field(max_length=100)
    next_after_id: str | None
    order: Literal["immutable_snapshot_id_not_recency"]
    originals_retained: Literal[True]
    universe_complete: Literal[False]


class FactSourceChanges(Model):
    format: Literal["official-facts-source-changes-v1"]
    code: int
    from_snapshot_id: str
    to_snapshot_id: str
    from_disclosure_as_of: str
    to_disclosure_as_of: str
    fields: dict
    source_rows_changed: Literal[0]
    changes_applied: Literal[False]
    human_verification_inferred: Literal[False]
