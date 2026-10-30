"""Both-image direct delivery smoke remains provider-free and byte-bound."""

from pathlib import Path

import pytest

from scripts import smoke_broad_reader_direct_delivery_binding as binding


ROOT = Path(__file__).resolve().parents[1]


def test_all_three_new_workers_and_installed_source_are_byte_bound():
    result = binding.smoke(root=ROOT, git_sha="a" * 40)
    assert result["packaged_direct_reader_delivery_binding"] == "PASS"
    assert result["source_files"] == 53
    assert result["worker_count"] == 3
    assert result["provider_requests"] == result["database_queries"] == 0
    assert result["permission_enabled"] is result["ti_verified"] is False


def test_invalid_git_is_not_a_binding():
    with pytest.raises(ValueError):
        binding.smoke(root=ROOT, git_sha="main")
