#!/usr/bin/env python3
"""Current packaged worker and provider-free complete-plan validation CLI."""
import argparse
import os
from pathlib import Path
import stat
import sys

from mfdataindia.review import reader_execution_contract as contract
from mfdataindia.review import reader_execution_diagnostics as diagnostics
from mfdataindia.review import reader_execution_worker as worker
from mfdataindia.review import reader_execution_controller as controller
from mfdataindia.review import reader_execution_backend as backend
from mfdataindia.review.reader_execution_application import ApplicationCheck
from mfdataindia.review.auth import AssertionVerifier
from mfdataindia.review.policy import canonical


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        # argparse's default echoes unknown argument values, which may be a DSN.
        raise contract.ContractError("INVALID_COMMAND_LINE")


def load(path):
    contract.require(path is not None, "COMPLETE_CANONICAL_INPUT_REQUIRED")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        mode = os.fstat(fd)
        contract.require(stat.S_ISREG(mode.st_mode) and mode.st_nlink == 1
            and mode.st_uid == os.getuid() and not mode.st_mode & 0o077
            and 0 < mode.st_size <= contract.spec.MAX_PAYLOAD_BYTES,
            "PRIVATE_CANONICAL_INPUT_FILE_REQUIRED")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return contract.parse_json(handle.read(contract.spec.MAX_PAYLOAD_BYTES + 1))
    finally:
        os.close(fd)


def main():
    parser = Parser(description=__doc__)
    parser.add_argument("command", choices=("worker", "validate", "execute", "inspect"), nargs="?", default="worker")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--expected-git")
    parser.add_argument("--authority", type=Path)
    parser.add_argument("--trust", type=Path)
    parser.add_argument("--operation-root", type=Path)
    try:
        args = parser.parse_args()
    except contract.ContractError as error:
        print(canonical(diagnostics.report(error, stage="validation", worker=False)), file=sys.stderr)
        raise SystemExit(1) from None
    root = Path(__file__).resolve().parents[1]
    if args.command == "worker":
        # The worker snapshots os.environ itself, not an exact-dict type bypass.
        return worker.main(source_root=root)
    try:
        if args.command == "inspect":
            contract.require(args.operation_root is not None, "ORIGINAL_OPERATION_ROOT_REQUIRED")
            result = controller.inspect(args.operation_root, source_root=root,
                                        inspector_factory=backend.Inspector)
        else:
            contract.require(args.expected_git is not None, "COMPLETE_CANONICAL_INPUT_REQUIRED")
            plan = load(args.plan)
            if args.command == "validate":
                result = contract.dry_run(plan, source_root=root, expected_git=args.expected_git)
            else:
                # No provider secret lookup or signer provisioning. RO-04 must
                # supply the existing reader identity and delegated assertions.
                public = load(args.trust)
                contract.require(type(public) is dict and set(public) == {
                    "public_key", "issuer", "audience", "caller", "allowed_scopes"},
                    "APPLICATION_TRUST_CONTEXT_REQUIRED")
                verifier = AssertionVerifier(**{**public, "allowed_scopes": frozenset(public["allowed_scopes"])})
                env = dict(os.environ)
                app = ApplicationCheck(env.get("MFDATAINDIA_READER_DSN"), verifier,
                    env.get("READER_EXECUTION_READ_ASSERTION"), env.get("READER_EXECUTION_ADMIN_ASSERTION"))
                result = controller.execute(plan, load(args.authority), app, source_root=root,
                    expected_git=args.expected_git, backend_factory=backend.Backend)
    except BaseException as error:
        print(canonical(diagnostics.report(error, stage="validation", worker=False)), file=sys.stderr)
        raise SystemExit(1) from None
    print(canonical(result))
    if args.command == "execute" and result["state"] != "OPERATION_APPLICATION_VERIFIED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
