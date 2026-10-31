"""Provider-free current operation identity; no resource or database creation."""
import pytest

from mfdataindia.review import broad_reader_direct_delivery_spec as spec


def test_operation_roles_are_fixed_and_not_dated_release_copies():
    op = "op-0123456789abcdef"
    for kind in ("capture", "grant", "reader"):
        role = spec.role_for(kind, operation_id=op)
        assert role.job == "mf-reader-internal-" + kind + "-" + op
        assert role.worker == "scripts/reader_execution_dev.py"
        assert role.service_account == spec.ROLES[kind].service_account
        assert role.secret == spec.ROLES[kind].secret
        assert role.job not in spec.OLD_CONSUMED_JOBS
        assert spec.role_for(kind) == spec.ROLES[kind]


@pytest.mark.parametrize("value", [None, "", "op-short", "OP-0123456789abcdef",
                                    "op-0123456789abc/def", "dev-bread-cap-261027t"])
def test_only_explicit_current_operation_grammar_selects_new_resources(value):
    if value is None:
        assert spec.role_for("capture", operation_id=value) == spec.ROLES["capture"]
    else:
        with pytest.raises(spec.DeliverySpecError):
            spec.role_for("capture", operation_id=value)
