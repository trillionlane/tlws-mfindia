"""Source-only selection of one reviewed DEV view-definition variant.

These pins came from PR130's complete CI source compilation. This check is a
necessary constraint, not a replacement for the full live migration ledger,
function/callee attestation, dependency graph, or actual lock witness.
"""

from hashlib import sha256
import json


BASE_AUTHORITY_SHA256 = "efec1d25524362b3caa568a14b096935969de0cc277704d1c04d2ddb95242372"
VARIANT_AUTHORITY_SHA256 = "6230bcdbd2f442ca7e649be14cd0ed6d7fdb45fbbfdf2da399ae7cabf791a0e5"
_PROFILES = {
    "LEGACY_BASE44": (14, "57fba3c06dbae98db62038f3a1654f323ac13739ce100e2a18ab095ed06e6d4b"),
    "BASE44_EXTENDED": (15, "3c5ec8ce41302dd33f43541143ae1a234d3d27585d92ba94cc93210eef43721b"),
    "BASE44_BOOK_V2_SINGLE_PASS": (15, "3fc3053b2491772b66c843fbde77ef98f346c811c886267448bacc7b02ffee74"),
}


class ViewProfileError(ValueError):
    """The live view-definition set is not one exact reviewed source variant."""


def _require(condition):
    if not condition:
        raise ViewProfileError("UNMATCHED_BROAD_READER_SOURCE_PROFILE")


def select(state, candidates, *, base_authority_sha256, variant_authority_sha256):
    """Return one source candidate; every later live gate remains required."""
    _require(base_authority_sha256 == BASE_AUTHORITY_SHA256
             and variant_authority_sha256 == VARIANT_AUTHORITY_SHA256)
    _require(type(state) is dict and type(candidates) is list)
    state_views = [row for row in state.get("relations", [])
                   if type(row) is dict and row.get("relkind") == "v"]
    candidate_views = [row for row in candidates
                       if type(row) is dict and row.get("relkind") == "v"]
    _require(len(state_views) == len(candidate_views)
             and len(state_views) in {count for count, _ in _PROFILES.values()})
    state_by_name = {(r.get("nspname"), r.get("relname")): r for r in state_views}
    candidate_by_name = {(r.get("nspname"), r.get("relname")): r for r in candidate_views}
    _require(len(state_by_name) == len(candidate_by_name) == len(state_views)
             and set(state_by_name) == set(candidate_by_name))
    projection = []
    for name in sorted(state_by_name):
        state_row, candidate = state_by_name[name], candidate_by_name[name]
        _require(all(state_row[k] == candidate[k]
                     for k in ("oid", "nspname", "relname", "relkind", "owner", "definition")))
        seal = state_row["definition"]
        _require(type(seal) is dict and set(seal) == {"is_null", "sha256", "utf8_bytes"}
                 and seal["is_null"] is False and type(seal["sha256"]) is str
                 and len(seal["sha256"]) == 64 and type(seal["utf8_bytes"]) is int
                 and seal["utf8_bytes"] > 0)
        projection.append({"nspname": name[0], "relname": name[1], "definition": seal})
    raw = json.dumps(projection, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode()
    digest = sha256(raw).hexdigest()
    matches = [name for name, (count, expected) in _PROFILES.items()
               if len(projection) == count and digest == expected]
    _require(len(matches) == 1)
    return {"profile": matches[0], "views_sha256": digest, "views": len(projection),
            "source_only_candidate": True, "live_admission": False,
            "permission_enabled": False}
