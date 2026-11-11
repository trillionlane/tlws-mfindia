"""Reviewed packaged security/entity facts, never an HTTP or environment input.

The initial registry is deliberately empty: existing company names, sectors
and article topics do not establish verified security/entity joins. New facts
require dated evidence and source review; no guessed seed links are shipped.
"""
from .exposure_policy import REGISTRY_VERSION, validate_registry


def load():
    document = {'format': REGISTRY_VERSION, 'records': []}
    validate_registry(document)
    return document
