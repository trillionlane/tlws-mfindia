"""Private operational status, not a claim of complete monthly coverage."""

from pydantic import Field
from typing import Literal
from mfdataindia.enrichment.schemas import Model


class RefreshPolicyResponse(Model):
    policy_hash: str
    document: dict


class RefreshStatus(Model):
    format: Literal["official-facts-refresh-status-v1"]
    runs: list[dict] = Field(max_length=10)
    last_run: dict | None
    last_successful_source_check: dict | None
    all_amc_coverage: Literal[False]
    human_verification: Literal["separate_from_source_refresh"]
    nav_dataset_version_is_facts_revision: Literal[False]
