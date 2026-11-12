"""Resolve AMFI catalogue changes against existing family identities."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from uuid import uuid4

from psycopg.types.json import Jsonb

from mfdataindia.ingest.amfi_navall import AmfiScheme
from mfdataindia.load.amfi_to_store import clean_isin, variant_rows
from mfdataindia.load.fund_family import tags_for
from mfdataindia.load.normalise import slugify

# Issuer continuity confirmed by official Addendum No. 21/2026-27.
# Resolve the feed label to the existing AMC row and its registered bindings.
ISSUER_ALIASES = {"Zerodha CASE Mutual Fund": "Zerodha Mutual Fund"}

# NSE/NMFTM/72131 lists the payout and reinvestment ISINs for both plans.
# The prior AMFI rows published only payout; the current feed fills the second slot.
SUNDARAM_IDCW_SLOTS = {
    154124: ("DIRECT", "INF903JA1MA8", "INF903JA1MB6"),
    154125: ("REGULAR", "INF903JA1MD2", "INF903JA1ME0"),
}


@dataclass(frozen=True)
class Variant:
    code: int
    key: str
    issuer: str
    plan: str
    option: str
    isins: tuple[str | None, str | None]
    name: str = ""
    in_scope: bool = False
    defunct: bool = False


@dataclass(frozen=True)
class Rename:
    family_id: str
    old_key: str
    new_key: str
    codes: tuple[int, ...]


@dataclass(frozen=True)
class Resolution:
    renames: tuple[Rename, ...]
    holds: tuple[tuple[str, str], ...]


def normalize_issuers(schemes: list[AmfiScheme]) -> list[AmfiScheme]:
    return [
        replace(scheme, amc=ISSUER_ALIASES[scheme.amc])
        if scheme.amc in ISSUER_ALIASES else scheme
        for scheme in schemes
    ]


def read_identities(conn) -> tuple[dict[int, Variant], dict[str, str]]:
    rows = conn.execute("""
        SELECT f.amfi_scheme_code, COALESCE(v.group_key, '') AS group_key,
               a.amfi_amc_name, f.plan_type, f.option_type, f.isin_growth_or_div_payout,
               f.isin_div_reinvest, f.scheme_name, f.in_scope, f.is_defunct
        FROM mf.funds f JOIN mf.amcs a USING(amc_id)
        LEFT JOIN mf.fund_variants v USING(amfi_scheme_code)
        ORDER BY f.amfi_scheme_code
    """).fetchall()
    variants = {
        r["amfi_scheme_code"]: Variant(
            r["amfi_scheme_code"], r["group_key"], r["amfi_amc_name"],
            r["plan_type"], r["option_type"],
            (clean_isin(r["isin_growth_or_div_payout"]), clean_isin(r["isin_div_reinvest"])),
            r["scheme_name"], r["in_scope"], r["is_defunct"],
        )
        for r in rows
    }
    families = conn.execute("SELECT group_key, tlws_mf_id FROM mf.fund_family").fetchall()
    return variants, {row["group_key"]: row["tlws_mf_id"] for row in families}


def read_source(schemes: list[AmfiScheme]) -> tuple[dict[int, Variant], set[int]]:
    keys: dict[int, set[str]] = defaultdict(set)
    for code, key, *_ in variant_rows(schemes):
        keys[code].add(key)
    incoming: dict[int, Variant] = {}
    conflicts = {code for code, values in keys.items() if len(values) != 1}
    for scheme in schemes:
        if not scheme.amfi_scheme_code.isascii() or not scheme.amfi_scheme_code.isdecimal():
            continue
        code = int(scheme.amfi_scheme_code)
        group = keys.get(code, set())
        row = Variant(
            code, next(iter(group)) if len(group) == 1 else "",
            scheme.amc, scheme.plan_type, scheme.option,
            (clean_isin(scheme.isin_div_payout_or_growth), clean_isin(scheme.isin_div_reinvestment)),
            scheme.scheme_name, scheme.in_scope, scheme.is_defunct,
        )
        if code in incoming and incoming[code] != row:
            conflicts.add(code)
        incoming[code] = row
    return incoming, conflicts


def _identifiers_continue(old: Variant, new: Variant) -> bool:
    if (old.code, old.issuer, old.plan, old.option) != (new.code, new.issuer, new.plan, new.option):
        return False
    if old.isins == new.isins:
        return True
    evidence = SUNDARAM_IDCW_SLOTS.get(old.code)
    if evidence is None:
        return False
    plan, payout, reinvestment = evidence
    return (old.issuer == "Sundaram Mutual Fund" and old.plan == plan and old.option == "IDCW"
            and old.key == new.key == "SUNDARAM INCOME PLUS ARBITRAGE ACTIVE FOF"
            and old.isins == (payout, None) and new.isins == (payout, reinvestment))


def matches(old: Variant, new: Variant) -> bool:
    return any(old.isins) and _identifiers_continue(old, new)


def resolve(
    before: dict[int, Variant], families: dict[str, str],
    incoming: dict[int, Variant], conflicts: set[int],
) -> Resolution:
    current_members: dict[str, set[int]] = defaultdict(set)
    next_members: dict[str, set[int]] = defaultdict(set)
    holds: set[tuple[str, str]] = {(str(code), "SOURCE_CODE_CONFLICT") for code in conflicts}
    for code, old in before.items():
        if old.key:
            current_members[old.key].add(code)
        new = incoming.get(code)
        if new and not _identifiers_continue(old, new):
            holds.add((str(code), "IDENTIFIERS_CHANGED"))
        key = new.key if new and new.key else old.key
        if old.key and new and not new.key:
            holds.add((str(code), "MEMBERSHIP_UNRESOLVED"))
        if key:
            next_members[key].add(code)
    for code in incoming.keys() - before.keys():
        if incoming[code].key:
            next_members[incoming[code].key].add(code)
    for key, codes in next_members.items():
        issuers = {(incoming.get(code) or before[code]).issuer for code in codes}
        if len(issuers) != 1:
            holds.add((key, "MULTIPLE_ISSUERS"))
    for key in set(families) ^ set(current_members):
        holds.add((key, "COMMITTED_FAMILY_MEMBERSHIP_INCONSISTENT"))

    renames = []
    for old_key, codes in current_members.items():
        changed = {incoming[code].key for code in codes
                   if code in incoming and incoming[code].key and incoming[code].key != old_key}
        if not changed:
            continue
        new_key = next(iter(changed)) if len(changed) == 1 else ""
        if (not new_key or new_key in families or old_key not in families
                or next_members[new_key] != codes
                or any(code not in incoming or code in conflicts or incoming[code].key != new_key
                       or not matches(before[code], incoming[code]) for code in codes)):
            holds.add((old_key, "RENAME_SPLIT_MERGE_OR_IDENTITY_CONFLICT"))
            continue
        renames.append(Rename(families[old_key], old_key, new_key, tuple(sorted(codes))))
    return Resolution(tuple(sorted(renames, key=lambda row: row.old_key)), tuple(sorted(holds)))


def apply_renames(conn, renames: tuple[Rename, ...], incoming: dict[int, Variant], *,
                  source_hash: str, snapshot_date) -> None:
    """Called in the catalogue transaction after source and membership checks."""
    for rename in renames:
        family = conn.execute("""
            SELECT base_scheme_name, slug FROM mf.fund_family
            WHERE tlws_mf_id = %s AND group_key = %s FOR UPDATE
        """, (rename.family_id, rename.old_key)).fetchone()
        if family is None:
            raise RuntimeError("family changed since reconciliation")
        rows = conn.execute("""
            SELECT amfi_scheme_code FROM mf.fund_variants
            WHERE group_key = %s ORDER BY amfi_scheme_code FOR UPDATE
        """, (rename.old_key,)).fetchall()
        if tuple(row["amfi_scheme_code"] for row in rows) != rename.codes:
            raise RuntimeError("family membership changed since reconciliation")
        conn.execute("""
            UPDATE mf.fund_family SET group_key = %s, updated_at = now()
            WHERE tlws_mf_id = %s
        """, (rename.new_key, rename.family_id))
        conn.execute("""
            UPDATE mf.fund_variants v
            SET group_key = %s, base_scheme_name = incoming.name, updated_at = now()
            FROM unnest(%s::integer[], %s::text[]) AS incoming(code, name)
            WHERE v.group_key = %s AND v.amfi_scheme_code = incoming.code
        """, (rename.new_key, list(rename.codes),
              [incoming[code].name for code in rename.codes], rename.old_key))
        conn.execute("""
            INSERT INTO mf_identity.family_renames
                (family_id, former_key, current_key, former_name, former_slug, source_hash,
                 source_date, scheme_codes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (rename.family_id, rename.old_key, rename.new_key, family["base_scheme_name"],
              family["slug"], source_hash, snapshot_date, Jsonb(rename.codes)))


def rebuild(conn) -> None:
    """Maintain legacy group keys and stable URLs alongside the durable mapping."""
    representatives = conn.execute("""
        SELECT DISTINCT ON (v.group_key)
               v.group_key, f.scheme_name AS base_scheme_name, a.amfi_amc_name,
               ff.asset_class, ff.sub_asset_class, ff.risk_level
        FROM mf.fund_variants v JOIN mf.funds f USING(amfi_scheme_code)
        JOIN mf.amcs a USING(amc_id) LEFT JOIN mf.fund_facts ff USING(amfi_scheme_code)
        ORDER BY v.group_key, f.in_scope DESC, (f.plan_type='REGULAR') DESC,
                 (f.option_type='GROWTH') DESC, (ff.amfi_scheme_code IS NOT NULL) DESC,
                 f.amfi_scheme_code
    """).fetchall()
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO mf.fund_family (tlws_mf_id, group_key, base_scheme_name, slug, tags)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (group_key) DO UPDATE SET
                base_scheme_name = EXCLUDED.base_scheme_name, tags = EXCLUDED.tags, updated_at = now()
            WHERE (mf.fund_family.base_scheme_name, mf.fund_family.tags)
                IS DISTINCT FROM (EXCLUDED.base_scheme_name, EXCLUDED.tags)
        """, [
            (str(uuid4()), row["group_key"], row["base_scheme_name"], slugify(row["base_scheme_name"]),
             tags_for(row["amfi_amc_name"], row["asset_class"], row["sub_asset_class"], row["risk_level"]))
            for row in representatives
        ])
    conn.execute("""
        INSERT INTO mf_identity.variant_families (amfi_scheme_code, family_id)
        SELECT v.amfi_scheme_code, f.tlws_mf_id
        FROM mf.fund_variants v JOIN mf.fund_family f USING(group_key)
        ON CONFLICT (amfi_scheme_code) DO NOTHING
    """)
    bad = conn.execute("""
        SELECT EXISTS (
            SELECT 1 FROM mf.fund_variants v
            LEFT JOIN mf_identity.variant_families b USING(amfi_scheme_code)
            LEFT JOIN mf.fund_family f ON f.group_key = v.group_key
            WHERE b.family_id IS NULL OR b.family_id IS DISTINCT FROM f.tlws_mf_id
        ) OR EXISTS (
            SELECT 1 FROM mf.fund_family f
            WHERE NOT EXISTS (SELECT 1 FROM mf.fund_variants v WHERE v.group_key = f.group_key)
        ) AS failed
    """).fetchone()
    if bad["failed"]:
        raise RuntimeError("fund family identity integrity check failed")
