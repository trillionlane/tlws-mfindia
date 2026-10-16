"""Hermetic archive replay and metadata/content verification; no cloud calls."""

import base64
import hashlib
from types import SimpleNamespace
import pytest
from mfdataindia.enrichment import refresh_archive as archive
from mfdataindia.enrichment.refresh_policy import ARCHIVE_BUCKET, ARCHIVE_PREFIX


@pytest.mark.parametrize(
    "fault", ["none", "replay", "status", "generation", "size", "hash", "bytes", "overflow"]
)
def test_create_only_archive_verifies_original_generation_without_overwrite(fault):
    raw = b"%PDF-synthetic-original"
    digest = hashlib.sha256(raw).hexdigest()
    name = ARCHIVE_PREFIX + digest + ".pdf"
    metadata = {
        "bucket": ARCHIVE_BUCKET,
        "name": name,
        "generation": "2",
        "size": str(len(raw)),
        "md5Hash": base64.b64encode(hashlib.md5(raw, usedforsecurity=False).digest()).decode(),
    }
    if fault == "generation":
        metadata["generation"] = "0"
    if fault == "size":
        metadata["size"] = "1"
    if fault == "hash":
        metadata["md5Hash"] = "wrong"
    calls = []

    class Session:
        def get(self, url, **kw):
            calls.append(("get", url, kw))
            if url.startswith("http://metadata."):
                return SimpleNamespace(
                    status_code=200, json=lambda: {"access_token": "fixture-token"}
                )
            if kw.get("stream"):
                assert kw["params"] == {"alt": "media", "generation": "2"}
                content = (
                    b"wrong" if fault == "bytes" else raw + b"x" if fault == "overflow" else raw
                )
                return SimpleNamespace(
                    status_code=200, iter_content=lambda size: iter([content]), close=lambda: None
                )
            return SimpleNamespace(status_code=200, json=lambda: metadata)

        def post(self, url, **kw):
            calls.append(("post", url, kw))
            assert kw["params"] == {"uploadType": "media", "name": name, "ifGenerationMatch": "0"}
            assert kw["data"] == raw and not kw["allow_redirects"]
            return SimpleNamespace(
                status_code=412 if fault == "replay" else 500 if fault == "status" else 200,
                json=lambda: metadata,
            )

    if fault in ("none", "replay"):
        assert archive.retain(Session(), raw)["sha256"] == digest
    else:
        with pytest.raises(ValueError):
            archive.retain(Session(), raw)
    assert len([x for x in calls if x[0] == "post"]) == 1
    assert all(not kw.get("allow_redirects") for _, _, kw in calls)
