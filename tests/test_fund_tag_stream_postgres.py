"""PostgreSQL rollback proof for late streamed tag readback failures."""

import json
import logging

import pytest

from mfdataindia.tagging import policy, store
from test_fund_tagging_postgres import (
    append_source_receipt,
    tagging_db as tagging_db,
    tagging_rows,
    writer,
)
from test_ro01_reader_policy_postgres import connect, ro01_db as ro01_db

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
@pytest.mark.parametrize(
    ("table", "reason"),
    [
        ("current", "TAGGING_PUBLICATION_INCOMPLETE"),
        ("assessments", "TAGGING_HISTORY_DIFFERS"),
    ],
)
def test_late_stream_mismatch_rolls_back_every_tag_table(tagging_db, monkeypatch, caplog, table, reason):
    with writer(tagging_db) as conn:
        store.publish(conn)
    with connect(tagging_db, autocommit=False) as owner:
        before = tagging_rows(owner)
        store.limits(owner)
        assert owner.execute("SHOW transaction_timeout").fetchone()["transaction_timeout"] == "15s"
        assert owner.execute("SHOW statement_timeout").fetchone()["statement_timeout"] == "5s"
        assert owner.execute("SHOW lock_timeout").fetchone()["lock_timeout"] == "5s"
        owner.execute("""
            INSERT INTO mf.funds (
                amfi_scheme_code,amc_id,scheme_name,scheme_name_norm,scheme_type,scheme_category,
                plan_type,plan_source,option_type,last_seen_in_source
            )
            SELECT n,1,'Streaming fixture ' || n,'STREAMING FIXTURE ' || n,
                   'OPEN_ENDED','Equity Scheme - Large Cap Fund','REGULAR','COLUMN','GROWTH',DATE '2026-11-01'
            FROM generate_series(100,130) AS n
        """)
        receipt = append_source_receipt(owner, source_kind="fund-family-rebuild")
    insert = store._insert
    verify = store.verify_candidate
    seen = []

    def corrupt_late(conn, relation, rows):
        if relation == table:
            assert len(rows) > 512
            rows = list(rows)
            index = max(range(len(rows)), key=lambda i: (rows[i]["code"], rows[i]["dimension"]))
            row = rows[index]
            rows[index] = {**row, "document": {**row["document"], "reason": "SYNTHETIC_MISMATCH"}}
        return insert(conn, relation, rows)

    def verify_limits(conn, candidate):
        row = conn.execute("""
            SELECT current_setting('transaction_timeout') AS transaction,
                   current_setting('statement_timeout') AS statement,
                   current_setting('lock_timeout') AS lock
        """).fetchone()
        assert row == {"transaction": "2min", "statement": "30s", "lock": "5s"}
        seen.append(candidate["snapshot_id"])
        verify(conn, candidate)

    caplog.set_level(logging.INFO, logger="mfdataindia.tagging.phases")
    caplog.clear()
    with monkeypatch.context() as patch, writer(tagging_db) as conn:
        patch.setattr(store, "_insert", corrupt_late)
        patch.setattr(store, "verify_candidate", verify_limits)
        with pytest.raises(policy.TaggingError, match=reason):
            store.publish(conn)
        assert len(seen) == 1
        assert tagging_rows(conn) == before
    records = [json.loads(record.message) for record in caplog.records
               if record.name == "mfdataindia.tagging.phases"]
    assert [event for event in records if event["format"] == "mfdata-tag-publication-limits-v1"] == [{
        "format": "mfdata-tag-publication-limits-v1",
        "statement_timeout": "30s", "transaction_timeout": "2min", "lock_timeout": "5s",
    }]
    phases = [event for event in records if event["format"] == "mfdata-tag-publication-phase-v1"]
    assert [(event["phase"], event["state"]) for event in phases[-2:]] == [
        ("readback", "FAILED"), ("transaction", "FAILED"),
    ]
    assert all(event["elapsed_seconds"] >= 0 for event in phases if event["state"] != "STARTED")

    with writer(tagging_db) as conn:
        published = store.publish(conn)
        assert published["state"] == "PUBLISHED"
        assert published["source_publication_id"] == receipt["publication_id"]
        assert published["snapshot_id"] == seen[0]
        assert store.publish(conn)["state"] == "UNCHANGED"
