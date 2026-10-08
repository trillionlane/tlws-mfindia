"""Build mf.fund_family — our own fund identity (tlws_mf_id, slug, tags).

One row per base scheme (fund family), keyed by the AMFI-derived
``mf.fund_variants.group_key`` (see ``normalise.base_scheme_key``). This is
the entity for related-news retrieval and stable URLs: news is about the
fund, not the plan/option variant.

* ``tlws_mf_id``: deterministic UUIDv5 of the group_key under a fixed
  namespace, so the same fund always gets the same ID — stable across
  rebuilds, no counter to coordinate.
* ``slug``: ``normalise.slugify(base_scheme_name)`` — URL/search safe.
* ``tags``: slugged facets for news search: AMC, asset class, sub-asset
  class, risk level (from the family's representative facts row).

Idempotent: upserts on ``group_key``; re-running after new AMFI schemes
arrive adds new families and refreshes tags.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from mfdataindia.load.normalise import slugify
from mfdataindia.store.postgres import PostgresStore

log = logging.getLogger("fund_family")

#: Fixed namespace for our own IDs — changing it re-issues every ID.
FAMILY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "mfdataindia/family")

_FAMILIES_SQL = """
    SELECT v.group_key,
           v.base_scheme_name,
           a.amfi_amc_name                              AS amc,
           ff.asset_class, ff.sub_asset_class, ff.risk_level
      FROM mf.fund_variants v
      JOIN mf.funds f ON f.amfi_scheme_code = v.amfi_scheme_code
      LEFT JOIN mf.amcs a       ON a.amc_id = f.amc_id
      LEFT JOIN mf.fund_facts ff ON ff.amfi_scheme_code = f.amfi_scheme_code
     WHERE v.group_key = %s
     ORDER BY (f.in_scope) DESC,
              (f.plan_type = 'REGULAR') DESC,
              (f.option_type = 'GROWTH') DESC,
              (ff.amfi_scheme_code IS NOT NULL) DESC,
              f.amfi_scheme_code
     LIMIT 1
"""


def tlws_mf_id(group_key: str) -> str:
    """Deterministic UUIDv5 for a fund family (stable for the fund's life)."""
    return str(uuid.uuid5(FAMILY_NAMESPACE, group_key))


def tags_for(amc: Optional[str], asset_class: Optional[str],
             sub_asset_class: Optional[str],
             risk_level: Optional[str]) -> list[str]:
    """Search facets for related-news lookup, in a stable order."""
    tags = [slugify(t) for t in (amc, asset_class, sub_asset_class, risk_level)]
    return list(dict.fromkeys(t for t in tags if t))


def build_fund_family(store: PostgresStore, *, dry_run: bool = False) -> dict[str, Any]:
    """(Re)build mf.fund_family from mf.fund_variants. Returns a report."""
    rep: dict[str, Any] = {"families": 0, "inserted": 0, "updated": 0,
                           "dry_run": dry_run}
    with store.connect().cursor() as cur:
        keys = [r["group_key"] for r in cur.execute(
            "SELECT DISTINCT group_key FROM mf.fund_variants ORDER BY 1")]
        for group_key in keys:
            row = cur.execute(_FAMILIES_SQL, (group_key,)).fetchone()
            if row is None:
                continue
            rep["families"] += 1
            tags = tags_for(row["amc"], row["asset_class"],
                            row["sub_asset_class"], row["risk_level"])
            if dry_run:
                continue
            with store.transaction() as conn, conn.cursor() as c2:
                c2.execute(
                    """
                    INSERT INTO mf.fund_family
                        (tlws_mf_id, group_key, base_scheme_name, slug, tags)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (group_key) DO UPDATE SET
                        base_scheme_name = EXCLUDED.base_scheme_name,
                        slug             = EXCLUDED.slug,
                        tags             = EXCLUDED.tags,
                        updated_at       = now()
                    """,
                    (tlws_mf_id(group_key), group_key, row["base_scheme_name"],
                     slugify(row["base_scheme_name"]), tags))
                if c2.rowcount == 1:
                    rep["inserted"] += 1
    log.info("fund_family: %s", rep)
    return rep