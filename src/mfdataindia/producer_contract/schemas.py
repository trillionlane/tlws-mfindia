"""MFData-authored native contract; JSON Schema/OpenAPI are mechanical exports.

Immutable mapping truth and mutable refresh observations are separate models.
The historical TIP I0-v1 schema is not this contract's authority.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import policy

MAX_SAFE_INTEGER = 2**53 - 1
SECTIONS = ("variants", "executionReferences", "nav", "facts", "family")
Section = Literal["variants", "executionReferences", "nav", "facts", "family"]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]
RootId = Annotated[str, Field(pattern=r"^mfref-v1:[0-9a-f]{64}$")]
Timestamp = Annotated[str, Field(json_schema_extra={"format": "date-time"})]
TIMESTAMP = re.compile(
    r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])T"
    r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]+)?"
    r"(?:Z|[+-](?:0[0-9]|1[0-3]):[0-5][0-9]|[+-]14:00)"
)


def timestamp_ms(value: str) -> int:
    """Validate a real RFC3339 calendar/timezone with millisecond precision."""
    if type(value) is not str or TIMESTAMP.fullmatch(value) is None:
        raise ValueError("TIMESTAMP_INVALID")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        delta = instant - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
    except (ValueError, OverflowError):
        raise ValueError("TIMESTAMP_INVALID") from None


def bounded_utf16(value: str, maximum: int) -> str:
    # Bound browser/JavaScript consumers too: non-BMP characters use two units.
    try:
        size = len(value.encode("utf-16-le")) // 2
    except UnicodeError:
        raise ValueError("IDENTIFIER_INVALID") from None
    if not 1 <= size <= maximum:
        raise ValueError("IDENTIFIER_INVALID")
    return value


def unique_reasons(value: list[str]) -> list[str]:
    if len(set(value)) != len(value):
        raise ValueError("REASON_CODES_INVALID")
    return value


class WireModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, revalidate_instances="always")

    @model_validator(mode="before")
    @classmethod
    def primitive_input(cls, value):
        # Do not let Literal equality, int subclasses or custom objects coerce discriminators.
        def check(item):
            if type(item) in (str, int, bool, float, type(None)):
                return
            if type(item) is list:
                for child in item:
                    check(child)
                return
            if type(item) is dict and all(type(key) is str for key in item):
                for child in item.values():
                    check(child)
                return
            raise ValueError("STRUCTURE_INVALID")

        check(value)
        return value


class Variant(WireModel):
    model_config = ConfigDict(json_schema_extra={"allOf": [{
        "if": {"properties": {"optionCode": {"const": "IDCW_REINVEST"}}},
        "then": {"properties": {"matchedIsinSlot": {"const": "DIV_REINVEST"}}},
        "else": {"properties": {"matchedIsinSlot": {"const": "GROWTH_OR_DIV_PAYOUT"}}},
    }]})
    amfiSchemeCode: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
    matchedIsinSlot: Literal["GROWTH_OR_DIV_PAYOUT", "DIV_REINVEST"]
    isin: Annotated[str, Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")]
    planCode: Literal["REGULAR"]
    optionCode: Literal["GROWTH", "IDCW_PAYOUT", "IDCW_REINVEST"]

    @model_validator(mode="after")
    def registry_slot(self):
        expected = "DIV_REINVEST" if self.optionCode == "IDCW_REINVEST" else "GROWTH_OR_DIV_PAYOUT"
        if self.matchedIsinSlot != expected:
            raise ValueError("OPTION_CONFLICT")
        return self


class Mapping(WireModel):
    model_config = ConfigDict(json_schema_extra={"allOf": [
        {"if": {"properties": {"status": {"const": status}}},
         "then": {"properties": {"reasonCodes": {
             "minItems": 0 if status == "MAPPED" else 1,
             "maxItems": 0 if status == "MAPPED" else 1 if status in {"UNMAPPED", "AMBIGUOUS"} else 10,
             "items": {"enum": sorted(reasons)},
         }}}}
        for status, reasons in {
            "MAPPED": {"NO_EXACT_NSE_REFERENCE"},  # Empty array; item schema is never used.
            "UNMAPPED": {"NO_EXACT_NSE_REFERENCE"},
            "AMBIGUOUS": {"MULTIPLE_PLAUSIBLE_NSE_REFERENCES"},
            "CONFLICT": policy.CONFLICT_REASONS,
            "OUT_OF_SCOPE": policy.EXCLUSION_REASONS,
        }.items()
    ]})
    status: Literal["MAPPED", "AMBIGUOUS", "UNMAPPED", "CONFLICT", "OUT_OF_SCOPE"]
    reasonCodes: Annotated[list[policy.ClosedReason], Field(max_length=10, json_schema_extra={"uniqueItems": True})]

    _unique = field_validator("reasonCodes")(unique_reasons)

    @model_validator(mode="after")
    def closed_reasons(self):
        reasons = set(self.reasonCodes)
        if self.reasonCodes != sorted(reasons):
            raise ValueError("REASON_ORDER_INVALID")
        valid = (
            (self.status == "MAPPED" and not reasons)
            or (self.status == "UNMAPPED" and reasons == {"NO_EXACT_NSE_REFERENCE"})
            or (self.status == "AMBIGUOUS" and reasons == {"MULTIPLE_PLAUSIBLE_NSE_REFERENCES"})
            or (self.status == "CONFLICT" and bool(reasons) and reasons <= policy.CONFLICT_REASONS)
            or (self.status == "OUT_OF_SCOPE" and bool(reasons) and reasons <= policy.EXCLUSION_REASONS)
        )
        if not valid:
            raise ValueError("STATUS_REASON_INVALID")
        return self


class ProviderReference(WireModel):
    provider: Literal["NSE"]
    schemeCode: Annotated[str, Field(min_length=1, max_length=64)]

    @field_validator("schemeCode")
    @classmethod
    def string_bound(cls, value):
        return bounded_utf16(value, 64)


class ExecutionReferenceRecord(WireModel):
    model_config = ConfigDict(json_schema_extra={"allOf": [{
        "if": {"properties": {"mapping": {"properties": {"status": {"const": "MAPPED"}}}}},
        "then": {"properties": {"providerReference": {"type": "object"}}},
        "else": {"properties": {"providerReference": {"type": "null"}}},
    }]})
    contractVersion: Literal[policy.EXECUTION_VERSION]
    executionReferenceId: Annotated[Identifier, Field(pattern=r"^mfer-(?:route|assessment)-v1:[0-9a-f]{64}$")]
    snapshotId: RootId
    sourceAsOf: Timestamp
    expiresAt: Literal[None]
    variant: Variant
    mapping: Mapping
    providerReference: ProviderReference | None

    @field_validator("executionReferenceId", "snapshotId")
    @classmethod
    def identifier_bound(cls, value):
        return bounded_utf16(value, 128)

    @field_validator("sourceAsOf")
    @classmethod
    def timestamp(cls, value):
        timestamp_ms(value)
        return value

    @model_validator(mode="after")
    def consistency(self):
        if (self.mapping.status == "MAPPED") != (self.providerReference is not None):
            raise ValueError("PROVIDER_REFERENCE_INVALID")
        if self.executionReferenceId != policy.expected_reference_id(self.model_dump(mode="json")):
            raise ValueError("REFERENCE_ID_INVALID")
        return self


Count = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SourcePin(WireModel):
    snapshotId: Identifier
    digest: Digest


class StatusCounts(WireModel):
    MAPPED: Count
    AMBIGUOUS: Count
    UNMAPPED: Count
    CONFLICT: Count
    OUT_OF_SCOPE: Annotated[int, Field(ge=0, le=0)]


class VariantOutcomeCounts(WireModel):
    MAPPED: Count
    AMBIGUOUS: Count
    UNMAPPED: Count
    CONFLICT: Count


class UniverseCounts(WireModel):
    inputVariants: Count
    eligibleVariants: Count
    evaluatedVariants: Count
    excludedVariants: Count
    withheldEligibleVariants: Count

    @model_validator(mode="after")
    def accounting(self):
        if (self.inputVariants != self.eligibleVariants + self.excludedVariants
                or self.evaluatedVariants + self.withheldEligibleVariants != self.eligibleVariants):
            raise ValueError("UNIVERSE_COUNTS_INVALID")
        return self


class ExecutionMetadata(WireModel):
    model_config = ConfigDict(json_schema_extra={"allOf": [{
        "if": {"properties": {"completeness": {"const": "COMPLETE"}}},
        "then": {"properties": {"mappingContentDigest": {"type": "string"}}},
        "else": {"properties": {"mappingContentDigest": {"type": "null"}}},
    }]})
    mappingContentDigest: Annotated[Digest | None, Field(description=(
        "SHA-256 of mfdata-mapping-semantics-v1: validated variant/route decisions and population accounting; "
        "excludes source, snapshot and acceptance metadata. Required null unless COMPLETE. "
        "Distinct from the wire-record contentDigest."))]
    canonicalUniverse: SourcePin
    nseSource: SourcePin | None
    codeRegistryVersion: Literal[policy.REGISTRY_VERSION]
    mappingPolicyVersion: Literal[policy.POLICY_VERSION]
    universe: UniverseCounts
    recordStatusCounts: StatusCounts
    variantOutcomeCounts: VariantOutcomeCounts
    exclusions: dict[policy.PopulationExclusion, Count]
    withheldReasons: dict[policy.ClosedReason, Count]
    acceptedAt: Timestamp | None
    nseFilenameDate: str | None
    nseFilenameDateBasis: Literal["FILENAME"] | None
    completeness: Literal["UNAVAILABLE", "INCOMPLETE", "COMPLETE"]

    @field_validator("acceptedAt")
    @classmethod
    def time(cls, value):
        if value is not None:
            timestamp_ms(value)
        return value

    @field_validator("nseFilenameDate")
    @classmethod
    def date(cls, value):
        if value is not None:
            from datetime import date
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
                raise ValueError("SOURCE_DATE_INVALID")
            date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def accounting(self):
        records = self.recordStatusCounts
        variants = self.variantOutcomeCounts
        if (self.completeness == "COMPLETE") != (self.mappingContentDigest is not None):
            raise ValueError("MAPPING_SEMANTIC_DIGEST_REQUIRED")
        if sum(variants.model_dump().values()) != self.universe.evaluatedVariants:
            raise ValueError("VARIANT_OUTCOME_COUNTS_INVALID")
        if ((variants.MAPPED == 0 and records.MAPPED != 0) or records.MAPPED < variants.MAPPED
                or any(getattr(records, status) != getattr(variants, status)
                       for status in ("AMBIGUOUS", "UNMAPPED", "CONFLICT"))):
            raise ValueError("RECORD_VARIANT_COUNTS_INVALID")
        if sum(self.exclusions.values()) != self.universe.excludedVariants:
            raise ValueError("EXCLUSION_COUNTS_INVALID")
        if sum(self.withheldReasons.values()) != self.universe.withheldEligibleVariants:
            raise ValueError("WITHHELD_COUNTS_INVALID")
        if any(value == 0 for value in (*self.exclusions.values(), *self.withheldReasons.values())):
            raise ValueError("ZERO_ACCOUNTING_BUCKET")
        if self.completeness == "UNAVAILABLE":
            if (self.nseSource is not None or self.acceptedAt is not None
                    or self.nseFilenameDate is not None or self.nseFilenameDateBasis is not None
                    or self.universe.evaluatedVariants or any(records.model_dump().values())):
                raise ValueError("UNAVAILABLE_SOURCE_INVALID")
        else:
            if (self.nseSource is None or self.nseFilenameDate is None
                    or self.nseFilenameDateBasis != "FILENAME"):
                raise ValueError("SOURCE_PROVENANCE_REQUIRED")
            if self.completeness != "COMPLETE" and records.UNMAPPED:
                raise ValueError("INCOMPLETE_SOURCE_CANNOT_UNMAP")
            if self.completeness == "COMPLETE":
                if self.acceptedAt is None or self.universe.withheldEligibleVariants:
                    raise ValueError("COMPLETE_UNIVERSE_NOT_EVALUATED")
            elif (self.acceptedAt is not None or self.universe.evaluatedVariants
                    or any(records.model_dump().values())):
                raise ValueError("INCOMPLETE_SOURCE_NOT_ACCEPTED")
        return self


class SectionDescriptor(WireModel):
    section: Section
    schemaVersion: Annotated[str, Field(min_length=1, max_length=96)]
    sectionId: Annotated[str, Field(pattern=r"^mfsection-v1:[0-9a-f]{64}$")]
    contentDigest: Digest
    availability: Literal["AVAILABLE", "UNAVAILABLE", "WITHHELD"]
    itemCount: Count
    sourceAsOf: Timestamp | None
    expiresAt: Literal[None]
    reasonCodes: Annotated[list[policy.ClosedReason], Field(max_length=10)]
    executionMetadata: ExecutionMetadata | None

    _unique = field_validator("reasonCodes")(unique_reasons)

    @model_validator(mode="after")
    def consistency(self):
        if self.schemaVersion != "mfdata-reference-" + self.section + "-v1":
            raise ValueError("SECTION_VERSION_INVALID")
        if self.sectionId != policy.section_id(self.model_dump(mode="json")):
            raise ValueError("SECTION_DIGEST_INVALID")
        if self.availability in {"UNAVAILABLE", "WITHHELD"} and self.itemCount != 0:
            raise ValueError("COVERAGE_INVALID")
        if self.sourceAsOf is not None:
            timestamp_ms(self.sourceAsOf)
        if self.section != "executionReferences":
            if (self.availability != "UNAVAILABLE" or self.itemCount != 0
                    or self.executionMetadata is not None or self.sourceAsOf is not None
                    or self.contentDigest != policy.digest([]) or self.reasonCodes):
                raise ValueError("RESERVED_SECTION_UNAVAILABLE_ONLY")
        elif self.executionMetadata is None:
            raise ValueError("EXECUTION_METADATA_REQUIRED")
        else:
            meta = self.executionMetadata
            if self.sourceAsOf != meta.acceptedAt:
                raise ValueError("ACCEPTANCE_TIME_MISMATCH")
            if self.itemCount != sum(meta.recordStatusCounts.model_dump().values()):
                raise ValueError("STATUS_COUNTS_INVALID")
            if meta.completeness == "UNAVAILABLE" and self.availability != "UNAVAILABLE":
                raise ValueError("UNAVAILABLE_SOURCE_INVALID")
            if meta.completeness == "COMPLETE" and self.availability != "AVAILABLE":
                raise ValueError("COMPLETE_AVAILABILITY_INVALID")
            if meta.completeness == "INCOMPLETE" and self.availability != "WITHHELD":
                raise ValueError("INCOMPLETE_AVAILABILITY_INVALID")
            if self.itemCount == 0 and self.contentDigest != policy.digest([]):
                raise ValueError("EMPTY_CONTENT_DIGEST_INVALID")
        return self


class SnapshotManifest(WireModel):
    contractVersion: Literal["mfdata-reference-snapshot-v1"]
    snapshotId: RootId
    sections: Annotated[list[SectionDescriptor], Field(min_length=5, max_length=5)]

    @model_validator(mode="after")
    def all_sections_once(self):
        if tuple(section.section for section in self.sections) != SECTIONS:
            raise ValueError("SECTION_INVENTORY_INVALID")
        if self.snapshotId != policy.root_id([s.model_dump(mode="json") for s in self.sections]):
            raise ValueError("ROOT_DIGEST_INVALID")
        return self


class ExecutionReferencePage(WireModel):
    contractVersion: Literal["mfdata-reference-snapshot-v1"]
    snapshotId: RootId
    section: Literal["executionReferences"]
    sectionId: Annotated[str, Field(pattern=r"^mfsection-v1:[0-9a-f]{64}$")]
    sectionContentDigest: Digest
    count: Annotated[int, Field(ge=0, le=100)]
    items: Annotated[list[ExecutionReferenceRecord], Field(max_length=100)]
    nextCursor: Annotated[str, Field(min_length=1, max_length=1024)] | None

    @model_validator(mode="after")
    def count_matches(self):
        if self.count != len(self.items) or (not self.items and self.nextCursor is not None):
            raise ValueError("PAGE_COUNT_INVALID")
        if any(item.snapshotId != self.snapshotId for item in self.items):
            raise ValueError("SNAPSHOT_MISMATCH")
        if any(item.mapping.status == "OUT_OF_SCOPE" for item in self.items):
            raise ValueError("OUT_OF_SCOPE_ACCOUNTING_ONLY")
        return self


class Cursor(WireModel):
    snapshotId: RootId
    section: Literal["executionReferences"]
    sectionId: Annotated[str, Field(pattern=r"^mfsection-v1:[0-9a-f]{64}$")]
    afterAmfiSchemeCode: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
    afterMatchedIsinSlot: Literal["GROWTH_OR_DIV_PAYOUT", "DIV_REINVEST"]
    afterExecutionReferenceId: Identifier


ObservationId = Annotated[str, Field(pattern=r"^mfrefresh-observation-v1:[0-9a-f]{64}$")]


class RefreshObservation(WireModel):
    """Standalone immutable attempt receipt, separate from any latest summary."""

    contractVersion: Literal["mfdata-reference-refresh-observation-v1"]
    observationId: ObservationId
    attemptId: Identifier
    attemptAt: Timestamp
    disposition: Literal["VERIFIED_UNCHANGED", "UPDATED", "FAILED", "INCOMPLETE", "CONFLICT"]
    sourceSnapshotId: Identifier | None
    sourceDigest: Digest | None
    filenameGenerationDate: str | None
    filenameDateBasis: Literal["FILENAME"] | None
    diagnosticCode: Literal["VERIFIED_UNCHANGED", "NEW_SNAPSHOT_ACCEPTED", "FETCH_FAILED",
                            "VALIDATION_FAILED", "SOURCE_INCOMPLETE", "IDENTITY_CONFLICT"]
    candidateMappingContentDigest: Annotated[Digest | None, Field(description=(
        "Candidate mfdata-mapping-semantics-v1 digest recomputed from a complete validated section, "
        "not the wire-record contentDigest or source hash. UPDATED requires a semantic change; "
        "VERIFIED_UNCHANGED retains the original mapping root and sourceAsOf."))]
    canonicalUniverse: SourcePin | None
    codeRegistryVersion: Literal[policy.REGISTRY_VERSION]
    mappingPolicyVersion: Literal[policy.POLICY_VERSION]
    previousHeadSnapshotId: RootId | None
    retainedOrAcceptedHeadSnapshotId: RootId | None

    _date = field_validator("filenameGenerationDate")(ExecutionMetadata.date.__func__)

    @model_validator(mode="after")
    def provenance(self):
        timestamp_ms(self.attemptAt)
        if (self.sourceSnapshotId is None) != (self.sourceDigest is None):
            raise ValueError("OBSERVATION_PIN_INVALID")
        if (self.filenameGenerationDate is None) != (self.filenameDateBasis is None):
            raise ValueError("OBSERVATION_DATE_INVALID")
        if self.disposition in {"VERIFIED_UNCHANGED", "UPDATED"}:
            if (self.sourceSnapshotId is None or self.filenameGenerationDate is None
                    or self.candidateMappingContentDigest is None or self.canonicalUniverse is None
                    or self.retainedOrAcceptedHeadSnapshotId is None):
                raise ValueError("OBSERVATION_DECISION_PINS_REQUIRED")
        if self.disposition != "UPDATED":
            if self.previousHeadSnapshotId != self.retainedOrAcceptedHeadSnapshotId:
                raise ValueError("OBSERVATION_CANNOT_CHANGE_HEAD")
        elif self.previousHeadSnapshotId == self.retainedOrAcceptedHeadSnapshotId:
            raise ValueError("OBSERVATION_UPDATE_REQUIRES_NEW_HEAD")
        expected = {"VERIFIED_UNCHANGED": {"VERIFIED_UNCHANGED"}, "UPDATED": {"NEW_SNAPSHOT_ACCEPTED"},
                    "FAILED": {"FETCH_FAILED", "VALIDATION_FAILED"}, "INCOMPLETE": {"SOURCE_INCOMPLETE"},
                    "CONFLICT": {"IDENTITY_CONFLICT"}}
        if self.diagnosticCode not in expected[self.disposition]:
            raise ValueError("OBSERVATION_DIAGNOSTIC_INVALID")
        if self.observationId != policy.observation_id(self.model_dump(mode="json")):
            raise ValueError("OBSERVATION_ID_INVALID")
        return self


class RefreshStatus(WireModel):
    """Mutable latest summary/pointer. Never part of a mapping/root digest."""

    contractVersion: Literal["mfdata-reference-refresh-status-v1"]
    activeSnapshotId: Annotated[str, Field(pattern=r"^mfref-v1:[0-9a-f]{64}$")] | None
    lastRefreshAttemptAt: Timestamp | None
    lastSuccessfulRefreshAt: Timestamp | None
    lastVerifiedAt: Timestamp | None
    refreshStatus: Literal["NOT_ATTEMPTED", "VERIFIED_UNCHANGED", "UPDATED", "FAILED", "INCOMPLETE", "CONFLICT"]
    latestObservationId: ObservationId | None

    @model_validator(mode="after")
    def times(self):
        for value in (self.lastRefreshAttemptAt, self.lastSuccessfulRefreshAt, self.lastVerifiedAt):
            if value is not None:
                timestamp_ms(value)
        if self.refreshStatus == "NOT_ATTEMPTED":
            if any(value is not None for value in (self.lastRefreshAttemptAt, self.lastSuccessfulRefreshAt,
                                                   self.lastVerifiedAt, self.latestObservationId)):
                raise ValueError("REFRESH_NOT_ATTEMPTED_INVALID")
        elif self.lastRefreshAttemptAt is None or self.latestObservationId is None:
            raise ValueError("REFRESH_OBSERVATION_REQUIRED")
        if self.lastRefreshAttemptAt is not None:
            for value in (self.lastSuccessfulRefreshAt, self.lastVerifiedAt):
                if value is not None and timestamp_ms(value) > timestamp_ms(self.lastRefreshAttemptAt):
                    raise ValueError("REFRESH_TIME_ORDER_INVALID")
        if self.refreshStatus in {"VERIFIED_UNCHANGED", "UPDATED"}:
            if (self.activeSnapshotId is None or self.lastSuccessfulRefreshAt != self.lastRefreshAttemptAt
                    or self.lastVerifiedAt != self.lastRefreshAttemptAt):
                raise ValueError("SUCCESSFUL_REFRESH_INVALID")
        return self
