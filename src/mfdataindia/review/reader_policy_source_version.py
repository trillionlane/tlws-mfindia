"""RO-01 current attribution version, not acceptance of consumed diagnostics.

Only the intentional runtime profile integration changes this source pin. The
historical mapping remains in each diagnostic spec as release evidence; a new
image/spec fingerprint is mandatory and no old receipt is re-admitted.
"""

VERSION = "ro01-reader-policy-source-v1"
PATH = "src/mfdataindia/review/privileges.py"
PREVIOUS_SHA256 = "4929964f90292e48f9d81c054e848ed835d190c71a1c54230f82d0e4eb049c33"
CURRENT_SHA256 = '7e2dd4fb88fd5b503778955601c0c362b7af25291cad4f1c7ef40362572b5c7d'


def current_attribution(historical):
    if type(historical) is not dict or historical.get(PATH) != PREVIOUS_SHA256:
        raise ValueError("UNRECOGNIZED_READER_POLICY_SOURCE_PREDECESSOR")
    return {**historical, PATH: CURRENT_SHA256}
