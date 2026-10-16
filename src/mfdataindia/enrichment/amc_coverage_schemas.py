"""Private census metadata only; no source bytes or acquisition API."""

from typing import Literal
from pydantic import Field
from mfdataindia.enrichment.schemas import Model


class AmcCoverageResponse(Model):
    format: Literal["official-amc-coverage-v1"]
    policy_hash: str
    register_hash: str
    coverage_revision: str
    dataset_version: int | None
    directory_observed_at: str
    directory_sha256: str
    directory_population_basis: str
    directory_entries: int
    directory_entries_with_link: int
    database_amcs: int
    database_only_amcs: int
    retained_schemes: int
    in_scope_schemes: int
    scope_policy: Literal["existing_generated_in_scope_unchanged"]
    amcs: list[dict] = Field(max_length=600)
    wave_membership: dict[str, list[str]]
    mapping_counts: dict[str, int]
    all_amc_data_complete: Literal[False]
    current_month_coverage_certified: Literal[False]
    new_acquisition_authority: Literal[False]
    human_verification_created: Literal[False]
    source_rows_changed: Literal[0]


class SchemePage(Model):
    format: Literal["official-amc-schemes-v1"]
    amc_key: str
    coverage_revision: str
    items: list[dict] = Field(max_length=100)
    next_after_code: int | None
    all_amc_data_complete: Literal[False]


class CoveragePolicy(Model):
    policy_hash: str
    document: dict
