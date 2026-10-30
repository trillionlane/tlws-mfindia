"""Finite full-byte inverses for the runtime-environment entrypoint repair."""


WORKERS = frozenset({
    "scripts/collect_broad_reader_direct_dev.py",
    "scripts/apply_broad_reader_direct_dev.py",
    "scripts/check_broad_reader_direct_dev.py",
})
SPEC = "src/mfdataindia/review/broad_reader_direct_delivery_spec.py"
IDENTITY_TEST = "tests/test_broad_reader_direct_fresh_capture_identity.py"
GUARD = "tests/test_broad_reader_direct_no_freeze_parent_preservation.py"
MODIFIED = WORKERS | {SPEC, IDENTITY_TEST, GUARD}
NEW_ADDED = frozenset({
    "tests/broad_reader_runtime_environment_preservation.py",
    "tests/test_broad_reader_direct_runtime_environment.py",
    "tests/test_broad_reader_direct_runtime_environment_parent.py",
})
GUARD_IMPORT = (
    b"from broad_reader_runtime_environment_preservation import NEW_ADDED, restore_runtime_parent\n"
)
GUARD_READ_OLD = b"            raw = path.read_bytes()\n"
GUARD_READ_NEW = GUARD_READ_OLD + b"            raw = restore_runtime_parent(name, raw)\n"
GUARD_HASH_OLD = (
    b"    assert hashlib.sha256((ROOT / NEW_IDENTITY_TEST).read_bytes()).hexdigest() == NEW_IDENTITY_SHA256\n"
)
GUARD_HASH_NEW = (
    b"    assert hashlib.sha256(restore_runtime_parent(\n"
    b"        NEW_IDENTITY_TEST, (ROOT / NEW_IDENTITY_TEST).read_bytes()\n"
    b"    )).hexdigest() == NEW_IDENTITY_SHA256\n"
)
GUARD_DELTA_OLD = b"                                                NEW_IDENTITY_TEST, SELF}\n"
GUARD_DELTA_NEW = b"                                                NEW_IDENTITY_TEST, SELF} | NEW_ADDED\n"


def replace_once(raw, current, original):
    assert raw.count(current) == 1
    return raw.replace(current, original, 1)


def restore_runtime_parent(name, raw):
    if name in WORKERS:
        return replace_once(raw, b"print(run_worker(dict(os.environ)), flush=True)",
                            b"print(run_worker(os.environ), flush=True)")
    if name == SPEC:
        raw = replace_once(raw, b'    "mf-reader-direct-capture-261030b",\n', b"")
        raw = replace_once(raw, b'"dev-bread-direct-cap-261030c"',
                           b'"dev-bread-direct-cap-261030b"')
        return replace_once(raw, b'"mf-reader-direct-capture-261030c"',
                            b'"mf-reader-direct-capture-261030b"')
    if name == IDENTITY_TEST:
        raw = replace_once(raw, b'NEW_JOB = "mf-reader-direct-capture-261030c"',
                           b'NEW_JOB = "mf-reader-direct-capture-261030b"')
        return replace_once(raw, b'assert capture.rollout_id == "dev-bread-direct-cap-261030c"',
                            b'assert capture.rollout_id == "dev-bread-direct-cap-261030b"')
    if name == GUARD:
        raw = replace_once(raw, GUARD_IMPORT + b"\n", b"")
        raw = replace_once(raw, GUARD_READ_NEW, GUARD_READ_OLD)
        raw = replace_once(raw, GUARD_HASH_NEW, GUARD_HASH_OLD)
        return replace_once(raw, GUARD_DELTA_NEW, GUARD_DELTA_OLD)
    return raw
