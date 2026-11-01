"""Durable create-only operation records. No reset, deletion or retry API."""
import fcntl
import os
from pathlib import Path
import re
import stat

from . import reader_execution_contract as contract
from . import reader_execution_diagnostics as diagnostics
from .policy import canonical, fingerprint


class JournalError(contract.ContractError):
    pass


def _require(condition):
    if not condition:
        raise JournalError("IMMUTABLE_JOURNAL_UNCONFIRMED")


def _directory(path):
    path = Path(path)
    _require(path.is_absolute() and all(part not in (".", "..") for part in path.parts))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


class ProgramLock:
    """One programme lock, not an execution identity or a mutation approval."""
    def __init__(self, base, *, existing=False):
        self.base = Path(base)
        self.directory = _directory(self.base)
        self.fd = None
        try:
            flags = os.O_RDWR | os.O_NOFOLLOW | (0 if existing else os.O_CREAT)
            self.fd = os.open(".reader-execution-program.lock", flags, 0o600, dir_fd=self.directory)
            mode = os.fstat(self.fd)
            _require(stat.S_ISREG(mode.st_mode) and mode.st_nlink == 1
                     and mode.st_uid == os.getuid() and not mode.st_mode & 0o077)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not existing:
                os.fsync(self.fd)
                os.fsync(self.directory)
        except BaseException:
            self.close()
            raise JournalError("PROGRAM_LOCK_OR_DURABILITY_UNCONFIRMED") from None

    def roots(self):
        return [self.base / name for name in sorted(os.listdir(self.directory))
                if name.startswith("mf-reader-internal-")]

    def identity(self):
        mode = os.fstat(self.directory)
        _require(mode.st_uid == os.getuid() and not mode.st_mode & 0o022)
        return {"device": mode.st_dev, "inode": mode.st_ino, "uid": mode.st_uid,
                "mode": stat.S_IMODE(mode.st_mode)}

    def genesis(self):
        # Independently provisioned/anchored by RO-04. Never auto-initialize a
        # fresh history after missing local records. This is not an execution.
        proxy = object.__new__(Journal)
        proxy.fd = self.directory
        try:
            value = proxy._read(".reader-execution-genesis.json")
            _require(type(value) is dict and set(value) == {"format", "programme", "genesis_id"}
                and value["format"] == "mf-reader-execution-genesis-v1"
                and value["programme"] == "tlws-mfdata-internal-reader-dev-v1")
            contract.spec.sha(value["genesis_id"])
            return value
        except (OSError, ValueError, TypeError):
            raise JournalError("PROGRAMME_HISTORY_MISSING_NO_AUTOMATIC_RESET") from None

    def create(self, plan):
        _require(self.fd is not None)
        name = "mf-reader-internal-" + plan["operation_id"]
        _require(re.fullmatch(r"mf-reader-internal-op-[a-z0-9]{12,24}", name))
        # No exist_ok, cleanup delete, or reuse of a spent operation.
        os.mkdir(name, 0o700, dir_fd=self.directory)
        os.fsync(self.directory)  # Root allocation durable before any cloud mutation.
        journal = Journal(self.base / name, parent_fd=self.directory)
        try:
            journal._exclusive("plan.json", plan)
            return journal
        except BaseException:
            journal.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.directory is not None:
            os.close(self.directory)
            self.directory = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class Journal:
    def __init__(self, root, *, parent_fd=None):
        self.root = Path(root)
        _require(re.fullmatch(r"mf-reader-internal-op-[a-z0-9]{12,24}", self.root.name))
        self.fd = (_directory(self.root) if parent_fd is None else
            os.open(self.root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd))
        mode = os.fstat(self.fd)
        try:
            _require(mode.st_uid == os.getuid() and not mode.st_mode & 0o077)
        except BaseException:
            self.close()
            raise

    def _exclusive(self, name, value):
        _require(name == "plan.json" or re.fullmatch(r"event-[0-9]{4}\.json", name))
        raw = (canonical(value) + "\n").encode()
        _require(0 < len(raw) <= contract.spec.MAX_PAYLOAD_BYTES)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
            os.fsync(self.fd)  # File AND containing-directory durability before caller proceeds.
        finally:
            os.close(fd)

    def _read(self, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            mode = os.fstat(fd)
            _require(stat.S_ISREG(mode.st_mode) and mode.st_nlink == 1
                     and mode.st_uid == os.getuid() and not mode.st_mode & 0o077
                     and 0 < mode.st_size <= contract.spec.MAX_PAYLOAD_BYTES)
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(contract.spec.MAX_PAYLOAD_BYTES + 1)
            value = contract.parse_json(raw)
            _require(raw == (canonical(value) + "\n").encode())
            return value
        finally:
            os.close(fd)

    def plan(self):
        value = self._read("plan.json")
        _require(self.root.name == "mf-reader-internal-" + value["operation_id"])
        return value

    def events(self):
        names = sorted(os.listdir(self.fd))
        _require("plan.json" in names and all(name == "plan.json" or
                 re.fullmatch(r"event-[0-9]{4}\.json", name) for name in names))
        names.remove("plan.json")
        result, previous = [], None
        for index, name in enumerate(names):
            _require(name == f"event-{index:04d}.json")
            value = self._read(name)
            _require(type(value) is dict and set(value) == {
                "format", "sequence", "previous_sha256", "state", "kind", "grant_effect", "data", "sha256"})
            body = {key: item for key, item in value.items() if key != "sha256"}
            _require(value["format"] == "mf-reader-execution-event-v1"
                     and type(value["sequence"]) is int and value["sequence"] == index
                     and value["previous_sha256"] == previous and value["sha256"] == fingerprint(body)
                     and value["grant_effect"] in diagnostics.EFFECTS
                     and (value["kind"] is None or value["kind"] in contract.KINDS)
                     and type(value["state"]) is str and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", value["state"])
                     and type(value["data"]) is dict)
            result.append(value)
            previous = value["sha256"]
        return result

    def append(self, *, state, kind=None, effect=diagnostics.NO_GRANT, data=None):
        old = self.events()
        _require(len(old) < 1000 and effect in diagnostics.EFFECTS)
        body = {"format": "mf-reader-execution-event-v1", "sequence": len(old),
                "previous_sha256": old[-1]["sha256"] if old else None,
                "state": state, "kind": kind, "grant_effect": effect,
                "data": {} if data is None else data}
        value = {**body, "sha256": fingerprint(body)}
        self._exclusive(f"event-{len(old):04d}.json", value)
        return value

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
