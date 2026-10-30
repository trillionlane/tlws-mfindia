"""Pure final-source/request preparation for the fixed DEV reader delivery.

This module does not contain a cloud backend or create/run a job. It cannot
grant permission. Its output is only an exact candidate for later reviewed,
create-once provider orchestration.
"""

import hashlib
from pathlib import Path

from mfdataindia.review import broad_reader_direct_delivery_spec as spec
from mfdataindia.review import broad_reader_source_authority as source
from mfdataindia.review.policy import fingerprint


def package_hashes(root, *, kind="capture"):
    """Read exact installed worker/controller/spec bytes; no mutable alias."""
    root = Path(root).resolve()
    spec.require(kind in spec.ROLES, "UNKNOWN_DIRECT_DELIVERY_JOB")
    paths = {
        "worker_sha256": root / spec.ROLES[kind].worker,
        "controller_sha256": Path(__file__).resolve(),
        "spec_sha256": Path(spec.__file__).resolve(),
    }
    result = {}
    for name, path in paths.items():
        spec.require(path.is_file() and not path.is_symlink(),
                     "EXACT_DIRECT_PACKAGE_FILE_REQUIRED")
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def prepare_capture(*, payload, root, final_git_sha, image, source_root):
    """Complete capture request and source binding before any provider I/O."""
    checked = spec.validate_capture_payload(
        payload, final_git_sha=final_git_sha, source_root=source_root)
    spec.require(image == checked["image_binding"]["app"],
                 "EXACT_CAPTURE_APP_IMAGE_REQUIRED")
    packaged = source.verify_packaged_source(source_root)
    hashes = package_hashes(source_root)
    return spec.request("capture", root=root, final_git_sha=final_git_sha,
                        image=image, payload=checked,
                        source_sha256=fingerprint(packaged), **hashes)


def prepare_grant(*, payload, root, final_git_sha, image, source_root):
    """One exact dormant grant candidate; no provider or owner connection."""
    checked = spec.validate_grant_payload(
        payload, final_git_sha=final_git_sha, source_root=source_root)
    spec.require(image == checked["image_binding"]["app"],
                 "EXACT_GRANT_APP_IMAGE_REQUIRED")
    packaged = source.verify_packaged_source(source_root)
    hashes = package_hashes(source_root, kind="grant")
    return spec.request("grant", root=root, final_git_sha=final_git_sha,
                        image=image, payload=checked,
                        source_sha256=fingerprint(packaged), **hashes)


def prepare_reader(*, payload, root, final_git_sha, image, source_root):
    """One exact authenticated-reader candidate, never a grant or TI check."""
    checked = spec.validate_reader_payload(
        payload, final_git_sha=final_git_sha, source_root=source_root)
    spec.require(image == checked["image_binding"]["app"],
                 "EXACT_READER_APP_IMAGE_REQUIRED")
    packaged = source.verify_packaged_source(source_root)
    hashes = package_hashes(source_root, kind="reader")
    return spec.request("reader", root=root, final_git_sha=final_git_sha,
                        image=image, payload=checked,
                        source_sha256=fingerprint(packaged), **hashes)
