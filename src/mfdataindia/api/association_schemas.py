"""Typed contract for the private fund-family association writer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mfdataindia.load.normalise import slugify

AssociationTagType = Literal["scheme_alias"]
AssociationSource = Literal["trillion-insights"]


class AssociationTag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=160)
    type: AssociationTagType
    source: AssociationSource

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = slugify(value.strip())
        if not normalized or len(normalized) > 160:
            raise ValueError("value must normalize to a 1..160 character slug")
        return normalized


class AssociationTagMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    tags: list[AssociationTag] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def reject_duplicates(self) -> "AssociationTagMutation":
        keys = [(tag.type, tag.value, tag.source) for tag in self.tags]
        if len(keys) != len(set(keys)):
            raise ValueError("tags must be unique after normalization")
        return self


class AssociationTagState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tlws_mf_id: str
    version: int = Field(ge=0)
    tags: list[AssociationTag]


class AssociationTagMutationResponse(AssociationTagState):
    previous_version: int = Field(ge=0)
    added: list[AssociationTag]


class AssociationConflict(BaseModel):
    detail: str
    current_version: int | None = None
