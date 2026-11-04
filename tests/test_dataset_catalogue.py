from copy import deepcopy

import pytest

from mfdataindia.datasets import catalogue as c, enrollment, policy as p, reads


def row(**changes):
    return {"oid": 42, "schema": "mf_new_research", "relation": "observations",
            "kind": "r", "owner": "mfdata_admin", "row_security": False,
            "schema_owner": "mfdata_admin",
            "enrollment": enrollment.enrollment_comment(classification="fund_research", title="Synthetic observations"),
            "schema_usage": True, "schema_create": False, "can_select": True,
            "grant_option": False, "mutation": False, "public_read": False,
            "columns": [{"name":"id","type":"int4","type_schema":"pg_catalog","nullable":False,"position":1}],
            "primary_key": ["id"], **changes}


@pytest.mark.parametrize('change,reason', [
    ({'owner':'untrusted_creator'}, 'UNSUPPORTED_DATASET_CREATOR'),
    ({'schema_owner':'untrusted_creator'}, 'UNSUPPORTED_DATASET_SCHEMA_OWNER'),
    ({'schema':'mf','owner':'mfdata_ingest'}, 'DATASET_CREATOR_SCHEMA_POLICY_CONFLICT'),
    ({'schema_usage':False}, 'DATASET_READ_GRANTS_MISSING'),
    ({'can_select':False}, 'DATASET_READ_GRANTS_MISSING'),
    ({'schema_create':True}, 'UNSAFE_DATASET_CAPABILITY'),
    ({'grant_option':True}, 'UNSAFE_DATASET_CAPABILITY'),
    ({'mutation':True}, 'UNSAFE_DATASET_CAPABILITY'),
    ({'public_read':True}, 'PUBLIC_DATASET_READ_NOT_ALLOWED'),
    ({'row_security':True}, 'UNSUPPORTED_DATASET_RELATION'),
    ({'kind':'f'}, 'UNSUPPORTED_DATASET_RELATION'),
    ({'unsafe_dependencies':True}, 'UNREVIEWED_DATASET_DEPENDENCY'),
    ({'schema':'mf','relation':'schema_migrations'}, 'EXCLUDED_INTERNAL_CONTROL'),
])
def test_capability_and_identity_decisions_are_independent_of_select(change, reason):
    with pytest.raises(p.DatasetError, match=reason):
        c.checked(row(**change))


@pytest.mark.parametrize('column', [
    {'name':'id','type':'int4','type_schema':'untrusted','nullable':False,'position':1},
    {'name':'id','type':'arbitrary','type_schema':'pg_catalog','nullable':False,'position':1},
    {'name':'password','type':'text','type_schema':'pg_catalog','nullable':False,'position':1},
])
def test_unknown_output_routines_and_sensitive_columns_are_not_executed(column):
    with pytest.raises(p.DatasetError):
        c.checked(row(columns=[column],primary_key=[column['name']]))


@pytest.mark.parametrize('value', [
    {'password':'synthetic-secret-marker'}, {'nested':[{'access_token':'synthetic-secret-marker'}]},
    {'note':'postgresql://user:synthetic-secret-marker@host/db'},
    {'note':'password=synthetic-secret-marker'},
])
def test_credential_content_fails_with_value_free_reason(value):
    with pytest.raises(p.DatasetError) as stopped:
        reads._safe_value(value)
    assert str(stopped.value) == 'SENSITIVE_DATASET_VALUE'
    assert 'synthetic-secret-marker' not in str(stopped.value)


def test_select_is_not_registration_or_ready_and_internal_controls_cannot_be_reclassified():
    value = row()
    assert c.checked(value)[0]['id'] == p.dataset_id(value['schema'],value['relation'])
    value['enrollment'] = None
    with pytest.raises(p.DatasetError,match='INVALID_DATASET_ENROLLMENT'):
        c.checked(value)
    # A trusted label alone cannot bypass the explicit current control boundary.
    for schema, relation in c.INTERNAL_CONTROL_RELATIONS:
        with pytest.raises(p.DatasetError,match='EXCLUDED_INTERNAL_CONTROL'):
            c.checked(row(schema=schema,relation=relation))


@pytest.mark.parametrize('extra', ['sql','reader','grant_option','connection','url'])
def test_enrollment_metadata_has_no_untrusted_target_or_command_fields(extra):
    raw = enrollment.enrollment_comment(classification='fund_research',title='Synthetic')
    import json
    data = json.loads(raw[len(c.PREFIX):])
    data[extra] = 'synthetic-secret-marker'
    with pytest.raises(p.DatasetError,match='INVALID_DATASET_ENROLLMENT') as stopped:
        c.decode_enrollment(c.PREFIX + json.dumps(data))
    assert 'synthetic-secret-marker' not in str(stopped.value)


def test_array_is_ordinary_schema_metadata_not_executable_custom_type():
    value = deepcopy(row())
    value['columns'].append({'name':'tags','type':'_text','type_schema':'pg_catalog','nullable':True,'position':2})
    assert c.checked(value)[0]['columns'][1]['type'] == '_text'


def test_binary_without_a_stable_selector_is_unavailable_before_discovery():
    columns = [{'name':'pdf','type':'bytea','type_schema':'pg_catalog','nullable':True,'position':1}]
    with pytest.raises(p.DatasetError,match='BINARY_DATASET_KEY_REQUIRED'):
        c.checked(row(kind='v',columns=columns,primary_key=[]))
