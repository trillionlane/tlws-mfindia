"""Pin the entire admitted PR133 parent and the finite no-freeze DEV delta."""

import hashlib
import os
from pathlib import Path
import stat
import subprocess

from broad_reader_runtime_environment_preservation import NEW_ADDED, restore_runtime_parent


ROOT = Path(__file__).resolve().parents[1]
PARENT = "9337a4d2d28c1c428039d20e8fbc3492b4c073eb"
MODIFIED = {
    "scripts/apply_broad_reader_direct_dev.py": "3d58da041916441396f50f96b4cb268da1fbf1bbd60716d7faef7f6282bcd083",
    "scripts/check_broad_reader_direct_dev.py": "f36fff4ce3173aa4609a306b05189dd5ffec933196385db21cc7feba8ab8c7d9",
    "scripts/collect_broad_reader_direct_dev.py": "9e26ea2f828066a0815edb322243fbaf2439afccebccb4852a529150998da9e0",
    "scripts/operate_broad_reader_direct_dev.py": "adf53918e2f5cac5b608a8a444dc00ac2d40a70c1c68bfdcb53dc0ebe6c29bfd",
    "src/mfdataindia/review/broad_reader_direct_delivery_spec.py": "4a9b1d949c81304c2af248f43bbaffd292082cb2bd41542588f73ab0222da6dc",
    "src/mfdataindia/review/broad_reader_direct_grant.py": "c0b1ffa2add33504ba94dcc65728fc114aac8fcd15b41c4ddfbe4a368916b828",
    "src/mfdataindia/review/broad_reader_direct_rollout_controller.py": "edde0ce15012df012aba9964790f513e4288b9d34618894e49d33655bd227b07",
    "tests/test_broad_reader_direct_capture_stage_diagnostic.py": "4c92dea44383565ad685650c0d430e398f3d93630d7bf07ff534d1dc71cc7f9b",
    "tests/test_broad_reader_direct_capture_worker.py": "dd4496a1aa770adbf864d851202c217800c7152608bae1d806a50f5526a196ee",
    "tests/test_broad_reader_direct_delivery_spec.py": "9d2b1c02f4c086a7e8c2c53a81a076c8ada8252102a074867de691f4b002f696",
    "tests/test_broad_reader_direct_delivery_preservation.py": "5fa68f8d0b1f3205fe8adb70b7cebe6de09aca47de914243240848824c2a49cb",
    "tests/test_broad_reader_direct_grant_postgres.py": "178fe74a17c4bb6e998494124d3142c971955fde797efcdfdf2a01c4e5273a74",
    "tests/test_broad_reader_direct_reader_worker.py": "cbca5e04d709e012635ac32b2d5ec63a5742fd76479a26a0e6bfce317b01afe1",
    "tests/test_broad_reader_direct_rollout_controller.py": "ab91bc2146fc98fb26099008f6b4794a67b4ec8461f2bf42757a2a4a5f6cd056",
}
NEW_TEST = "tests/test_broad_reader_direct_no_freeze.py"
NEW_TEST_SHA256 = "91328a1fed560b86701988d355d7baba0c0755be03406defe4fff8df26a366df"
NEW_CHAIN_TEST = "tests/test_broad_reader_direct_no_freeze_chain.py"
NEW_CHAIN_SHA256 = "a89e9e595b7a5dbcb842894a1e2509eadc1e2958555a05947532044064c3d454"
NEW_IDENTITY_TEST = "tests/test_broad_reader_direct_fresh_capture_identity.py"
NEW_IDENTITY_SHA256 = "b5866cdfeb6b56b672b7cc66da6b3a0022c4f8ca12bfa8a36b3395daa4c068d8"
SELF = "tests/test_broad_reader_direct_no_freeze_parent_preservation.py"


def _git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def test_all_parent_bytes_modes_and_exact_no_freeze_delta():
    records = [row for row in _git("ls-tree", "-r", "-z", PARENT).split(b"\0") if row]
    assert len(records) == 1052
    parent_names = set()
    for record in records:
        identity, encoded_name = record.split(b"\t", 1)
        mode, kind, blob = identity.decode().split()
        name = encoded_name.decode()
        parent_names.add(name)
        path = ROOT / name
        assert kind == "blob"
        if mode == "120000":
            assert path.is_symlink()
            raw = os.readlink(path).encode()
        else:
            assert path.is_file() and not path.is_symlink(), name
            assert stat.S_ISREG(path.stat().st_mode)
            assert bool(path.stat().st_mode & 0o111) == (mode == "100755"), name
            raw = path.read_bytes()
            raw = restore_runtime_parent(name, raw)
        if name in MODIFIED:
            assert hashlib.sha256(raw).hexdigest() == MODIFIED[name], name
            assert raw != _git("show", PARENT + ":" + name), name
        else:
            actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            assert actual == blob, name
    assert set(MODIFIED) <= parent_names
    assert NEW_TEST not in parent_names and NEW_CHAIN_TEST not in parent_names
    assert NEW_IDENTITY_TEST not in parent_names
    assert SELF not in parent_names
    for name in (NEW_TEST, NEW_CHAIN_TEST, NEW_IDENTITY_TEST, SELF):
        path = ROOT / name
        assert path.is_file() and not path.is_symlink()
        assert stat.S_ISREG(path.stat().st_mode)
        assert not path.stat().st_mode & 0o111
    assert hashlib.sha256((ROOT / NEW_TEST).read_bytes()).hexdigest() == NEW_TEST_SHA256
    assert hashlib.sha256((ROOT / NEW_CHAIN_TEST).read_bytes()).hexdigest() == NEW_CHAIN_SHA256
    assert hashlib.sha256(restore_runtime_parent(
        NEW_IDENTITY_TEST, (ROOT / NEW_IDENTITY_TEST).read_bytes()
    )).hexdigest() == NEW_IDENTITY_SHA256
    changed = set(_git("diff", "--name-only", PARENT).decode().splitlines())
    untracked = set(_git("ls-files", "--others", "--exclude-standard").decode().splitlines())
    assert changed | untracked == set(MODIFIED) | {NEW_TEST, NEW_CHAIN_TEST,
                                                NEW_IDENTITY_TEST, SELF} | NEW_ADDED
