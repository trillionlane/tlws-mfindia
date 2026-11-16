"""Exercise a late PostgreSQL error in a multi-batch tag publication pipeline."""

import psycopg
import pytest

from mfdataindia.tagging import store
from test_fund_tagging_postgres import (
    append_source_receipt,
    tagging_db as tagging_db,
    tagging_rows,
    writer,
)
from test_ro01_reader_policy_postgres import connect, ro01_db as ro01_db

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("ro01_db", ["core"], indirect=True)
def test_later_pipeline_batch_rolls_back_snapshot_and_projection(tagging_db, monkeypatch):
    with writer(tagging_db) as conn:
        store.publish(conn)
    with connect(tagging_db, autocommit=False) as owner:
        before = tagging_rows(owner)
        owner.execute("UPDATE mf.funds SET last_seen_in_source='2026-11-01'")
        receipt = append_source_receipt(owner)
    insert = store._insert

    def corrupt_later_batch(conn, table, rows):
        if table == "current":
            # First batch succeeds. PostgreSQL rejects a later command at the
            # pipeline sync boundary after the old projection was deleted.
            first = rows + [rows[0]] * (store.INSERT_BATCH_ROWS - len(rows))
            rows = first + [{**rows[0], "document": None}]
        return insert(conn, table, rows)

    with monkeypatch.context() as patch, writer(tagging_db) as conn:
        patch.setattr(store, "_insert", corrupt_later_batch)
        with pytest.raises(psycopg.errors.NotNullViolation) as error:
            store.publish(conn)
        assert error.value.diag.column_name == "document"
        assert tagging_rows(conn) == before

    with writer(tagging_db) as conn:
        second = store.publish(conn)
        assert second["state"] == "PUBLISHED"
        assert second["source_publication_id"] == receipt["publication_id"]
        assert store.publish(conn)["state"] == "UNCHANGED"
