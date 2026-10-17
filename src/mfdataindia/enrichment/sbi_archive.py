"""SBI create-only PDF/XML archives; frozen pilot archival remains unchanged."""

import base64
import hashlib
from urllib.parse import quote

from mfdataindia.enrichment.refresh_policy import ARCHIVE_BUCKET


def retain(session, content, *, media_type):
    prefix = "official-monthly-facts/sbi-expanded/"
    if media_type not in ("application/pdf", "application/xml"):
        raise ValueError("registered SBI original media required")
    if (
        not isinstance(content, bytes)
        or (media_type == "application/pdf" and not content.startswith(b"%PDF-"))
        or not 0
        < len(content)
        <= (16 * 1024 * 1024 if media_type == "application/pdf" else 1048576)
    ):
        raise ValueError("bounded original PDF required for archive")
    digest = hashlib.sha256(content).hexdigest()
    name = prefix + digest + (".pdf" if media_type == "application/pdf" else ".xml")
    identity = session.get(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
        timeout=10,
        allow_redirects=False,
    )
    if identity.status_code != 200 or not isinstance(identity.json().get("access_token"), str):
        raise ValueError("archive workload identity unavailable")
    headers = {"Authorization": "Bearer " + identity.json()["access_token"]}
    md5 = base64.b64encode(hashlib.md5(content, usedforsecurity=False).digest()).decode()
    created = session.post(
        "https://storage.googleapis.com/upload/storage/v1/b/" + ARCHIVE_BUCKET + "/o",
        params={"uploadType": "media", "name": name, "ifGenerationMatch": "0"},
        headers={**headers, "Content-Type": media_type, "Content-MD5": md5},
        data=content,
        timeout=180,
        allow_redirects=False,
    )
    if created.status_code in (200, 201):
        metadata = created.json()
    elif created.status_code == 412:
        # Expected replay only: independently reconcile the retained generation.
        response = session.get(
            "https://storage.googleapis.com/storage/v1/b/"
            + ARCHIVE_BUCKET
            + "/o/"
            + quote(name, safe=""),
            headers=headers,
            timeout=30,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise ValueError("existing archive metadata unavailable")
        metadata = response.json()
    else:
        raise ValueError("create-only original archive failed; no automatic retry")
    generation = str(metadata.get("generation", ""))
    if (
        metadata.get("bucket") != ARCHIVE_BUCKET
        or metadata.get("name") != name
        or not generation.isdigit()
        or int(generation) <= 0
        or str(metadata.get("size")) != str(len(content))
        or metadata.get("md5Hash") != md5
    ):
        raise ValueError("immutable original archive metadata differs")
    response = session.get(
        "https://storage.googleapis.com/storage/v1/b/"
        + ARCHIVE_BUCKET
        + "/o/"
        + quote(name, safe=""),
        params={"alt": "media", "generation": generation},
        headers=headers,
        stream=True,
        timeout=120,
        allow_redirects=False,
    )
    try:
        if response.status_code != 200:
            raise ValueError("generation-pinned original archive unavailable")
        checksum, size = hashlib.sha256(), 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > len(content):
                raise ValueError("retained archive exceeds expected original size")
            checksum.update(chunk)
        if size != len(content) or checksum.hexdigest() != digest:
            raise ValueError("retained original bytes differ")
    finally:
        response.close()
    return {
        "uri": "gs://" + ARCHIVE_BUCKET + "/" + name,
        "generation": generation,
        "sha256": digest,
        "size": len(content),
        "md5": md5,
    }
