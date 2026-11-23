"""Finite SBI v9 predecessor composition; no provider or database I/O."""
import hashlib
import json
from pathlib import Path

import pytest

from mfdataindia.enrichment import amc_gate_source_revision, amc_source_revision
from mfdataindia.enrichment import hdfc_source_revision
from mfdataindia.enrichment import sbi_document_date_source_revision as revision

ROOT = Path(__file__).resolve().parents[1]
PATHS = {
    "book_sources": {
        "current": "6cd62f07cf815b45e36dbd4f977bfc23d9159836950dbbf33f335d619020d236",
        "parent": "3417de8f671d9dc56b7f163d1496fd3180466fd9a5cbb6667fbb6cd5a751c1cd",
        "hdfc": "c18abb9c4cc7a02b430b2ad0595f399ea971599733048dab70a1b17ef0c3fb74",
        "amc": "26f4363058998b0a03a18eb702744b7cc09b817b18ab036294bed1f185d9e551",
        "gate": "5f3bbf8cc0f607f65cb4e837614412bc78f35eaae89234d34b188a220ddc6983",
    },
    "book_store": {
        "current": "f639ca946047edb2383b71858cfa848a2b5ee782fe4c16ca39b105677a8a9fa0",
        "parent": "60acc212b06f5a8a38daac38a6c42a6af66c3aaa620ad9ad95bfda8b4ff86d07",
        "hdfc": "796bcac64576ca3f1612259c00b454d499d60604485f7222481c8cab9c181fb2",
        "amc": "8de21e08d12e2dde50e7954cb82dbc0282a419c4516c8c3882bcbf70160fd69a",
        "gate": "88592de099ef4fb1e8de6d3abac150944b80cee40d1894219375b01e803ab41f",
    },
    "hdfc_source_revision": {
        "current": "1553b80d02ee3500b46ea950d2fdffc7ebd2ef52cdfaf1cefb6e44c1a1a795cb",
        "parent": "4e51a917d9aae170f9559c3055c3ab49ebf622969fb707b57671336695a8f073",
        "hdfc": "4e51a917d9aae170f9559c3055c3ab49ebf622969fb707b57671336695a8f073",
        "amc": "4e51a917d9aae170f9559c3055c3ab49ebf622969fb707b57671336695a8f073",
        "gate": "4e51a917d9aae170f9559c3055c3ab49ebf622969fb707b57671336695a8f073",
    },
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def test_exact_current_parent_and_retained_chains_are_finite():
    authority = (ROOT / revision.FILE).read_bytes()
    assert digest(authority) == revision.SHA256
    document = json.loads(authority)
    assert set(document["modified"]) == {
        f"src/mfdataindia/enrichment/{name}.py" for name in PATHS
    }
    for name, expected in PATHS.items():
        path = ROOT / f"src/mfdataindia/enrichment/{name}.py"
        raw = path.read_bytes()
        row = document["modified"][path.relative_to(ROOT).as_posix()]
        assert digest(raw) == row["current_sha256"] == expected["current"]
        assert digest(revision.previous(path)) == row["previous_sha256"] == expected["parent"]
        assert digest(hdfc_source_revision.previous(path)) == expected["hdfc"]
        assert digest(amc_source_revision.previous_bytes(path)) == expected["amc"]
        assert digest(amc_gate_source_revision.previous_bytes(path, version=6)) == expected["gate"]
        restored = raw
        for replacement in reversed(row["replacements"]):
            after = replacement["after"].encode()
            assert replacement["after_occurrences"] == restored.count(after) == 1
            restored = restored.replace(after, replacement["before"].encode(), 1)
        assert digest(restored) == expected["parent"]


def test_named_unknown_bytes_and_authority_tamper_fail_closed(monkeypatch):
    for name in PATHS:
        path = ROOT / f"src/mfdataindia/enrichment/{name}.py"
        current = path.read_bytes()
        parent = revision.previous(path)
        assert revision.previous(path, parent) == parent
        for raw in (current + b"\n# unknown", current[:-1], parent + b"\n# unknown"):
            with pytest.raises(ValueError, match="SBI_DOCUMENT_DATE_UNREVIEWED_SOURCE_BYTES"):
                revision.previous(path, raw)
    assert revision.previous(ROOT / "README.md", b"unrelated path") == b"unrelated path"
    monkeypatch.setattr(revision, "SHA256", "0" * 64)
    with pytest.raises(ValueError, match="SBI_DOCUMENT_DATE_PREDECESSOR_AUTHORITY_DIFFERS"):
        revision.previous(ROOT / "src/mfdataindia/enrichment/book_sources.py")


def test_all_existing_v1_authorities_remain_byte_identical():
    expected = {
        "config/official-hdfc-installer-policy-reuse-predecessors-v1.json": "ad83dd1fef91440800af9dbe28cbd8cc321b6099215ea8310ff6bd38b4adb3c9",
        "config/official-hdfc-dynamic-predecessors-v1.json": "f4a55546b54b0e1e90b98369f321fb7d364c80b37c260ec39b6f1e8cad5597ba",
        "config/official-amc-source-extension-v1.json": "c97f3bbe729af2541bf50199d1d8a4c85c06e9c97cf1cc259fb0431c08a76900",
        "config/official-amc-gate-source-predecessors-v1.json": "58278781167af4173e10cad42dd718bbddcf898d33481a001a8ba263faa2f154",
    }
    assert {name: digest((ROOT / name).read_bytes()) for name in expected} == expected
