import importlib.util
import json
from pathlib import Path

from mfdataindia.datasets.app import create_app
from mfdataindia.datasets.schemas import RowPage
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_additive_dataset_contract_is_current_and_get_only():
    path = ROOT / 'scripts/export_dataset_openapi.py'
    spec = importlib.util.spec_from_file_location('dataset_export',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = module.render()
    assert (ROOT / 'contracts/mfdataindia-consumer-datasets-v1.json').read_text() == raw
    contract = json.loads(raw)
    assert len(contract['paths']) == 4
    assert all(set(methods) == {'get'} for methods in contract['paths'].values())
    assert contract['x-read-only']
    assert 'Cloud Run' in contract['x-authentication-boundary']


def test_factory_keeps_original_domain_inventory_and_owns_its_current_pool():
    from mfdataindia.api.app import create_app as original
    dsn = 'postgresql://fixture:fixture@127.0.0.1:1/fixture'
    before = original(dsn).openapi()
    after = create_app(dsn).openapi()
    assert {k:v for k,v in after['paths'].items() if not k.startswith('/api/datasets/v1')} == before['paths']
    assert all(after['components']['schemas'][k] == v for k,v in before['components']['schemas'].items())


def test_versioned_wire_has_no_hidden_write_or_snapshot_acceptance_claim():
    schema = RowPage.model_json_schema()
    assert schema['properties']['ordering']['enum'] == ['keyset','canonical_offset']
    assert schema['properties']['consistency']['const'] == 'fresh_repeatable_read_each_page'
    assert 'consumer_accepted' not in schema['properties']


def test_unknown_duplicate_and_invalid_query_do_not_echo_values_or_check_out_pool():
    app = create_app('postgresql://fixture:fixture@127.0.0.1:1/fixture')
    # No lifespan/pool initialization: invalid inputs must fail before DB access.
    client = TestClient(app)
    base = '/api/datasets/v1/d_' + '1'*32 + '/rows'
    for suffix in ('?sql=synthetic-secret-marker','?limit=1&limit=2',
                   '?limit=synthetic-secret-marker','?limit=101'):
        reply = client.get(base+suffix)
        assert reply.status_code==422 and reply.json()=={'detail':'INVALID_DATASET_QUERY'}
        assert 'synthetic-secret-marker' not in reply.text
