"""Staged future source authority must not change already-active current facts."""

from copy import deepcopy

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from mfdataindia.enrichment import (
    book_facts,
    book_projection,
    book_runtime,
    book_sources,
    book_store,
    current_facts_store as current,
    multi_facts,
)
from mfdataindia.enrichment.store import identity
from mfdataindia.review.policy import fingerprint
from test_amc_book_facts import KEY, ROOT, prepared, publish
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES
from test_verified_review_store import verified_db as verified_db


def test_book_policy_parent_is_the_retained_registered_v3_graph():
    assert book_sources.current_policy()["previous_policy_hash"] == fingerprint(
        multi_facts.current_policy()
    )


@pytest.mark.postgres
def test_retained_cohort_never_falls_into_book_projection_after_identity_drift(
    verified_db, monkeypatch
):
    identities, inputs, _ = prepared(verified_db, monkeypatch)
    publish(verified_db, identities, inputs)
    reference = [
        {
            "code": 100001,
            "batch_id": inputs[0][0]["batch_id"],
            "as_of": "2026-09-30",
            "values": {"aum": "12345.67"},
        }
    ]
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        book_projection.activate(
            conn,
            ROOT,
            reference,
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
        conn.execute(
            "UPDATE mf.funds SET scheme_name=scheme_name||' RENAMED SYNTHETIC FIXTURE' WHERE amfi_scheme_code=103174"
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        changed = book_store.population(conn, KEY)
        changed_identity = identity(conn, 103174)
    batches, originals, archives = deepcopy(inputs)
    for batch in batches:
        next(r for r in batch["observations"] if r["identity"]["code"] == 103174)["identity"] = (
            changed_identity
        )
        batch["population_hash"] = fingerprint(changed)
        batch["batch_id"] = fingerprint({k: v for k, v in batch.items() if k != "batch_id"})
    publish(verified_db, changed, (batches, originals, archives), "protected-identity-drift")
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        assert not conn.execute(
            "SELECT 1 FROM mf_live_facts.field_versions WHERE code=103174"
        ).fetchone()
        assert current.get(conn, 103174)["mapping_state"] == "identity_changed"


@pytest.mark.postgres
def test_staged_future_source_and_registry_replay_cannot_leak_into_active_policy(
    verified_db, monkeypatch
):
    identities, inputs, _ = prepared(verified_db, monkeypatch)
    first = publish(verified_db, identities, inputs)
    reference = [
        {
            "code": 100001,
            "batch_id": inputs[0][0]["batch_id"],
            "as_of": "2026-09-30",
            "values": {"aum": "12345.67"},
        }
    ]
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        activation = book_projection.activate(
            conn,
            ROOT,
            reference,
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
    original = book_sources.configuration()
    staged = deepcopy(original)
    key = "future-source-fixture"
    specification = deepcopy(staged["source_specifications"][KEY])
    specification.update(key=key, archive_prefix="official-monthly-facts/" + key + "/")
    staged["source_specifications"][key] = specification
    staged["registry"]["sources"].append({**staged["registry"]["sources"][0], "key": key})
    staged["registry"]["version"] = 2
    staged["registry"]["maximum_sources_per_execution"] = 4
    staged["registry_hash"] = fingerprint(staged["registry"])
    with psycopg.connect(verified_db) as conn:
        conn.execute(
            "INSERT INTO mf_book_facts.configuration VALUES(2,%s,%s)",
            (fingerprint(staged), Jsonb(staged)),
        )
    # Model the next approved source's successful staging under the restricted
    # writer. The active current policy still references registry version 1.
    batches, originals, archives = deepcopy(inputs)
    for batch in batches:
        batch.update(source_key=key, specification_hash=fingerprint(specification))
        row = next(r for r in batch["observations"] if r["identity"]["code"] == 100001)
        row["result"]["fields"]["aum"].update(value="54321.00", raw_value="54321.00 Cr")
        batch["batch_id"] = fingerprint({k: v for k, v in batch.items() if k != "batch_id"})
    for archive in archives:
        archive["uri"] = archive["uri"].replace("/" + KEY + "/", "/" + key + "/")
    body = {k: v for k, v in first.items() if k != "run_id"}
    body.update(
        source_key=key,
        execution_id=multi_facts.JOB + "-staged-future",
        status="updated",
        specification_hash=fingerprint(specification),
        batch_ids=[b["batch_id"] for b in batches],
        archives=archives,
        coverage=[book_facts.coverage(b) for b in batches],
    )
    with psycopg.connect(runtime_dsn(verified_db, ROLES[0]), row_factory=dict_row) as conn:
        for original_file in originals:
            book_runtime.retain_source(conn, key, original_file)
        for batch in batches:
            book_store.append_exact(
                conn,
                "batches",
                "batch_id",
                batch,
                {
                    k: batch[k]
                    for k in (
                        "source_key",
                        "content_hash",
                        "identity_content_hash",
                        "disclosure_as_of",
                    )
                },
            )
        book_store.append_run(conn, body)
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        field = current.get(conn, 100001)["fields"]["aum"]
        assert field["state"] == "available" and field["value"] == "12345.67"
        assert field["disclosure_as_of"] == "2026-09-30"
        assert field["source_count"] == 1
        assert field["sources"][0]["batch_id"] == inputs[0][0]["batch_id"]
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM mf_book_facts.runs WHERE source_key=%s", (key,)
            ).fetchone()["n"]
            == 1
        )
        book_projection.recover(conn, ROOT, activation["transition_id"])
    # Restore the previous registry as a new immutable owner generation, then
    # reactivate. Replayed matching registry hashes must not duplicate sources.
    with psycopg.connect(verified_db) as conn:
        conn.execute(
            "INSERT INTO mf_book_facts.configuration VALUES(3,%s,%s)",
            (fingerprint(original), Jsonb(original)),
        )
    with psycopg.connect(verified_db, row_factory=dict_row) as conn:
        book_projection.activate(
            conn,
            ROOT,
            reference,
            expected_configuration_hash=fingerprint(dict(current.configuration(conn))),
        )
        field = current.get(conn, 100001)["fields"]["aum"]
        assert field["state"] == "available" and field["value"] == "12345.67"
        assert field["disclosure_as_of"] == "2026-09-30" and field["source_count"] == 1
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM mf_book_facts.batches WHERE source_key=%s", (key,)
            ).fetchone()["n"]
            == 2
        )
