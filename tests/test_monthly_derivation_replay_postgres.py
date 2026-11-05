"""Native dependency-only replay conflict; retained snapshots never rewritten."""
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import subprocess

import psycopg
from psycopg.rows import dict_row
import pytest

from mfdataindia.enrichment import monthly_store, monthly_pdf, monthly_facts
from mfdataindia.enrichment import monthly_derivation_replay as replay
from mfdataindia.review.store import Conflict
from test_monthly_facts import prepare_monthly, verified_db as verified_db
from test_verified_review_api import runtime_dsn
from test_verified_review_store import ROLES, connection

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[1]


def test_native_original_publisher_reproduces_conflict_current_explicit_replay_preserves_every_row(verified_db, monkeypatch):
    selection, raw = prepare_monthly(verified_db, monkeypatch)
    dependency = ['6.20.1']
    original_extract, original_decode = monthly_pdf.extract, monthly_facts.facts.decode
    def extract(*args, **kwargs):
        value = original_extract(*args, **kwargs)
        return {**value, 'extractor_version':dependency[0]}
    def decode(*args, **kwargs):
        text, lineage = original_decode(*args, **kwargs)
        return text, {**lineage, 'extractor_version':dependency[0]}
    monkeypatch.setattr(monthly_pdf, 'extract', extract)
    monkeypatch.setattr(monthly_facts.facts, 'decode', decode)
    dsn = runtime_dsn(verified_db, ROLES[0])
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        retained = monthly_store.publish(conn, selection, raw)
    with connection(verified_db, readonly=True) as conn:
        before = monthly_store.capture(conn)
    dependency[0] = '6.21.4'
    name = 'src/mfdataindia/enrichment/monthly_store.py'
    baseline = module_from_spec(spec_from_loader('amc_original_monthly_store', loader=None))
    baseline.__file__ = str(ROOT/name)
    source = subprocess.check_output(['git','show','6d31b1b82c78f56fa3c36083404e61b7cb8cd38e:'+name],cwd=ROOT)
    exec(compile(source,name,'exec'),baseline.__dict__)
    with pytest.raises(Conflict), psycopg.connect(dsn, row_factory=dict_row) as conn:
        baseline.publish(conn, selection, raw)
    observations = []
    for _ in range(2):
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            result = monthly_store.publish(conn, selection, raw,
                equivalent_replay=lambda writer, candidate: replay.resolve(writer,candidate,observations))
        assert result == retained
    assert len(observations) == 4 and len({r['candidate_snapshot_id'] for r in observations}) == 2
    with connection(verified_db, readonly=True) as conn:
        assert monthly_store.capture(conn) == before
    # Existing callers retain the strict historical behavior until they adopt
    # this explicit composition; no blanket change to old publication rules.
    with pytest.raises(Conflict), psycopg.connect(dsn, row_factory=dict_row) as conn:
        monthly_store.publish(conn, selection, raw)
