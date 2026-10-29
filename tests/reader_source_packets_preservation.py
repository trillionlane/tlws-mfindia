"""Finite whole-file inverses for the three approved CI-test edits only.

Every current byte is pinned before replacement. The inverse is the exact
reviewed parent Git blob, whose whole SHA-256 is also pinned. No ignored
region, normalization or test-assertion weakening is accepted.
"""

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = "fb8129decd1ff7ba32e960140426b0b112c04275"
PACKET_PATHS = {
    "tests/test_reader_source_reference_postgres.py": (
        "6d46bea61bcc225dd0c5c88ca58bd6bbf1259e26e599c8be98ac129513620e7a",
        "405d5e1a22a8bf05437274e421072d6cee6cf0788d333a709c11a62d53c9d236"),
    "tests/test_reader_source_variants_postgres.py": (
        "54edfd729bc86e525aa1047045ac7136322681ce1cb202f6133d7a9e12e57ce5",
        "42a8564a5c8f70225285aed7967cb41a34469b57760a773a568ae280169fe5cc"),
    "tests/test_reader_source_variants_preservation.py": (
        "580f855e2b106046fdd9e18d63f27e2f2c36245aa867c7594ca78c08a7f856d9",
        "0967d540bf5618ada598032dad013e50382e2302ac7bb3e71377e5471ec22da4"),
}


def restore_packet_source(raw, path):
    assert type(raw) is bytes and path in PACKET_PATHS
    current_sha, original_sha = PACKET_PATHS[path]
    assert hashlib.sha256(raw).hexdigest() == current_sha, path
    original = subprocess.check_output(["git", "show", PARENT + ":" + path], cwd=ROOT)
    assert hashlib.sha256(original).hexdigest() == original_sha, path
    assert raw != original, path
    return original


def test_exact_three_whole_file_inverses_restore_parent_bytes_and_modes():
    assert len(PACKET_PATHS) == 3
    for path in PACKET_PATHS:
        full = ROOT / path
        assert full.is_file() and not full.is_symlink()
        assert restore_packet_source(full.read_bytes(), path)
