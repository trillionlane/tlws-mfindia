"""Exact admitted PR131 squash-main tree plus only the fixed Phase C DEV delivery delta."""

import hashlib
import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REVIEWED_PARENT = "850873cc6469cf32f4bc427b7e56117983feb3b5"
ADMITTED_PR133_MAIN = "9337a4d2d28c1c428039d20e8fbc3492b4c073eb"
NO_FREEZE_MODIFIED = {
    "scripts/apply_broad_reader_direct_dev.py",
    "scripts/check_broad_reader_direct_dev.py",
    "scripts/collect_broad_reader_direct_dev.py",
    "src/mfdataindia/review/broad_reader_direct_delivery_spec.py",
    "src/mfdataindia/review/broad_reader_direct_grant.py",
    "src/mfdataindia/review/broad_reader_direct_rollout_controller.py",
    "tests/test_broad_reader_direct_capture_stage_diagnostic.py",
    "tests/test_broad_reader_direct_capture_worker.py",
    "tests/test_broad_reader_direct_delivery_preservation.py",
    "tests/test_broad_reader_direct_grant_postgres.py",
    "tests/test_broad_reader_direct_rollout_controller.py",
}
GUARD = "tests/test_broad_reader_direct_parent_preservation.py"
READER = "src/mfdataindia/review/broad_reader_direct_reader_check.py"
READER_TEST = "tests/test_broad_reader_direct_reader_check.py"
NATIVE_TEST = "tests/test_broad_reader_direct_grant_postgres.py"
ADDED = {
    "scripts/apply_broad_reader_direct_dev.py",
    "scripts/check_broad_reader_direct_dev.py",
    "scripts/collect_broad_reader_direct_dev.py",
    "scripts/operate_broad_reader_direct_dev.py",
    "scripts/smoke_broad_reader_direct_delivery_binding.py",
    "src/mfdataindia/review/broad_reader_direct_capacity.py",
    "src/mfdataindia/review/broad_reader_direct_delivery_backend.py",
    "src/mfdataindia/review/broad_reader_direct_delivery_controller.py",
    "src/mfdataindia/review/broad_reader_direct_delivery_spec.py",
    "src/mfdataindia/review/broad_reader_direct_rollout_controller.py",
    "tests/test_broad_reader_direct_capacity.py",
    "tests/test_broad_reader_direct_capture_stage_diagnostic.py",
    "tests/test_broad_reader_direct_capture_worker.py",
    "tests/test_broad_reader_direct_delivery_backend.py",
    "tests/test_broad_reader_direct_delivery_binding.py",
    "tests/test_broad_reader_direct_delivery_preservation.py",
    "tests/test_broad_reader_direct_delivery_spec.py",
    "tests/test_broad_reader_direct_grant_worker.py",
    "tests/test_broad_reader_direct_reader_worker.py",
    "tests/test_broad_reader_direct_rollout_controller.py",
}
OLD_PARENT_LINE = b'PARENT = "b56fe2ae0806fc0eaa80692be82c854bd429af84"\n'
NEW_PARENT_LINE = (OLD_PARENT_LINE +
                   b'REVIEWED_MAIN = "850873cc6469cf32f4bc427b7e56117983feb3b5"\n')
OLD_DIFF = b'PARENT + "..HEAD"'
NEW_DIFF = b'PARENT + ".." + REVIEWED_MAIN'


def original_guard(raw):
    assert raw.count(NEW_PARENT_LINE) == 1
    assert raw.count(NEW_DIFF) == 1
    restored = raw.replace(NEW_PARENT_LINE, OLD_PARENT_LINE, 1)
    restored = restored.replace(NEW_DIFF, OLD_DIFF, 1)
    assert restored != raw
    return restored


def original_reader(raw):
    assert raw.count(b'"isolation": "repeatable read"') == 1
    assert raw.count(b'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY') == 1
    return (raw.replace(b'"isolation": "repeatable read"',
                        b'"isolation": "read committed"', 1)
            .replace(b'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY',
                     b'BEGIN ISOLATION LEVEL READ COMMITTED READ ONLY', 1))


def original_reader_test(raw):
    assert raw.count(b'"isolation": "repeatable read"') == 1
    return raw.replace(b'"isolation": "repeatable read"',
                       b'"isolation": "read committed"', 1)


def original_native_test(raw):
    added = b'        assert check["context"]["isolation"] == "repeatable read"\n'
    assert raw.count(added) == 1
    return raw.replace(added, b'', 1)


def test_every_reviewed_parent_byte_mode_and_finite_delta_is_preserved():
    changed = subprocess.check_output(
        ["git", "diff", "--name-status", "--no-renames",
         REVIEWED_PARENT + ".." + ADMITTED_PR133_MAIN], cwd=ROOT).decode().splitlines()
    assert set(changed) == {"M\t" + path for path in
                            (GUARD, READER, READER_TEST, NATIVE_TEST)} | {
                                "A\t" + path for path in ADDED}
    records = subprocess.check_output(
        ["git", "ls-tree", "-r", "-z", REVIEWED_PARENT], cwd=ROOT).split(b"\0")
    records = [row for row in records if row]
    assert len(records) == 1032
    for record in records:
        identity, encoded_name = record.split(b"\t", 1)
        mode, kind, blob = identity.decode().split()
        name = encoded_name.decode()
        path = ROOT / name
        assert kind == "blob"
        if mode == "120000":
            assert path.is_symlink()
            raw = os.readlink(path).encode()
        else:
            assert path.is_file() and not path.is_symlink()
            assert stat.S_ISREG(path.stat().st_mode)
            assert bool(path.stat().st_mode & 0o111) == (mode == "100755")
            raw = path.read_bytes()
            if name in NO_FREEZE_MODIFIED:
                # The new exact-parent guard pins every current byte. This
                # historical proof still checks the admitted PR133 tree.
                raw = subprocess.check_output(
                    ["git", "show", ADMITTED_PR133_MAIN + ":" + name], cwd=ROOT)
            if name == GUARD:
                raw = original_guard(raw)
            elif name == READER:
                raw = original_reader(raw)
            elif name == READER_TEST:
                raw = original_reader_test(raw)
            elif name == NATIVE_TEST:
                raw = original_native_test(raw)
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        assert actual == blob, name
    for name in ADDED:
        path = ROOT / name
        assert path.is_file() and not path.is_symlink()
        assert stat.S_ISREG(path.stat().st_mode)
        assert not (path.stat().st_mode & 0o111)
