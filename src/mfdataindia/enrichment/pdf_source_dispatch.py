"""Reviewed process boundary, not a replacement historical source authority.

All registered PDF sources predate the patched dependency contract. No source
authority is activated here; diagnostic/policy reads never call this dispatcher.
Direct historical library helpers are not authorized executable commands.
"""

from mfdataindia.enrichment import pdf_security


def require_source_authority():
    pdf_security.reject_unallocated_source_authority()
