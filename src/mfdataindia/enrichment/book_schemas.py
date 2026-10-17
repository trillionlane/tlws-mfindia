"""Private read-only book coverage, with explicit gaps and publication bounds."""

from typing import Literal

from pydantic import Field
from mfdataindia.enrichment.schemas import Identity, Model


class BookPolicy(Model):
    configuration_hash: str
    document: dict


class BookStatus(Model):
    format: Literal["official-amc-book-status-v1"]
    configuration_hash: str
    sources: list[dict] = Field(min_length=1, max_length=4)
    source_rows_changed: Literal[0]
    all_amc_data_complete: Literal[False]
    adjusted_idcw_analytics_enabled: Literal[False]


class BookFund(Model):
    format: Literal["official-amc-book-fund-v1"]
    identity: Identity
    observations: list[dict] = Field(max_length=20)
    verification_status: Literal["needs_verification"]
    adjusted_idcw_analytics_enabled: Literal[False]
