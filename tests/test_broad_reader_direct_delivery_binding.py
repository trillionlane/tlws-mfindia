"""Both-image direct delivery smoke remains provider-free and byte-bound."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_SCRIPT_SPEC = spec_from_file_location(
    "_phasec_smoke_broad_reader_direct_delivery_binding",
    Path(__file__).resolve().parents[1] / "scripts"
    / "smoke_broad_reader_direct_delivery_binding.py")
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
binding = module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(binding)


ROOT = Path(__file__).resolve().parents[1]


def test_all_three_new_workers_and_installed_source_are_byte_bound():
    result = binding.smoke(root=ROOT, git_sha="a" * 40)
    assert result["packaged_direct_reader_delivery_binding"] == "PASS"
    assert result["source_files"] == 54
    assert result["worker_count"] == 3
    assert result["provider_requests"] == result["database_queries"] == 0
    assert result["permission_enabled"] is result["ti_verified"] is False


def test_invalid_git_is_not_a_binding():
    with pytest.raises(ValueError):
        binding.smoke(root=ROOT, git_sha="main")
