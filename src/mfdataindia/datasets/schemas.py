"""Versioned wire types; dataset column names/values remain registry supplied."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import policy

Format = Literal['mfdata-consumer-datasets-v1']
State = Literal['available','empty','unavailable','withheld']
Classification = Literal['fund_research','source_evidence']
TypeName = Literal[tuple(sorted(policy.TYPES))]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class DatasetErrorBody(Model):
    detail: str


class Column(Model):
    name: str = Field(pattern=r'^[a-z][a-z0-9_]{0,62}$')
    type: TypeName
    nullable: bool


class Dataset(Model):
    id: str = Field(pattern=r'^d_[0-9a-f]{32}$')
    title: str = Field(min_length=1,max_length=160)
    classification: Classification
    state: State
    reason: str | None
    schema_version: str | None = Field(pattern=r'^[0-9a-f]{64}$')
    metadata_url: str
    rows_url: str


class Discovery(Model):
    format: Format
    datasets: list[Dataset]


class Metadata(Model):
    format: Format
    id: str = Field(pattern=r'^d_[0-9a-f]{32}$')
    title: str = Field(min_length=1,max_length=160)
    classification: Classification
    state: State
    reason: str | None
    schema_version: str | None = Field(pattern=r'^[0-9a-f]{64}$')
    columns: list[Column]
    key_columns: list[str]
    rows_url: str


class RowPage(Model):
    format: Format
    dataset_id: str = Field(pattern=r'^d_[0-9a-f]{32}$')
    schema_version: str = Field(pattern=r'^[0-9a-f]{64}$')
    state: State
    reason: str | None
    rows: list[dict[str,Any]]
    next_cursor: str | None = Field(max_length=policy.MAX_CURSOR_BYTES * 2)
    limit: int = Field(ge=1,le=policy.MAX_PAGE)
    ordering: Literal['keyset','canonical_offset']
    consistency: Literal['fresh_repeatable_read_each_page']


class ContentChunk(Model):
    format: Format
    dataset_id: str = Field(pattern=r'^d_[0-9a-f]{32}$')
    column: str
    schema_version: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    offset: int = Field(ge=0)
    encoding: Literal['base64']
    content: str = Field(max_length=((policy.MAX_BINARY_CHUNK + 2)//3)*4)
    next_offset: int | None
