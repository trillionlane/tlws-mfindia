"""Private metadata only; not an acquisition or publication HTTP endpoint."""

from typing import Literal
from pydantic import Field
from mfdataindia.enrichment.schemas import Model


class RegisteredPolicy(Model):
    policy_hash: str
    document: dict


class RegisteredStatus(Model):
    format: Literal["registered-facts-status-v1"]
    registry_hash: str
    sources: list[dict] = Field(min_length=2, max_length=2)
    runs: list[dict] = Field(max_length=10)
    all_amc_data_complete: Literal[False]
    source_rows_changed: Literal[0]


class RegisteredFacts(Model):
    format: Literal["registered-fund-facts-v1"]
    code: int
    identity_hash: str
    snapshots: list[dict] = Field(max_length=100)
    all_amc_data_complete: Literal[False]
    originals_replaced: Literal[False]
    adjusted_idcw_analytics_enabled: Literal[False]
