"""Whole-cohort financial preservation with exact append-only source assertions.

This is an evidence-format revision, not a new projection or a data writer.
Financial row hashes are computed inside PostgreSQL without float round-trips.
"""

from collections import Counter, defaultdict
import re

from psycopg import sql

from mfdataindia.enrichment import book_sources, current_facts_store as current
from mfdataindia.enrichment.current_graph_read import graph
from mfdataindia.review.policy import fingerprint

FORMAT = "official-prior-amc-projection-v2"
SECTIONS = ("mf_live_facts.field_versions", "mf_live_facts.selected_fields",
            "mf_live_facts.current_values", "mf.current_fund_facts")


def _codes(conn, names, protected=()):
    codes = [row["code"] for row in conn.execute(
        "SELECT f.amfi_scheme_code AS code FROM mf.funds f JOIN mf.amcs a USING(amc_id) "
        "WHERE a.amfi_amc_name=ANY(%s) OR f.amfi_scheme_code=ANY(%s) ORDER BY f.amfi_scheme_code",
        (list(names), list(protected)),
    ).fetchall()]
    if not 1 <= len(codes) <= 4096:
        raise ValueError("complete bounded preservation population required; no truncation")
    return codes


def _rows(conn, codes, *, selected_only=False):
    from mfdataindia.enrichment.book_preservation import sealed_core_read_relation

    sealed_core_read_relation(conn)
    sections = [("mf_live_facts.selected_fields", "selected_fields", "code", True)] if selected_only else [
        (SECTIONS[0], "field_versions", "code", True),
        (SECTIONS[1], "selected_fields", "code", True),
        (SECTIONS[2], "current_values", "code", False),
        (SECTIONS[3], "resolved", "amfi_scheme_code", False),
    ]
    queries = []
    for section, local, code, sources in sections:
        payload = sql.SQL("to_jsonb(t)-'sources'") if sources else sql.SQL("to_jsonb(t)")
        members = sql.SQL("""COALESCE((SELECT jsonb_agg(h ORDER BY h) FROM (
            SELECT encode(sha256(convert_to(value::text,'UTF8')),'hex') AS h
            FROM jsonb_array_elements(t.sources)) original_assertions),'[]'::jsonb)""") if sources else sql.SQL("'[]'::jsonb")
        condition = sql.SQL(" WHERE amfi_scheme_code=ANY(%s)") if local == "resolved" else sql.SQL("")
        queries.append(sql.SQL(
            "SELECT {} AS section,t.{} AS code,{} AS field,"
            "encode(sha256(convert_to(({})::text,'UTF8')),'hex') AS payload_hash,"
            "{} AS source_hashes FROM {} t{}"
        ).format(sql.Literal(section), sql.Identifier(code),
                 sql.SQL("t.field") if sources else sql.SQL("NULL::text"),
                 payload, members, sql.Identifier(local), condition))
    query = graph(conn, codes, complete=not selected_only) + sql.SQL(" UNION ALL ").join(queries)
    query += sql.SQL(" ORDER BY section,code,field,payload_hash")
    return conn.execute(query, (codes,) if selected_only else (codes, codes)).fetchall()


def _selected_value_rows(conn, codes):
    """Hash the complete selected value row, without building unused sources.

    The target-cell certificate never consumed source_hashes. Its hash is still
    exactly to_jsonb(selected_row)-'sources', built from the same six columns of
    the installed, ACL-checked view. Naming columns lets PostgreSQL discard the
    unused provenance aggregates; selection itself stays in the sealed view.
    Full four-relation/source-multiset evidence continues to use _rows unchanged.
    """
    from mfdataindia.enrichment.book_preservation import sealed_core_read_relation

    if (not isinstance(codes, list) or not 1 <= len(codes) <= 4096
            or any(type(code) is not int or not 0 < code <= 2147483647 for code in codes)
            or codes != sorted(set(codes))):
        raise ValueError("complete ordered target population required; no truncation")
    sealed_core_read_relation(conn)
    names = ["field_versions", "field_heads", "selected_fields"]
    contexts = conn.execute(
        "SELECT relname,relowner,reloptions FROM pg_class "
        "WHERE relnamespace='mf_live_facts'::regnamespace AND relkind='v' AND relname=ANY(%s)",
        (names,),
    ).fetchall()
    if ({row["relname"] for row in contexts} != set(names)
            or len({row["relowner"] for row in contexts}) != 1
            or any(row["reloptions"] for row in contexts)):
        raise ValueError("sealed selected-value execution contexts differ")
    columns = conn.execute(
        "SELECT attname FROM pg_attribute WHERE attrelid='mf_live_facts.selected_fields'::regclass "
        "AND attnum>0 AND NOT attisdropped ORDER BY attnum"
    ).fetchall()
    if [row["attname"] for row in columns] != [
        "code", "field", "as_of", "value", "distinct_values", "usable", "sources"
    ]:
        raise ValueError("complete sealed selected-value column signature required")
    return conn.execute("""SELECT t.code,t.field,
        encode(sha256(convert_to(jsonb_build_object(
            'code',t.code,'field',t.field,'as_of',t.as_of,'value',t.value,
            'distinct_values',t.distinct_values,'usable',t.usable)::text,'UTF8')),'hex') AS payload_hash
        FROM mf_live_facts.selected_fields t WHERE t.code=ANY(%s)
        ORDER BY t.code,t.field,payload_hash""", (codes,)).fetchall()


def capture_target(conn, key, *, codes=None):
    """Current supported cells and source-certified TER under the ACTIVE policy."""
    prior = book_sources.registry(book_sources.CURRENT_VERSION - 1)
    source = next((s for s in prior["sources"] if s["key"] == key), None)
    if source is None:
        raise ValueError("revised source must be an existing exact AMC")
    codes = codes or _codes(conn, [source["amc"]])
    selected = _selected_value_rows(conn, codes)
    cells = {str(row["code"]) + "|" + row["field"]: row["payload_hash"] for row in selected}
    if len(cells) != len(selected):
        raise ValueError("unique selected target field required")
    policy = current.policy_document(conn)
    active = conn.execute(
        "SELECT document FROM mf_book_facts.configuration WHERE configuration_hash=%s ORDER BY generation DESC LIMIT 1",
        (policy.get("book_source_configuration_hash"),),
    ).fetchone()
    if not active or active["document"] != book_sources.configuration(active["document"]["registry"]["version"]):
        raise ValueError("sealed active source authority required")
    spec_hash = active["document"]["source_specification_hashes"][key]
    # TER is NOT a primary/current BER field. Preserve its overlapping dated
    # values separately; original and first-acquired source rows stay immutable.
    supplied = conn.execute("""WITH observations AS MATERIALIZED (
        SELECT f.amfi_scheme_code AS code,b.disclosure_as_of::text AS period,o AS observation
        FROM mf_book_facts.batches b CROSS JOIN LATERAL jsonb_array_elements(b.document->'observations') o
        JOIN mf.funds f ON f.amfi_scheme_code=(o->'identity'->>'code')::integer
        JOIN mf.amcs a USING(amc_id)
        WHERE b.source_key=%s AND b.document->>'specification_hash'=%s AND f.in_scope
          AND f.amfi_scheme_code=ANY(%s)
          AND o->'identity'=jsonb_build_object('code',f.amfi_scheme_code,'amc',a.amfi_amc_name,
            'scheme_name',f.scheme_name,'plan',f.plan_type,'option',f.option_type,
            'isins',COALESCE((SELECT jsonb_agg(i ORDER BY i) FROM (SELECT DISTINCT btrim(z) AS i
              FROM unnest(ARRAY[f.isin_growth_or_div_payout::text,f.isin_div_reinvest::text]) z WHERE z IS NOT NULL) identifiers),'[]'::jsonb))
          AND EXISTS(SELECT 1 FROM mf_book_facts.runs r WHERE r.status<>'failed' AND r.document->'batch_ids' ? b.batch_id)
    ), supplied AS (
        SELECT code,period,'primary' AS kind,field.key AS field,
            jsonb_build_object('value',CASE WHEN field.key IN('aum','base_expense_ratio')
              THEN to_jsonb(trim_scale((field.value->>'value')::numeric)::text) ELSE field.value->'value' END,
              'unit',field.value->>'unit','scope',field.value->>'scope',
              'field_effective_date',field.value->'field_effective_date','date_basis',field.value->>'date_basis') AS value
        FROM observations CROSS JOIN LATERAL jsonb_each(observation->'result'->'fields') field
        WHERE field.value->>'status'='accepted'
        UNION ALL
        SELECT code,period,'TER','reported_total_expense_ratio',
            jsonb_build_object('value',trim_scale((observation->'result'->'reported_total_expense_ratio'->>'value')::numeric)::text,
              'unit',observation->'result'->'reported_total_expense_ratio'->>'unit',
              'scope',observation->'result'->'reported_total_expense_ratio'->>'scope',
              'expense_basis',observation->'result'->'reported_total_expense_ratio'->>'expense_basis')
        FROM observations WHERE observation->'result'->'reported_total_expense_ratio'->>'status'='accepted'
    ) SELECT code,period,kind,field,count(DISTINCT value) AS distinct_values,
        encode(sha256(convert_to((jsonb_agg(value ORDER BY value)->0)::text,'UTF8')),'hex') AS value_hash
      FROM supplied GROUP BY code,period,kind,field ORDER BY code,period,kind,field""", (key, spec_hash, codes)).fetchall()
    if any(row["distinct_values"] != 1 for row in supplied):
        raise ValueError("conflicting source-period values cannot be a preservation baseline")
    reported = {str(row["code"]) + "|" + row["period"]: row["value_hash"] for row in supplied if row["kind"] == "TER"}
    primary = {str(row["code"]) + "|" + row["period"] + "|" + row["field"]: row["value_hash"] for row in supplied if row["kind"] == "primary"}
    body = {"codes": codes, "selected_cells": cells, "selected_cell_count": len(cells),
            "source_primary_cells": primary, "source_primary_cell_count": len(primary),
            "reported_TER_cells": reported, "reported_TER_cell_count": len(reported)}
    return {**body, "sha256": fingerprint(body)}


def validate_target(doc):
    if (not isinstance(doc, dict)
            or set(doc) != {"codes", "selected_cells", "selected_cell_count", "source_primary_cells", "source_primary_cell_count", "reported_TER_cells", "reported_TER_cell_count", "sha256"}
            or doc["sha256"] != fingerprint({k: v for k, v in doc.items() if k != "sha256"})
            or not isinstance(doc["codes"], list) or not 1 <= len(doc["codes"]) <= 4096
            or any(type(code) is not int or not 0 < code <= 2147483647 for code in doc["codes"])
            or doc["codes"] != sorted(set(doc["codes"]))):
        raise ValueError("sealed whole revised-source preservation required")
    for kind, count in (("selected_cells", "selected_cell_count"), ("source_primary_cells", "source_primary_cell_count"), ("reported_TER_cells", "reported_TER_cell_count")):
        if not isinstance(doc[kind], dict) or type(doc[count]) is not int or doc[count] != len(doc[kind]):
            raise ValueError("complete revised-source cell accounting required")
        for key, value in doc[kind].items():
            code, separator, field = key.partition("|")
            if kind == "source_primary_cells":
                period, delimiter, field = field.partition("|")
                if delimiter != "|" or not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", period):
                    raise ValueError("exact source-primary reporting date required")
            if (separator != "|" or not code.isdecimal() or int(code) not in doc["codes"]
                    or not re.fullmatch("[0-9a-f]{64}", value)
                    or (field not in {"aum", "base_expense_ratio", "benchmark_name", "fund_managers", "risk_level"}
                        if kind != "reported_TER_cells" else not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", field))):
                raise ValueError("bounded exact revised-source cell key/hash required")
    return doc


def compare_target(before, after):
    validate_target(before)
    validate_target(after)
    if before["codes"] != after["codes"]:
        raise ValueError("whole revised AMC population changed")
    for kind in ("selected_cells", "source_primary_cells", "reported_TER_cells"):
        if any(after[kind].get(key) != value for key, value in before[kind].items()):
            raise ValueError("previously supported revised-AMC value/date changed or disappeared")
    return {"supported_primary_cells_preserved": len(before["selected_cells"]),
            "new_primary_cells": len(after["selected_cells"]) - len(before["selected_cells"]),
            "supported_primary_period_cells_preserved": len(before["source_primary_cells"]),
            "new_primary_period_cells": len(after["source_primary_cells"]) - len(before["source_primary_cells"]),
            "supported_reported_TER_cells_preserved": len(before["reported_TER_cells"]),
            "new_reported_TER_cells": len(after["reported_TER_cells"]) - len(before["reported_TER_cells"])}


def revised_keys(target_version):
    descriptor = book_sources.registry(target_version).get("authority_transition", {})
    return [descriptor["source_key"]] if descriptor.get("mode") == "source_method_revision" else []


def capture(conn, previous_version):
    target = previous_version + 1
    if target != book_sources.CURRENT_VERSION or target < 5:
        raise ValueError("exact current preservation transition required")
    prior = book_sources.registry(previous_version)
    revised = revised_keys(target)
    untouched = [s for s in prior["sources"] if s["key"] not in revised]
    protected = sorted({code for s in prior["sources"] for code in s["preserved_projection_codes"]})
    untouched_codes = _codes(conn, [s["amc"] for s in untouched], protected)
    all_codes = _codes(conn, [s["amc"] for s in prior["sources"]], protected)
    rows = _rows(conn, untouched_codes)
    members = {section: defaultdict(list) for section in SECTIONS[:2]}
    hashes, payload_hashes = {}, {}
    for section in SECTIONS:
        found = [row for row in rows if row["section"] == section]
        payload_hashes[section] = [row["payload_hash"] for row in found]
        hashes[section] = {"rows": len(found), "sha256": fingerprint([{"sha256": r["payload_hash"]} for r in found])}
        if section in members:
            for row in found:
                members[section][row["payload_hash"]].extend(row["source_hashes"])
    body = {"format": FORMAT, "previous_source_version": previous_version,
            "target_source_version": target, "codes": all_codes, "untouched_codes": untouched_codes,
            "untouched_source_keys": sorted(s["key"] for s in untouched), "revised_source_keys": sorted(revised),
            "hashes": hashes, "payload_hashes": payload_hashes,
            "source_members": {section: {key: sorted(value) for key, value in sorted(found.items())} for section, found in members.items()},
            "revised": {key: capture_target(conn, key) for key in revised}, "source_rows_changed": 0}
    return {**body, "sha256": fingerprint(body)}


def validate(doc):
    required = {"format", "previous_source_version", "target_source_version", "codes", "untouched_codes",
                "untouched_source_keys", "revised_source_keys", "hashes", "payload_hashes", "source_members", "revised", "source_rows_changed", "sha256"}
    if (not isinstance(doc, dict) or set(doc) != required or doc["format"] != FORMAT
            or type(doc["source_rows_changed"]) is not int or doc["source_rows_changed"] != 0
            or doc["sha256"] != fingerprint({k: v for k, v in doc.items() if k != "sha256"})
            or set(doc["hashes"]) != set(SECTIONS) or set(doc["payload_hashes"]) != set(SECTIONS)
            or set(doc["source_members"]) != set(SECTIONS[:2])
            or type(doc["previous_source_version"]) is not int or type(doc["target_source_version"]) is not int
            or doc["target_source_version"] != doc["previous_source_version"] + 1
            or doc["target_source_version"] < 5):
        raise ValueError("sealed full preservation-v2 evidence required")
    for key in ("codes", "untouched_codes"):
        values = doc[key]
        if (not isinstance(values, list) or not 1 <= len(values) <= 4096
                or any(type(v) is not int or not 0 < v <= 2147483647 for v in values)
                or values != sorted(set(values))):
            raise ValueError("whole ordered preservation population required")
    if not set(doc["untouched_codes"]) <= set(doc["codes"]) or set(doc["revised"]) != set(doc["revised_source_keys"]):
        raise ValueError("explicit bounded preservation scope required")
    for section, value in doc["hashes"].items():
        payloads = doc["payload_hashes"][section]
        if (set(value) != {"rows", "sha256"} or type(value["rows"]) is not int or value["rows"] < 0
                or not re.fullmatch("[0-9a-f]{64}", value["sha256"])
                or not isinstance(payloads, list) or len(payloads) != value["rows"]
                or any(not re.fullmatch("[0-9a-f]{64}", h) for h in payloads)
                or value["sha256"] != fingerprint([{"sha256": h} for h in payloads])):
            raise ValueError("exact financial query row/hash evidence required")
        if section in SECTIONS[:2]:
            groups = doc["source_members"][section]
            occurrences = Counter(payloads)
            if set(groups) != set(payloads):
                raise ValueError("every financial row requires full source membership evidence")
            for key, members in groups.items():
                if (not isinstance(members, list) or len(members) < occurrences[key]
                        or any(not re.fullmatch("[0-9a-f]{64}", h) for h in members)):
                    raise ValueError("complete original source assertion multiset required")
    for target in doc["revised"].values():
        validate_target(target)
    return doc


def compare(before, after):
    validate(before)
    validate(after)
    scope = ("previous_source_version", "target_source_version", "codes", "untouched_codes", "untouched_source_keys", "revised_source_keys")
    if any(before[key] != after[key] for key in scope) or before["hashes"] != after["hashes"]:
        raise ValueError("whole untouched AMC financial query payloads or population changed")
    appended = 0
    for section in SECTIONS[:2]:
        if set(before["source_members"][section]) != set(after["source_members"][section]):
            raise ValueError("untouched financial assertion group changed")
        for key, old in before["source_members"][section].items():
            new = after["source_members"][section][key]
            if Counter(old) - Counter(new):
                raise ValueError("old untouched source assertion removed or replaced")
            appended += len(new) - len(old)
    target = {key: compare_target(before["revised"][key], after["revised"][key]) for key in before["revised"]}
    return {"whole_prior_codes_checked": len(before["codes"]), "untouched_codes_checked": len(before["untouched_codes"]),
            "all_four_financial_query_payloads_unchanged": True, "old_source_assertions_preserved": True,
            "additional_provenance_members_across_read_results": appended, "revised_source_preservation": target,
            "source_rows_changed": 0}
