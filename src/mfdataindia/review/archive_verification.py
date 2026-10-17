"""Bounded parallel original reads; preserve the complete verifier and order."""
from concurrent.futures import ThreadPoolExecutor

WORKERS = 4
MAX_ORIGINALS = 4096


def verify_all(verifier, cloud, items):
    items = list(items)
    if not 1 <= len(items) <= MAX_ORIGINALS:
        raise ValueError("bounded nonempty complete source archive list required")
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        # Queue one bounded chunk only: failures cannot leave the entire
        # remaining list running in the background. Never sample or dedupe.
        for start in range(0, len(items), WORKERS):
            futures = [
                pool.submit(verifier, cloud, item)
                for item in items[start : start + WORKERS]
            ]
            results.extend(future.result() for future in futures)
    if len(results) != len(items):
        raise ValueError("every original requires complete byte verification")
    return results
