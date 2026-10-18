"""Real geometry and DB guards in disposable PostgreSQL, never live evidence."""
from copy import deepcopy

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from mfdataindia.enrichment import book_store, uti_header_evidence
from mfdataindia.review.policy import fingerprint
from test_uti_v3_geometry import header
from test_uti_v2_store import prepare_v4, inputs_v2
import test_uti_store as old
from test_verified_review_store import verified_db as verified_db


@pytest.mark.postgres
def test_frozen_canonicalizer_rejects_real_floats_and_exact_string_seals_agree(verified_db):
    with psycopg.connect(verified_db) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="noninteger JSON numeric artifact"):
            with conn.transaction():
                conn.execute("SELECT mf_review.canonical_json(%s)", (Jsonb(header()),))
        encoded = uti_header_evidence.encode(header())
        result = conn.execute("SELECT encode(sha256(convert_to(mf_review.canonical_json(%s),'UTF8')),'hex')", (Jsonb(encoded),)).fetchone()[0]
        assert result == fingerprint(encoded)


@pytest.mark.postgres
def test_canonical_title_guard_rejects_bad_artifacts_without_committed_rows(verified_db, monkeypatch):
    identities, calls, *_ = prepare_v4(verified_db, monkeypatch, canonical=True)
    valid = inputs_v2(identities, calls, canonical=True)
    mutations = [
        ("x", 15), ("x", "15,624"), ("x", "1e1"), ("x", "NaN"),
        ("x", "-0"), ("x", "+15"), ("x", "015"), ("x", True),
        ("x", "60"), ("x", "0.000000000000000001"),
        ("page_height", "0"), ("page_width", "2001"),
        ("y", "700"), ("font_points", "31"),
        ("text_hash", "f" * 64), ("source_hash", "f" * 64), ("extra", "forbidden"),
    ]
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        before = book_store.capture(conn)
    for i, (field, value) in enumerate(mutations):
        inputs = deepcopy(valid)
        batch = inputs[0][0]
        item = next(o for o in batch["observations"] if o["identity"]["code"] == 102398)
        item["result"]["detail_header_evidence"][field] = value
        batch["batch_id"] = fingerprint({k: v for k, v in batch.items() if k != "batch_id"})
        with pytest.raises((psycopg.Error, ValueError)):
            old.publish(verified_db, identities, inputs, execution="v3-reject-" + str(i))
        with psycopg.connect(verified_db, row_factory=dict_row) as conn:
            assert book_store.capture(conn) == before
    run = old.publish(verified_db, identities, valid, execution="v3-good-after-rejections")
    assert run["status"] in ("published", "updated", "unchanged")
