"""Current source version is explicit; historical evidence is not rewritten."""

import hashlib
import json
import subprocess

import pytest

from ro01_historical_source import ROOT, BASE, authority, historical_parent
from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review import broad_reader_embedded_authority as embedded


@pytest.mark.parametrize("name", sorted(authority()))
def test_intentional_current_full_bytes_and_tamper_are_not_ignored(name):
    raw = (ROOT / name).read_bytes()
    previous = subprocess.check_output(["git", "show", BASE + ":" + name], cwd=ROOT)
    assert historical_parent(ROOT / name, raw) == previous
    assert raw != previous
    with pytest.raises(AssertionError):
        historical_parent(ROOT / name, raw + b"\n# unreviewed")


def test_versioned_book_authority_changes_only_reader_compatibility_source():
    old_path = "docs/CI_SOURCE_REFERENCE_BOOK_VARIANT_AUTHORITY_20261029.json"
    old = (ROOT / old_path).read_bytes()
    assert old == subprocess.check_output(["git", "show", BASE + ":" + old_path], cwd=ROOT)
    current = (ROOT / source.VARIANT_DOCUMENT).read_bytes()
    document = json.loads(current)
    assert current == embedded.document("variant")
    assert hashlib.sha256(current).hexdigest() == source.VARIANT_SHA256
    assert document["compatibility"]["predecessor_sha256"] == hashlib.sha256(old).hexdigest()
    old_files = json.loads(old)["files"]
    assert document["files"][:-1] == old_files[:-1]
    assert document["files"][-1]["path"] == old_files[-1]["path"]
    assert document["files"][-1]["sha256"] != old_files[-1]["sha256"]
    assert document["runtime_authority"] is False
    assert source.verify_packaged_source(ROOT)["permission_enabled"] is False


def test_current_diagnostic_attribution_is_one_explicit_changed_policy_source():
    from mfdataindia.review import reader_policy_source_version as version
    from mfdataindia.review import core_permission_spec, core_access_metadata_spec, core_access_metadata_spec_v2
    for module in (core_permission_spec, core_access_metadata_spec, core_access_metadata_spec_v2):
        old = module.HISTORICAL_SOURCE_HASHES
        new = module.SOURCE_HASHES
        assert old[version.PATH] == version.PREVIOUS_SHA256
        assert new[version.PATH] == hashlib.sha256((ROOT / version.PATH).read_bytes()).hexdigest()
        assert {k: v for k, v in old.items() if k != version.PATH} == {k: new[k] for k in old if k != version.PATH}
        added = {"src/mfdataindia/review/reader_access_profile.py",
                 "src/mfdataindia/review/reader_policy_source_version.py"}
        assert set(new) - set(old) == added
        assert all(new[path] == hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in added)
    with pytest.raises(ValueError, match="UNRECOGNIZED_READER_POLICY_SOURCE_PREDECESSOR"):
        version.current_attribution({version.PATH: "0" * 64})


def test_existing_controllers_pin_the_current_versioned_specs_exactly():
    from mfdataindia.review import core_permission_controller, core_access_metadata_controller, core_access_metadata_controller_v2
    from mfdataindia.review import core_permission_spec, core_access_metadata_spec, core_access_metadata_spec_v2
    from pathlib import Path
    for controller, spec in ((core_permission_controller, core_permission_spec),
                             (core_access_metadata_controller, core_access_metadata_spec),
                             (core_access_metadata_controller_v2, core_access_metadata_spec_v2)):
        assert controller.SPEC_SHA == hashlib.sha256(Path(spec.__file__).read_bytes()).hexdigest()
