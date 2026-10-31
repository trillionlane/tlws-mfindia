"""RO-01 current attribution version, not acceptance of consumed diagnostics.

Only the intentional runtime profile integration changes this source pin. The
historical mapping remains in each diagnostic spec as release evidence; a new
image/spec fingerprint is mandatory and no old receipt is re-admitted.
"""

HISTORICAL_RO01_VERSION = "ro01-reader-policy-source-v1"
VERSION = "ro02-reader-policy-source-v2"
PATH = "src/mfdataindia/review/privileges.py"
PREVIOUS_SHA256 = "4929964f90292e48f9d81c054e848ed835d190c71a1c54230f82d0e4eb049c33"
CURRENT_SHA256 = '7e2dd4fb88fd5b503778955601c0c362b7af25291cad4f1c7ef40362572b5c7d'


def historical_ro01_attribution(historical):
    if type(historical) is not dict or historical.get(PATH) != PREVIOUS_SHA256:
        raise ValueError("UNRECOGNIZED_READER_POLICY_SOURCE_PREDECESSOR")
    return {**historical, PATH: CURRENT_SHA256}


CONFIGURATION_PATH = "src/mfdataindia/enrichment/current_facts_store.py"
CONFIGURATION_PREVIOUS_SHA256 = "380f656f5d70a4870c9c86d2f821cc14c418128a1ffb51cf9124c5e3b8b5afc0"
CONFIGURATION_CURRENT_SHA256 = '700f1e53771ac9212fcd57805788bf0b0753b8cb5b57ca9970304fb25556149c'

def current_attribution(historical):
    value = historical_ro01_attribution(historical)
    if value.get(CONFIGURATION_PATH) != CONFIGURATION_PREVIOUS_SHA256:
        raise ValueError("UNRECOGNIZED_READER_CONFIGURATION_SOURCE_PREDECESSOR")
    return {**value, CONFIGURATION_PATH: CONFIGURATION_CURRENT_SHA256}
