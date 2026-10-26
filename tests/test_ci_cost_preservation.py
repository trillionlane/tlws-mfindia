"""All 851 parent files restore EXACT full bytes under six finite exceptions."""

import hashlib
import json
from pathlib import Path

import pytest

from ci_cost_preservation import APPROVED, DESIGN_SHA, restore_parent_file

ROOT = Path(__file__).resolve().parents[1]
EXACT_SIX = frozenset({
    'tests/test_verified_review_store.py', 'scripts/run_ci_tests.py',
    'tests/test_ci_test_partition.py', 'tests/test_broad_reader_policy_preservation.py',
    'tests/test_reader_permission_capture_preservation.py', 'tests/test_reader_access_transport.py',
})
GUARD_HASHES = {
    'tests/test_broad_reader_policy_preservation.py': 'db68bb097482e9575013766132a1394f524892912ea7e60a2af0e7c502764c68',
    'tests/test_reader_permission_capture_preservation.py': '2af0c6c4f6e628c796e424ca4d2cdccba6aec02ccc7051fad45fb2a7bd3716cd',
    'tests/test_reader_access_transport.py': '69c51d99ac397a8daa4454361b0bfe83beec6405badb80d510c126d880678f8a',
}


def test_all_parent_files_whole_bytes_including_full_old_preservation_guard():
    authority = json.loads((ROOT / 'docs/CI_COST_PARENT_AUTHORITY_20261026.json').read_text())
    assert authority['base'] == 'c150d9237496deb204ff52e5180b123e35171405'
    assert authority['tree'] == 'ed634d60218f1e155f4acc190dee7caedf96b9e7'
    assert len(authority['files']) == authority['old_tracked_files'] == 851
    assert set(authority['allowed_full_byte_inverse_only']) == APPROVED == EXACT_SIX
    assert DESIGN_SHA == '8d4c3437d34f30cba094faf53de0b35b37eade4709dcdb6f663757a7745d75ba'
    assert hashlib.sha256((ROOT / 'docs/CI_COST_REDUCTION_DESIGN_20261026.json').read_bytes()).hexdigest() == DESIGN_SHA
    recipes = json.loads((ROOT / 'docs/CI_COST_REDUCTION_DESIGN_20261026.json').read_text())['counted_literal_recipe_before_code']
    assert set(recipes) == EXACT_SIX
    for name, expected in authority['files'].items():
        path = ROOT / name
        assert path.is_symlink() == (expected['kind'] == 'symlink'), name
        data = str(path.readlink()).encode() if path.is_symlink() else path.read_bytes()
        original = data
        if name in EXACT_SIX:
            for change in reversed(recipes[name]['substitutions']):
                old, new = change['old'].encode(), change['new'].encode()
                assert data.count(new) == change['count'] == 1
                data = data.replace(new, old, 1)
            assert hashlib.sha256(data).hexdigest() == recipes[name]['old_full_sha256']
        if name in GUARD_HASHES:
            assert hashlib.sha256(data).hexdigest() == GUARD_HASHES[name]
        assert restore_parent_file(name, original) == data
        assert hashlib.sha256(data).hexdigest() == expected['sha256'], name


@pytest.mark.parametrize('name', sorted(APPROVED))
@pytest.mark.parametrize('mutation', ['append', 'drop', 'duplicate'])
def test_literal_inverse_does_not_hide_any_unapproved_bytes(name, mutation):
    data = (ROOT / name).read_bytes()
    if mutation == 'append':
        data += b'\n# unapproved edit\n'
    elif mutation == 'drop':
        data = data[1:]
    else:
        data += data
    with pytest.raises(AssertionError):
        restore_parent_file(name, data)


def test_unapproved_file_is_never_normalized():
    data = b'unchanged bytes\t \n'
    assert restore_parent_file('src/arbitrary.py', data) is data
