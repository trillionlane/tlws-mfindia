"""Parallel reads cannot change, skip or reorder an original byte proof."""
import threading
import time

import pytest

from mfdataindia.review import archive_verification as checks


def test_every_reference_including_duplicates_is_verified_in_order():
    cloud = object()
    items = [{"n": n} for n in range(13)] + [{"n": 2}]
    called = []
    lock = threading.Lock()
    active = peak = 0

    def verify(actual, item):
        nonlocal active, peak
        assert actual is cloud
        with lock:
            active += 1
            peak = max(active, peak)
        time.sleep(0.002 * (4 - item["n"] % 4))
        with lock:
            called.append(item)
            active -= 1
        return {"proof": item["n"]}

    result = checks.verify_all(verify, cloud, items)
    assert result == [{"proof": item["n"]} for item in items]
    assert sorted(i["n"] for i in called) == sorted(i["n"] for i in items)
    assert 1 < peak <= 4 and active == 0


def test_failure_rejects_without_starting_any_later_chunk():
    called = []
    lock = threading.Lock()

    def verify(cloud, item):
        with lock:
            called.append(item)
        if item == 1:
            raise ValueError("generation or full original bytes differ")
        return item

    with pytest.raises(ValueError, match="full original bytes differ"):
        checks.verify_all(verify, None, list(range(20)))
    assert set(called) <= {0, 1, 2, 3} and 1 in called


@pytest.mark.parametrize("items", [[], list(range(4097))])
def test_bounds_fail_before_any_storage_request(items):
    def forbidden(*args):
        raise AssertionError("must not access storage")

    with pytest.raises(ValueError, match="bounded"):
        checks.verify_all(forbidden, None, items)


def test_single_original_verifier_result_is_unchanged():
    proof = {
        "uri": "gs://fixture#123", "sha256": "a" * 64, "bytes": 123,
        "full_original_bytes_verified": True,
    }
    assert checks.verify_all(lambda cloud, item: proof, None, [object()]) == [proof]
