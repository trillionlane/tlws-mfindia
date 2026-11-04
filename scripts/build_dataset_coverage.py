#!/usr/bin/env python3
"""Account for a retained relation catalogue without connecting or reading data.

Static domain references are leads, not claims that every field is exposed or
consumed. Only later authenticated runtime/consumer receipts can establish that.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from mfdataindia.datasets import catalogue, policy  # noqa: E402

HELD_FOR_REVIEW = catalogue.HELD_FOR_REVIEW
SOURCE_RELATIONS = {'blobs','sources','catalogs'}
# Explicit baseline semantic keys; future view declarations are enrollment
# metadata, not source edits. The baseline must match the retained definition.
VIEW_KEYS = {
    ('mf','nav_last_5y'): ['amfi_scheme_code','nav_date'],
    ('mf','current_fund_facts'): ['amfi_scheme_code'],
    ('mf','current_risk_profile'): ['amfi_scheme_code'],
    ('mf','v_fund_data_status'): ['amfi_scheme_code'],
    ('mf','v_nav_coverage'): ['amfi_scheme_code'],
    ('mf_live_facts','baseline'): ['amfi_scheme_code'],
    ('mf_live_facts','resolved'): ['amfi_scheme_code'],
    ('mf_live_facts','current_values'): ['code'],
}


def references():
    result = {}
    for path in sorted((ROOT / 'src/mfdataindia').rglob('*.py')):
        if '/datasets/' in str(path):
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for schema, relation in re.findall(r'\b(mf(?:_[a-z][a-z0-9_]*)?)\.([a-z][a-z0-9_]*)\b', node.value):
                item = {'path': str(path.relative_to(ROOT)), 'line': node.lineno,
                        'kind': 'static_reference_not_complete_route_or_field_proof'}
                key = schema, relation
                if item not in result.setdefault(key, []):
                    result[key].append(item)
    return result


def build(value, source_hash):
    state = value.get('plan',{}).get('before', value)
    rows = [r for r in state['relations'] if r['relkind'] in ('r','p','v','m')]
    columns = state['columns']
    found = references()
    entries = []
    seen = set()
    for row in sorted(rows, key=lambda r:(r['nspname'],r['relname'])):
        key = row['nspname'], row['relname']
        if key in seen:
            raise ValueError('DUPLICATE_RELATION_INVENTORY')
        seen.add(key)
        control = key in catalogue.INTERNAL_CONTROL_RELATIONS
        held = key in HELD_FOR_REVIEW
        classification = 'internal_control' if control else 'source_evidence' if key[1] in SOURCE_RELATIONS else 'fund_research'
        exposed = not control and not held
        dataset = policy.dataset_id(*key)
        fields = [{'name': c['name'], 'database_type_oid': c['type_oid'],
                   'nullable': not c['not_null'],
                   'generic_dataset_expectation': 'all_fields_subject_to_verified_safe_schema' if exposed else 'not_exposed'}
                  for c in columns if c['oid'] == row['oid'] and not c['dropped']]
        entries.append({'relation': '.'.join(key), 'kind': row['relkind'],
            'dataset_id': dataset if exposed else None, 'classification': classification,
            'disposition': 'excluded_internal_control' if control else 'architectural_review_required' if held else 'eligible_implementation_pending',
            'reason': 'Internal execution/approval/identity/configuration/control evidence, not a consumer dataset.' if control else HELD_FOR_REVIEW[key] if held else None,
            'fields': fields, 'required_SELECT_observed': bool(row['capabilities'][0]),
            'SELECT_is_not_API_or_TI_acceptance': True,
            'existing_source_references': found.get(key,[]),
            'existing_domain_route_and_field_coverage': 'not_yet_reconciled_with_TI',
            'dataset_contract': policy.VERSION if exposed else None,
            'proposed_dataset_routes': [catalogue.paths(dataset)['metadata_url'], catalogue.paths(dataset)['rows_url']] if exposed else [],
            'TI_consumer': 'Authenticated reusable dataset view; confirm final TI route' if exposed else None,
            'pagination': 'Physical-PK keyset, otherwise explicit canonical offset; no cross-page snapshot claim' if exposed else None,
            'proposed_view_key_columns': VIEW_KEYS.get(key,[]) if exposed else [],
            'view_definition_sha256': row['definition']['sha256'] if row['relkind']=='v' else None,
            'row_availability': 'not_observed_through_new_API',
            'actual_TI_consumption': 'not_observed' if exposed else 'not_applicable_excluded_or_pending_decision'})
    return {'format':'mfdata-dataset-coverage-inventory-v1',
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'baseline_source':'Retained catalogue metadata, not a fresh live data observation',
        'input_sha256':source_hash, 'enrolled_relation_count':len(rows),
        'accounted_relation_count':len(entries),
        'excluded_internal_control_count':sum(e['disposition']=='excluded_internal_control' for e in entries),
        'architectural_review_required_count':sum(e['disposition']=='architectural_review_required' for e in entries),
        'eligible_pending_count':sum(e['disposition']=='eligible_implementation_pending' for e in entries),
        'actual_consumer_ready_count':0, 'complete_integration_claim':False,
        'relations':entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('catalogue',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    raw = args.catalogue.read_bytes()
    document = build(json.loads(raw),hashlib.sha256(raw).hexdigest())
    with args.output.open('x') as stream:
        json.dump(document,stream,indent=2,sort_keys=True)
        stream.write('\n')
    print(json.dumps({k:v for k,v in document.items() if k!='relations'}))


if __name__ == '__main__':
    main()
