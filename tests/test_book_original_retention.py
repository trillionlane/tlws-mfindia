"""Mocked lifecycle only: no source-coverage, archive-generation or DB proof."""

import hashlib
import importlib
from datetime import datetime, timezone

import pytest


SOURCES = ["kotak", "uti"]


def prepared(monkeypatch, kind, *, failure=None, same_bytes=False):
    runtime = importlib.import_module("mfdataindia.enrichment." + kind + "_runtime")
    parser = getattr(runtime, kind + "_book", None) or runtime.book
    acquisition = getattr(runtime, kind + "_acquisition")
    compiler = getattr(runtime, kind + "_batch")
    key = kind + "-expanded"
    spec = runtime.book_sources.specification(key)
    events = []
    clock = [0.0]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, *_):
            return None

    monkeypatch.setattr(runtime.psycopg, "connect", lambda *_args, **_kw: Connection())
    monkeypatch.setattr(runtime.book_store, "verify_acl", lambda *_: None)
    monkeypatch.setattr(runtime.book_store, "installed_configuration", lambda *_: {
        "document": {"source_specifications": {key: spec}}
    })
    monkeypatch.setattr(runtime.book_store, "population", lambda *_: [{"scheme_name": "Unit fund"}])
    monkeypatch.setattr(runtime.book_runtime, "receipt", lambda *_: None)
    monkeypatch.setattr(runtime.book_store, "append_run", lambda _conn, document: document)

    def publish(_conn, _key, _execution, _observed, _identities, _batches,
                _originals, archives, **_kwargs):
        events.append(("publish", None))
        return {"status": "updated", "archives": list(archives), "mocked_lifecycle_only": True}

    monkeypatch.setattr(runtime.book_runtime, "publish", publish)
    requests = [{"source_url": "unit://catalogue-0", "expected_period": "2026-09-30"},
                {"source_url": "unit://catalogue-1", "expected_period": "2026-08-31"}]
    monkeypatch.setattr(acquisition, "recent_catalogues", lambda *_: requests)
    if kind == "kotak":
        monkeypatch.setattr(acquisition, "catalogue_book", lambda _raw, url:
                            "unit://book-" + url.rsplit("-", 1)[1])
    else:
        monkeypatch.setattr(acquisition, "catalogue_books", lambda _raw, url: [
            {"kind": book_kind, "source_url": "unit://book-" + url.rsplit("-", 1)[1] + book_kind}
            for book_kind in ("active", "passive")
        ])
    periods = iter([r["expected_period"] for r in requests])
    monkeypatch.setattr(parser, "period", lambda *_: next(periods))
    monkeypatch.setattr(runtime.sbi_book, "directory", lambda *_: [])
    monkeypatch.setattr(runtime, "candidates", lambda *_: [])
    monkeypatch.setattr(compiler, "compile_batch", lambda *_args, **kw:
                        {"mocked_lifecycle_only": True, "as_of": kw["as_of"]})
    if failure == "deadline_after_archive":
        monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])

    def transport(url):
        events.append(("fetch", url))
        if failure == "directory" and url == parser.DIRECTORY:
            raise TimeoutError("deliberate unit directory failure")
        pdf = url.startswith("unit://book-")
        content = b"%PDF-unit" + (b"" if same_bytes else url.encode()) if pdf else b"{}"
        return {"source_url": url, "content": content,
                "media_type": "application/pdf" if pdf else "application/json"}

    def extract(raw):
        events.append(("decode", hashlib.sha256(raw).hexdigest()))
        if failure == "decode":
            raise ValueError("deliberate unit decoder failure")
        return {"mocked_lifecycle_only": True}

    def archive(session, raw, *, media_type):
        assert session.trust_env is False
        assert media_type == "application/pdf"
        checksum = hashlib.sha256(raw).hexdigest()
        events.append(("archive", checksum))
        if failure == "archive":
            raise TimeoutError("deliberate unit ambiguous archive failure")
        if failure == "deadline_after_archive":
            clock[0] = 521
        return {"uri": "gs://unit-originals/" + checksum + ".pdf", "generation": "1",
                "sha256": checksum, "size": len(raw), "md5": "unit-only"}

    def run():
        return runtime.run("unit-only-no-database", "unit-execution",
                           now=datetime(2026, 10, 17, tzinfo=timezone.utc),
                           transport=transport, extractor=extract, archive=archive)

    return runtime, run, events


@pytest.mark.parametrize("kind", SOURCES)
def test_original_is_retained_before_decoder_rejection(monkeypatch, kind):
    _, run, events = prepared(monkeypatch, kind, failure="decode")
    result = run()
    assert result["status"] == "failed"
    assert result["error_type"] == "ValueError"
    assert len(result["archives"]) == 1
    assert [kind for kind, _ in events if kind != "fetch"] == ["archive", "decode"]
    assert result["archives"][0]["sha256"] == next(value for event, value in events if event == "archive")
    assert result["source_rows_changed"] == 0


@pytest.mark.parametrize("kind", SOURCES)
def test_later_acquisition_failure_keeps_downloaded_book_archives(monkeypatch, kind):
    _, run, events = prepared(monkeypatch, kind, failure="directory")
    result = run()
    assert result["status"] == "failed"
    assert result["phase"] == "directory"
    expected = 2 if kind == "kotak" else 4
    assert len(result["archives"]) == expected
    assert len([event for event, _ in events if event == "archive"]) == expected
    assert not any(event == "publish" for event, _ in events)


@pytest.mark.parametrize("kind", SOURCES)
def test_success_does_not_archive_each_pdf_twice(monkeypatch, kind):
    _, run, events = prepared(monkeypatch, kind)
    result = run()
    expected = 2 if kind == "kotak" else 4
    assert result["mocked_lifecycle_only"]
    assert len(result["archives"]) == expected
    assert len([event for event, _ in events if event == "archive"]) == expected
    assert events[-1][0] == "publish"


@pytest.mark.parametrize("kind", SOURCES)
def test_same_pdf_bytes_are_retained_once_across_distinct_urls(monkeypatch, kind):
    _, run, events = prepared(monkeypatch, kind, same_bytes=True)
    result = run()
    assert result["mocked_lifecycle_only"]
    assert len(result["archives"]) == 1
    assert len([event for event, _ in events if event == "archive"]) == 1


@pytest.mark.parametrize("kind", SOURCES)
def test_archive_failure_does_not_retry_decode_or_publish(monkeypatch, kind):
    _, run, events = prepared(monkeypatch, kind, failure="archive")
    result = run()
    assert result["status"] == "failed"
    assert result["phase"] == "archive"
    assert result["error_type"] == "TimeoutError"
    assert result["archives"] == []
    assert [event for event, _ in events if event != "fetch"] == ["archive"]


@pytest.mark.parametrize("kind", SOURCES)
def test_elapsed_budget_after_verified_archive_keeps_original_without_publishing(monkeypatch, kind):
    _, run, events = prepared(monkeypatch, kind, failure="deadline_after_archive")
    result = run()
    assert result["status"] == "failed"
    assert result["phase"] == "archive"
    assert result["error_type"] == "TimeoutError"
    assert len(result["archives"]) == 1
    assert [event for event, _ in events if event != "fetch"] == ["archive"]


@pytest.mark.parametrize("kind", SOURCES)
def test_existing_execution_receipt_prevents_repeat_acquisition(monkeypatch, kind):
    runtime, run, events = prepared(monkeypatch, kind)
    previous = {"status": "updated", "mocked_lifecycle_only": True}
    monkeypatch.setattr(runtime.book_runtime, "receipt", lambda *_: previous)
    assert run() is previous
    assert events == []
