"""Closed TAG02 consumer records; no private observation/control payloads."""
from typing import Literal
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import policy as p

FORMAT = 'mfdata-fund-tag-api-v1'
Dimension = Literal[tuple(p.DIMENSIONS)]
State = Literal['supported', 'contradicted', 'insufficient-evidence', 'not-assessed', 'not-applicable']
STATE_FOR_REASON = {'SUPPORTED_IDENTITY_OBSERVATION':'supported',
                    'OUTSIDE_CURRENT_PLAN_SCOPE':'not-applicable',
                    'AUTHORITATIVE_EVIDENCE_NOT_ASSESSED':'not-assessed',
                    'SOURCE_OBSERVATION_DATE_MISSING':'insufficient-evidence',
                    'CATEGORY_AUTHORITY_NOT_AMFI':'insufficient-evidence',
                    'EXPLICIT_PLAN_EVIDENCE_MISSING':'insufficient-evidence',
                    'UNMAPPED_FACT_VALUE':'insufficient-evidence'}


class TagModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class TagErrorBody(TagModel):
    detail: str


class TagDefinition(TagModel):
    tag_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    policy_version: Literal[p.VERSION]
    dimension: Dimension
    value: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def identity(self):
        if self.tag_id != p.digest({'version':self.policy_version,'dimension':self.dimension,'value':self.value}):
            raise ValueError('TAGGING_READ_MODEL_DIFFERS')
        return self


class TagAssessment(TagModel):
    snapshot_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    code: int = Field(gt=0, lt=2**31)
    dimension: Dimension
    state: State
    reason: Literal[tuple(STATE_FOR_REASON)]
    tag_id: str | None = Field(pattern=r'^[0-9a-f]{64}$')
    value: str | None = Field(max_length=1000)
    family_id: str | None = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    mapping_state: Literal['FAMILY_VERIFIED', 'FAMILY_MISSING', 'FAMILY_AMBIGUOUS']
    currency: Literal['not_certified']
    basis: Literal['observed_database_identity']
    in_scope: bool
    as_of: str | None = Field(pattern=r'^\d{4}-\d\d-\d\d$')
    evidence_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    policy_version: Literal[p.VERSION]

    @model_validator(mode='after')
    def consistency(self):
        if (self.state != STATE_FOR_REASON[self.reason] or (not self.in_scope) != (self.state=='not-applicable')
                or (self.in_scope and self.dimension in (*p.RESERVED,'risk') and self.state!='not-assessed')):
            raise ValueError('TAGGING_READ_MODEL_DIFFERS')
        if self.as_of is not None:
            date.fromisoformat(self.as_of)
        if self.state == 'supported':
            if (self.value is None or self.tag_id is None or self.dimension in (*p.RESERVED,'risk')
                    or self.reason != 'SUPPORTED_IDENTITY_OBSERVATION' or not self.in_scope
                    or self.as_of is None):
                raise ValueError('TAGGING_READ_MODEL_DIFFERS')
            TagDefinition(tag_id=self.tag_id,policy_version=self.policy_version,
                          dimension=self.dimension,value=self.value)
        elif self.tag_id is not None or self.value is not None:
            raise ValueError('TAGGING_READ_MODEL_DIFFERS')
        if (self.mapping_state == 'FAMILY_VERIFIED') != (self.family_id is not None):
            raise ValueError('TAGGING_READ_MODEL_DIFFERS')
        return self


class TaggedFund(TagModel):
    code: int = Field(gt=0, lt=2**31)
    state: Literal['found', 'not-found']
    assessments: list[TagAssessment] = Field(max_length=len(p.DIMENSIONS))


class TagDiscovery(TagModel):
    format: Literal[FORMAT]
    policy_version: Literal[p.VERSION]
    snapshot_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    dimensions: list[Dimension]
    ethical_screening_enabled: Literal[False]
    currency: Literal['not_certified']
    definitions_url: str
    funds_url: str
    batch_url: str
    published_funds: int = Field(ge=1,le=p.MAX_FUNDS)
    assessment_counts: dict[State,int]


class TagDefinitionPage(TagModel):
    format: Literal[FORMAT]
    snapshot_id: str
    definitions: list[TagDefinition] = Field(max_length=100)
    next_cursor: str | None
    limit: int


class TaggedFundPage(TagModel):
    format: Literal[FORMAT]
    snapshot_id: str
    funds: list[TaggedFund] = Field(max_length=50)
    next_cursor: str | None
    limit: int
    consistency: Literal['snapshot_bound_fresh_repeatable_read_each_page']


class TaggedFundBatch(TagModel):
    format: Literal[FORMAT]
    snapshot_id: str
    funds: list[TaggedFund] = Field(max_length=50)
