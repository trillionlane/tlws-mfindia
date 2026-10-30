"""Pin the entire admitted PR133 parent and the finite no-freeze DEV delta."""

import hashlib
import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PARENT = "9337a4d2d28c1c428039d20e8fbc3492b4c073eb"
MODIFIED = {
    "scripts/apply_broad_reader_direct_dev.py": "3d58da041916441396f50f96b4cb268da1fbf1bbd60716d7faef7f6282bcd083",
    "scripts/check_broad_reader_direct_dev.py": "f36fff4ce3173aa4609a306b05189dd5ffec933196385db21cc7feba8ab8c7d9",
    "scripts/collect_broad_reader_direct_dev.py": "9e26ea2f828066a0815edb322243fbaf2439afccebccb4852a529150998da9e0",
    "src/mfdataindia/review/broad_reader_direct_delivery_spec.py": "d84c1f173ff594f9929dbd44202361a6c8a7d27d74eba7a646b91018faf2d997",
    "src/mfdataindia/review/broad_reader_direct_grant.py": "08a1ed13dfad169d09836517e0796670a4d4e078e513357796c1c5f9d47d18d6",
    "src/mfdataindia/review/broad_reader_direct_rollout_controller.py": "ea279d6fa1b8ae945c6ae26120e2b09add1b5fe3a8306e36583f8b6dc10793cd",
    "tests/test_broad_reader_direct_capture_stage_diagnostic.py": "4c92dea44383565ad685650c0d430e398f3d93630d7bf07ff534d1dc71cc7f9b",
    "tests/test_broad_reader_direct_capture_worker.py": "1a280599c1fc52d445be507b873487c71279fe06c67013d2c4031b2fcbc2be69",
    "tests/test_broad_reader_direct_delivery_preservation.py": "5fa68f8d0b1f3205fe8adb70b7cebe6de09aca47de914243240848824c2a49cb",
    "tests/test_broad_reader_direct_grant_postgres.py": "178fe74a17c4bb6e998494124d3142c971955fde797efcdfdf2a01c4e5273a74",
    "tests/test_broad_reader_direct_rollout_controller.py": "0031259aa8f92cfb450d308850bc55641d407a8e55bba47ab4962fc77fadf3eb",
}
NEW_TEST = "tests/test_broad_reader_direct_no_freeze.py"
NEW_TEST_SHA256 = "91328a1fed560b86701988d355d7baba0c0755be03406defe4fff8df26a366df"
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
        if name in MODIFIED:
            assert hashlib.sha256(raw).hexdigest() == MODIFIED[name], name
            assert raw != _git("show", PARENT + ":" + name), name
        else:
            actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            assert actual == blob, name
    assert set(MODIFIED) <= parent_names
    assert NEW_TEST not in parent_names and SELF not in parent_names
    for name in (NEW_TEST, SELF):
        path = ROOT / name
        assert path.is_file() and not path.is_symlink()
        assert stat.S_ISREG(path.stat().st_mode)
        assert not path.stat().st_mode & 0o111
    assert hashlib.sha256((ROOT / NEW_TEST).read_bytes()).hexdigest() == NEW_TEST_SHA256
    changed = set(_git("diff", "--name-only", PARENT).decode().splitlines())
    untracked = set(_git("ls-files", "--others", "--exclude-standard").decode().splitlines())
    assert changed | untracked == set(MODIFIED) | {NEW_TEST, SELF}
