"""Synthetic decoder compatibility; never source/runtime acceptance evidence."""

import ast
import hashlib
import io
import json
from pathlib import Path
import runpy
import tomllib

import pypdf
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject
import pytest

from mfdataindia.enrichment import (
    book_sources, layout_book_pdf_v3, pdf_security_v2, uti_book_pdf_v3, uti_book_pdf_v5,
)
from mfdataindia.review.policy import fingerprint

ROOT = Path(__file__).resolve().parents[1]


def pdf(*, missing=False, content=b"", encrypted=False, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=200, height=200)
        if not missing:
            stream = DecodedStreamObject()
            stream.set_data(content)
            page.replace_contents(stream.flate_encode())
    if encrypted:
        writer.encrypt("synthetic-only")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def decode(raw, decoder):
    return (layout_book_pdf_v3.extract(raw, profile=decoder)
            if isinstance(decoder, str) else decoder.extract(raw))


@pytest.mark.parametrize("decoder", ["uti", "kotak", uti_book_pdf_v5])
def test_actual_new_isolated_workers_explicit_empty_stream(decoder):
    raw = pdf()
    result = decode(raw, decoder)
    assert result["source_hash"] == hashlib.sha256(raw).hexdigest()
    assert result["extractor_version"] == "6.21.2"
    assert result["document_pages"] == 1
    assert result["pages"][0]["text"] == ""
    assert result["whole_document_semantic_coverage"] is False
    policy = layout_book_pdf_v3.policy(decoder) if isinstance(decoder, str) else decoder.policy()
    assert policy["dependency_contract"] == pdf_security_v2.dependency_contract()
    assert result["policy_hash"] == fingerprint(policy)
    assert result["extraction_hash"] == fingerprint({k: v for k, v in result.items()
                                                   if k != "extraction_hash"})
    if decoder is uti_book_pdf_v5:
        assert result["pages"][0]["physical_left_text"] is None
        assert result["engine_hash"] == decoder.engine_hash()
    assert pdf_security_v2.admit_extraction(
        result, dependency=pdf_security_v2.dependency_contract(),
        engine_hash=result["engine_hash"], policy_hash=fingerprint(policy)) == result


@pytest.mark.parametrize("decoder", ["uti", "kotak", uti_book_pdf_v5])
@pytest.mark.parametrize("case", ["missing", "encrypted", "streams", "pages", "malformed"])
def test_actual_isolated_new_workers_fail_closed(decoder, case):
    raw = {"missing": lambda: pdf(missing=True), "encrypted": lambda: pdf(encrypted=True),
           "streams": lambda: pdf(content=b" " * 10000001), "pages": lambda: pdf(pages=401),
           "malformed": lambda: b"%PDF-1.7\nnot a PDF"}[case]()
    with pytest.raises(ValueError, match="decoder failed"):
        decode(raw, decoder)


def test_original_financial_layout_and_geometry_unchanged_on_both_aum_forms():
    fixture = runpy.run_path(str(ROOT / "scripts/smoke_uti_aum_decoder.py"))["fixture"]
    for joined in (False, True):
        raw = fixture(joined=joined)
        old, new = uti_book_pdf_v3.extract(raw), uti_book_pdf_v5.extract(raw)
        assert old["pages"] == new["pages"]
        assert new["pages"][0]["physical_aum_row"]["spans"][-2]["text"] == "4,094.35"
        assert old["policy_hash"] != new["policy_hash"]
        assert old["engine_hash"] != new["engine_hash"]
    old = uti_book_pdf_v3.policy()
    new = uti_book_pdf_v5.policy()
    for key in old:
        if key not in ("version", "financial_field_rules_changed"):
            assert new[key] == old[key]
    assert new["financial_field_rules_changed"] is False


@pytest.mark.parametrize("field,value,message", [
    ("extractor_version", "6.20.1", "extractor version"),
    ("engine_hash", "b" * 64, "engine"),
    ("policy_hash", "b" * 64, "policy"),
    ("extraction_hash", "b" * 64, "self-hash"),
])
def test_admission_checks_independent_fields_even_if_other_hashes_are_resealed(field, value, message):
    body = {"extractor_version": "6.21.2", "engine_hash": "e" * 64,
            "policy_hash": "a" * 64, "pages": []}
    body["extraction_hash"] = fingerprint(body)
    changed = {**body, field: value}
    if field != "extraction_hash":
        changed["extraction_hash"] = fingerprint({k: v for k, v in changed.items()
                                                  if k != "extraction_hash"})
    with pytest.raises(ValueError, match=message):
        pdf_security_v2.admit_extraction(changed, dependency=pdf_security_v2.dependency_contract(),
                                     engine_hash="e" * 64, policy_hash="a" * 64)


def test_dependency_contract_cannot_be_relabelled_and_runtime_must_match(monkeypatch):
    bad = {**pdf_security_v2.dependency_contract(), "version": "6.20.1"}
    with pytest.raises(ValueError, match="contract"):
        pdf_security_v2.admit_extraction({}, dependency=bad, engine_hash="e", policy_hash="p")
    monkeypatch.setattr(pdf_security_v2, "version", lambda _: "6.20.1")
    with pytest.raises(ValueError, match="patched pypdf"):
        uti_book_pdf_v5.extract(b"%PDF-")


def test_new_uti_engine_seals_transitive_historical_layout_policy_without_editing_it(monkeypatch):
    # v4 -> retained v2/v1 policy -> historical layout policy, including limits.
    # Test the engine artifact independently of the whole-history manifest.
    historical = Path(uti_book_pdf_v5.uti_book_pdf_v4.uti_book_pdf_v2.layout_book_pdf.__file__).resolve()
    original_read = Path.read_bytes
    original_bytes = historical.read_bytes()
    before = uti_book_pdf_v5.engine_hash()

    def changed_read(path):
        raw = original_read(path)
        return raw + b"\n# synthetic lineage tamper\n" if path.resolve() == historical else raw

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", changed_read)
        assert uti_book_pdf_v5.engine_hash() != before
    assert historical.read_bytes() == original_bytes
    assert uti_book_pdf_v5.engine_hash() == before


def test_historical_bytes_and_all_registered_authorities_are_unchanged_and_new_decoder_dormant():
    baseline = json.loads((ROOT / "docs/PDF_HISTORICAL_AUTHORITY_4157.json").read_bytes())
    for path, sha in baseline["frozen_files"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha, path
    for name, sha in baseline["runtime_archive_sha256"].items():
        path = ROOT / "src/mfdataindia/enrichment" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, name
    assert set(book_sources.REGISTRIES) == {1, 2, 3, 4, 5}
    assert book_sources.CURRENT_VERSION == 5
    for v in book_sources.REGISTRIES:
        assert fingerprint(book_sources.configuration(v)) == baseline["configurations"][str(v)]
        assert fingerprint(book_sources.current_policy(v)) == baseline["policies"][str(v)]
        assert "dependency_contract" not in json.dumps(book_sources.configuration(v))
    assert baseline["retargeted_test_previous_sha256"] == (
        "02626622d30a78276be0765dd49ef83a3597faf00d6c285d4119a6c33c585c85")


def test_exact_patched_dependency_and_artifacts_locked():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "pypdf==6.21.2" in project["project"]["dependencies"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    package = next(p for p in lock["package"] if p["name"] == "pypdf")
    assert package["version"] == pdf_security_v2.PYPDF_VERSION == "6.21.2"
    assert package["sdist"]["hash"] == "sha256:" + pdf_security_v2.SDIST_SHA256
    assert len(package["wheels"]) == 1
    assert package["wheels"][0]["hash"] == "sha256:" + pdf_security_v2.WHEEL_SHA256
    assert not any(p["name"].lower() == "pillow" for p in lock["package"])


def test_installed_security_fix_restricts_dct_image_branch_to_jpeg():
    tree = ast.parse((Path(pypdf.__file__).parent / "generic/_image_xobject.py").read_text())
    branches = [n for n in ast.walk(tree) if isinstance(n, ast.If)
                and ast.unparse(n.test) == "last_filter == FT.DCT_DECODE"]
    assert len(branches) == 1
    calls = [n for s in branches[0].body for n in ast.walk(s)
             if isinstance(n, ast.Call) and ast.unparse(n.func) == "Image.open"]
    assert len(calls) == 1
    assert ast.literal_eval(next(k.value for k in calls[0].keywords if k.arg == "formats")) == ("JPEG",)
