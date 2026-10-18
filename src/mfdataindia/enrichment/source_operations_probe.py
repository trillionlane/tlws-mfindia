"""Two ordinary Kotak catalogue reads only; never challenge/retry/fallback admission."""

import base64
from datetime import datetime, timezone
import hashlib

from mfdataindia.enrichment import kotak_acquisition
from mfdataindia.enrichment.source_operations import instant


def probe(as_of, *, transport=kotak_acquisition.fetch):
    as_of = instant(as_of)
    records = []
    for request in kotak_acquisition.recent_catalogues(as_of.date()):
        result = {**request, "catalogue_discovery_available": False,
                  "original_retained": False, "book_acquired": False,
                  "fallback_used": False, "redirects_followed": 0, "retries": 0}
        try:
            original = transport(request["source_url"])
            raw = original["content"]
            if (original["source_url"] != request["source_url"]
                    or original["final_url"] != request["source_url"]
                    or original["media_type"] != "application/json"
                    or not isinstance(raw, bytes) or not 0 < len(raw) <= 1048576):
                raise ValueError("exact bounded public catalogue original required")
            # Preserve returned original before parsing; malformed successful
            # responses are evidence, not permission to use the known fallback.
            result.update(original_retained=True, original_base64=base64.b64encode(raw).decode(),
                          original_sha256=hashlib.sha256(raw).hexdigest(), original_bytes=len(raw))
            candidate = kotak_acquisition.catalogue_book(raw, request["source_url"])
            result.update(catalogue_discovery_available=True, candidate_book_url=candidate,
                          outcome="catalogue_candidate_not_source_admission")
        except Exception as error:
            result.update(outcome="unavailable_or_unrecognized", error_type=type(error).__name__)
        result["observed_at"] = datetime.now(timezone.utc).isoformat()
        records.append(result)
    return {"format": "ordinary-kotak-catalogue-probe-v1", "as_of": as_of.isoformat(),
            "requests": records, "maximum_requests": 2, "data_writes": 0,
            "known_document_authority_extended": False, "source_admission_certified": False,
            "database_credentials_mounted": False, "UI": False, "production": False}
