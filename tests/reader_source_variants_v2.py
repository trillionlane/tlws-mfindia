"""Versioned CI-only extended collection for two bounded evidence packets.

The v1 collector and parser remain byte-identical, including their 8 MiB
whole-document limit. This v2 collector relaxes only the *pre-split* in-memory
document cap to 16 MiB; every emitted part still passes the old 8 MiB envelope.
"""

from pathlib import Path
import re

import reader_source_reference as legacy
import reader_source_variants as old


def collect_extended(conn):
    raw = legacy.collect(conn)
    objects = {(o["catalog"], o["oid"]): o["row"] for o in raw["objects"]}
    reader = [key[1] for key, row in objects.items()
              if key[0] == "pg_authid" and row["rolname"] == "mfdata_verified_reader"]
    old.require(len(reader) == 1)
    acl = []
    for cat in sorted(old.ACL):
        ids = sorted(key[1] for key in objects if key[0] == cat)
        params = (reader[0], ids) if cat == "pg_proc" else (ids,)
        values = old.query(conn, old.acl_query(cat), params,
                           ("oid", "raw_acl_is_null", "atoms", "reader_execute"))
        old.require([v["oid"] for v in values] == ids)
        acl.extend({"catalog": cat, **v} for v in values)
    candidate = old.query(conn, old.inventory.CANDIDATE_SQL, (),
                          old.inventory.CANDIDATE_COLUMNS)
    ids = sorted(key[1] for key in objects if key[0] == "pg_class")
    inheritance = old.query(
        conn,
        "SELECT inhrelid::bigint AS inhrelid,inhparent::bigint AS inhparent,inhseqno,inhdetachpending "
        "FROM pg_catalog.pg_inherits WHERE inhparent=ANY(%s::oid[]) OR inhrelid=ANY(%s::oid[]) "
        "ORDER BY inhparent,inhrelid,inhseqno",
        (ids, ids), ("inhrelid", "inhparent", "inhseqno", "inhdetachpending"),
    )
    result = {"format": "extended-CI-source-reference-NOT-admission-v1",
              "legacy": raw, "acl_rows": acl, "relation_candidate": candidate,
              "inheritance_rows": inheritance}
    old.validate_extended(result)
    old.require(len(old.canonical(result).encode()) <= 2 * legacy.MAX_RAW)
    return result


def validate_payload(payload, profile, *, expected_git):
    """Exact v1 logical checks after reconstructing the complete old payload."""
    old.require(profile in old.PROFILES)
    old.require(type(expected_git) is str and re.fullmatch("[0-9a-f]{40}", expected_git))
    old.require(set(payload) == set(
        "format profile compiler_git compiler_sources base_authority_sha256 "
        "variant_authority_sha256 authority raw raw_sha256 grant_state portable_sha256 "
        "portable_projection_format runtime_authority permission_acceptance".split()))
    old.require(payload["format"] == "extended-source-calibration-NOT-live-acceptance-v1")
    old.require(payload["compiler_git"] == expected_git)
    root = Path(__file__).resolve().parents[1]
    old.require(payload["compiler_sources"] == {
        path: old.digest((root / path).read_bytes())
        for path in ("tests/reader_source_reference.py", "tests/reader_source_variants.py")})
    old.require(payload["base_authority_sha256"] == legacy.AUTHORITY_SHA)
    old.require(payload["authority"] == old.variant_authority(root))
    old.require(payload["raw_sha256"] == old.digest(old.canonical(payload["raw"]).encode()))
    old.require(payload["profile"] == profile
                and payload["variant_authority_sha256"] == old.AUTHORITY_SHA)
    old.require(payload["runtime_authority"] is payload["permission_acceptance"] is False)
    old.state_shape(payload["grant_state"])
    databases = payload["grant_state"]["databases"]
    old.require(len(databases) == 1)
    old.require(databases[0]["oid"] == payload["raw"]["legacy"]["database"]["oid"])
    old.require(databases[0]["datname"] == payload["raw"]["legacy"]["database"]["datname"]
                == payload["raw"]["legacy"]["context"]["database"])
    old.require(old.grant_roots(payload["grant_state"]) == old.candidate_roots(payload["raw"]))
    portable = old.portable_source_reference(payload["raw"])
    old.require(payload["portable_projection_format"] == portable["format"])
    old.require(payload["portable_sha256"] == old.digest(old.canonical(portable).encode()))
    return payload
