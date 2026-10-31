"""One independently custodied RO-04 plan, never a caller-asserted approval.

No provisioning/writing/signing API. RO-04 must install the fixed public anchor
and complete approval record through independent OS administration. The normal
executor cannot create, replace or choose those records. This does not claim
immunity to a privileged administrator changing/deleting all programme history.
"""
import os
from pathlib import Path
import stat

from . import broad_reader_direct_delivery_spec as spec
from .policy import canonical, fingerprint

PROGRAMME = "tlws-mfdata-internal-reader-dev-v1"
# /etc is a symlink on the supported macOS operator; pin its canonical path.
ANCHOR = Path("/private/etc/mfdataindia/reader-programme-anchor.json")
APPROVAL = Path("/private/etc/mfdataindia/reader-programme-approval.json")
MAX_BYTES = 1024 * 1024


class AuthorityError(ValueError):
    pass


def require(condition):
    if not condition:
        raise AuthorityError("RO04_DIRECT_BOUNDED_PLAN_AUTHORITY_REQUIRED")


def _custodied(path):
    """Race-safe opened-object custody; no symlink or executor-writable parent."""
    require(path in (ANCHOR, APPROVAL) and os.getuid() != 0)
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        mode = os.fstat(directory)
        require(mode.st_uid == 0 and not mode.st_mode & 0o022)
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
            mode = os.fstat(directory)
            require(mode.st_uid == 0 and not mode.st_mode & 0o022)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            mode = os.fstat(fd)
            require(stat.S_ISREG(mode.st_mode) and mode.st_uid == 0
                    and mode.st_nlink == 1 and not mode.st_mode & 0o022
                    and 0 < mode.st_size <= MAX_BYTES)
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(MAX_BYTES + 1)
            require(len(raw) == mode.st_size)
            # Strict shared decoder rejects duplicates/noncanonical/unknown
            # encodings before the complete closed schemas below are checked.
            from .reader_execution_contract import parse_json
            value = parse_json(raw)
            require(raw == (canonical(value) + "\n").encode())
            return value, (mode.st_dev, mode.st_ino, fingerprint(value))
        finally:
            os.close(fd)
    finally:
        os.close(directory)


class Authority:
    def __init__(self):
        try:
            self.anchor, self.anchor_seal = _custodied(ANCHOR)
            self.record, self.record_seal = _custodied(APPROVAL)
            require(type(self.anchor) is dict and set(self.anchor) == {
                "format", "programme", "executor_uid", "journal_directory", "journal_device", "journal_inode",
                "journal_mode", "genesis_sha256", "minimum_epoch"}
                and self.anchor["format"] == "mf-reader-programme-anchor-v1"
                and self.anchor["programme"] == PROGRAMME
                and self.anchor["executor_uid"] == os.getuid()
                and self.anchor["journal_directory"] == str(spec.ARTIFACTS)
                and type(self.anchor["minimum_epoch"]) is int and self.anchor["minimum_epoch"] >= 1)
            spec.sha(self.anchor["genesis_sha256"])
            from .reader_execution_journal import _directory
            fd = _directory(spec.ARTIFACTS)
            try:
                mode = os.fstat(fd)
                require(mode.st_uid == os.getuid() and not mode.st_mode & 0o022
                    and (mode.st_dev, mode.st_ino, stat.S_IMODE(mode.st_mode)) == (
                        self.anchor["journal_device"], self.anchor["journal_inode"], self.anchor["journal_mode"]))
            finally:
                os.close(fd)
        except (OSError, ValueError, TypeError, KeyError):
            raise AuthorityError("RO04_DIRECT_BOUNDED_PLAN_AUTHORITY_REQUIRED") from None

    def verify(self, plan, supplied, *, lineage=None):
        try:
            require(_custodied(ANCHOR) == (self.anchor, self.anchor_seal)
                    and _custodied(APPROVAL) == (self.record, self.record_seal))
            record = self.record
            require(type(record) is dict and set(record) == {
                "format", "programme", "anchor_sha256", "epoch", "prior_lineage_sha256",
                "prior_inventory_sha256", "plan", "approved_by", "stages", "scope", "direct_human_record"}
                and supplied == record and record["format"] == "mf-reader-bounded-plan-authority-v2-custodied"
                and record["programme"] == PROGRAMME and record["anchor_sha256"] == fingerprint(self.anchor)
                and record["plan"] == plan and record["approved_by"] == spec.OPERATOR
                and record["stages"] == ["capture", "grant", "reader"]
                and record["scope"] == "ONE_DEV_CAPTURE_DERIVED_GRANT_AUTHENTICATED_READER"
                and type(record["epoch"]) is int and record["epoch"] >= self.anchor["minimum_epoch"])
            for key in ("prior_lineage_sha256", "prior_inventory_sha256"):
                spec.sha(record[key])
            direct = record["direct_human_record"]
            require(type(direct) is dict and set(direct) == {
                "source", "record_id", "approved_plan_sha256", "approved_at"}
                and direct["source"] == "DIRECT_HUMAN_MESSAGE_VERIFIED_BY_RO04_CUSTODIAN"
                and direct["approved_plan_sha256"] == fingerprint(plan))
            spec.sha(direct["record_id"])
            # Existing strict UTC/window parser, no new custody/freeze claim.
            from .broad_reader_direct_grant import direct_window
            start, end = direct_window(plan["window"])
            from datetime import datetime, timezone
            approved = datetime.fromisoformat(direct["approved_at"])
            require(approved.tzinfo is not None and approved.utcoffset().total_seconds() == 0
                    and approved <= end and approved <= datetime.now(timezone.utc))
            if lineage is not None:
                require(lineage["genesis_sha256"] == self.anchor["genesis_sha256"]
                    and lineage["journal_identity"] == {
                        "device": self.anchor["journal_device"], "inode": self.anchor["journal_inode"],
                        "uid": self.anchor["executor_uid"], "mode": self.anchor["journal_mode"]}
                    and record["epoch"] == len(lineage["operations"]) + 1
                    and record["prior_lineage_sha256"] == fingerprint(lineage)
                    and record["prior_inventory_sha256"] == fingerprint(lineage["inventory"]))
            return fingerprint(record)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise AuthorityError("RO04_DIRECT_BOUNDED_PLAN_AUTHORITY_REQUIRED") from None
