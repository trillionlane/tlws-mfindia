"""Dormant patched dependency compatibility, not live source authority."""

import ast
import hashlib
import importlib
from importlib.metadata import version
import inspect
import io
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import tomllib

import pytest

from mfdataindia.enrichment import book_sources, layout_book_pdf_v2, pdf_security, uti_book_pdf_v4
from mfdataindia.review.policy import fingerprint

ROOT = Path(__file__).resolve().parents[1]


def new_modules():
    return tuple(
        importlib.import_module("mfdataindia.enrichment." + name)
        for name in ("pdf_security_v2", "layout_book_pdf_v3", "uti_book_pdf_v5")
    )


def test_exact_security_patch_versions_and_both_distribution_artifact_hashes():
    assert version("pypdf") == "6.21.2"
    assert version("filelock") == "4.2.1"
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    expected = {
        "pypdf": (
            "6.21.2",
            "1d44d1a3a200f25062adff0085b02040e4646431ac2ccac22e42679e91466084",
            "0a3e1c5c886a1be03646c16a04827cc166c2a0575c971b26a4bf89686551c2d4",
        ),
        "filelock": (
            "4.2.1",
            "7be2662e9e826b9bba28afc0f843a123280d799a2ea44bb5d35ccec67905ae44",
            "c4c937ccac2a813cee448e662a56366cfb374c0f8702e75f295c412cc1c5d5a3",
        ),
    }
    for name, (wanted, wheel, source) in expected.items():
        package = next(p for p in lock["package"] if p["name"] == name)
        assert package["version"] == wanted
        assert package["sdist"]["hash"] == "sha256:" + source
        assert [w["hash"] for w in package["wheels"]] == ["sha256:" + wheel]
    assert (
        "pypdf==6.21.2"
        in tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    )
    assert not any(p["name"].lower() == "pillow" for p in lock["package"])


def test_all_historical_pdf_files_configurations_and_policies_remain_exact():
    old = json.loads((ROOT / "docs/PDF_SECURITY_AUTHORITY_86AE_20261024.json").read_text())
    for path, sha in old["frozen_files"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha, path
    assert set(book_sources.REGISTRIES) == {1, 2, 3, 4, 5}
    assert book_sources.CURRENT_VERSION == 5
    for v in book_sources.REGISTRIES:
        assert fingerprint(book_sources.configuration(v)) == old["configurations"][str(v)]
        assert fingerprint(book_sources.current_policy(v)) == old["policies"][str(v)]
    assert pdf_security.dependency_contract() == old["pdf_security_v1_contract"]
    assert uti_book_pdf_v4.engine_hash() == old["uti_v4_engine_hash"]
    assert fingerprint(uti_book_pdf_v4.policy()) == old["uti_v4_policy_hash"]
    for name in ("uti", "kotak"):
        assert fingerprint(layout_book_pdf_v2.policy(name)) == old["layout_v2_policy_hashes"][name]


@pytest.mark.parametrize("decoder", ["layout", "uti"])
def test_historical_patched_engines_reject_new_runtime_before_worker_spawn(monkeypatch, decoder):
    assert version("pypdf") == "6.21.2"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("historical worker spawned"))
    with pytest.raises(ValueError, match="exact reviewed patched pypdf"):
        if decoder == "layout":
            layout_book_pdf_v2.extract(b"%PDF-", profile="uti")
        else:
            uti_book_pdf_v4.extract(b"%PDF-")


def test_additive_dependency_contract_and_dormant_policies_are_distinct():
    dependency, layout, uti = new_modules()
    assert dependency.PYPDF_VERSION == "6.21.2"
    assert dependency.dependency_contract()["format"] == "patched-pdf-dependency-v2"
    assert {
        "GHSA-5hx5-vpf9-5wcv",
        "GHSA-6c7f-9265-g8fw",
        "GHSA-jr96-gvgh-j3r4",
        "GHSA-gmh7-3g9r-mjh5",
    } == set(dependency.dependency_contract()["advisories"])
    assert layout.policy("uti")["version"] == 3 and uti.policy()["version"] == 5
    assert uti.policy()["retained_security_policy_hash"] == fingerprint(uti_book_pdf_v4.policy())
    for old, new in (
        (layout_book_pdf_v2.policy("uti"), layout.policy("uti")),
        (uti_book_pdf_v4.policy(), uti.policy()),
    ):
        for key in old:
            if key not in ("version", "dependency_contract"):
                assert new[key] == old[key], key
    with pytest.raises(ValueError, match="unallocated"):
        dependency.reject_unallocated_source_authority()


def test_old_dependency_contract_cannot_be_relabelled_for_new_runtime():
    dependency, _, _ = new_modules()
    body = {"extractor_version": "6.21.2", "engine_hash": "e" * 64, "policy_hash": "a" * 64}
    body["extraction_hash"] = fingerprint(body)
    for old in (
        pdf_security.dependency_contract(),
        {**pdf_security.dependency_contract(), "version": "6.21.2"},
    ):
        with pytest.raises(ValueError, match="contract"):
            dependency.admit_extraction(
                body, dependency=old, engine_hash="e" * 64, policy_hash="a" * 64
            )


@pytest.mark.parametrize(
    "module",
    [
        "layout_book_pdf_v2.py",
        "_layout_book_pdf_worker_v2.py",
        "pdf_security.py",
        "uti_book_pdf_v4.py",
        "_uti_book_pdf_worker_v4.py",
    ],
)
def test_new_uti_engine_seals_every_retained_security_ancestor(monkeypatch, module):
    _, _, uti = new_modules()
    target = (ROOT / "src/mfdataindia/enrichment" / module).resolve()
    before = uti.engine_hash()
    read = Path.read_bytes
    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "read_bytes",
            lambda path: (
                read(path) + (b"\n# synthetic ancestor tamper" if path.resolve() == target else b"")
            ),
        )
        assert uti.engine_hash() != before
    assert uti.engine_hash() == before


def test_filelock_mutual_exclusion_timeout_and_reacquisition(tmp_path):
    from filelock import FileLock, Timeout

    assert version("filelock") == "4.2.1"
    first, second = FileLock(tmp_path / "synthetic.lock"), FileLock(tmp_path / "synthetic.lock")
    with first:
        with pytest.raises(Timeout):
            second.acquire(timeout=0)
    with second.acquire(timeout=0):
        assert second.is_locked
    assert not first.is_locked and not second.is_locked


def test_new_worker_bad_xref_header_is_bounded_without_relaxing_production_limits():
    # A small synthetic malformed document, with an OUTER test-only deadline.
    # No unbounded payload or parser operation occurs in the pytest process.
    dependency, _, _ = new_modules()
    dependency.require_runtime()
    raw = b"%PDF-1.7\nxref\n0 2147483647\n0000000000 65535 f \ntrailer\n<< /Size 2147483647 >>\nstartxref\n9\n%%EOF\n"
    result = subprocess.run(
        [sys.executable, "-I", "-m", "mfdataindia.enrichment._layout_book_pdf_worker_v3", "uti"],
        input=raw,
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == b""


def synthetic_reader():
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    output = io.BytesIO()
    writer.write(output)
    return PdfReader(io.BytesIO(output.getvalue()))


def test_115572_missing_objects_are_cached_even_when_the_value_is_none():
    from pypdf import PdfReader

    assert version("pypdf") == "6.21.2"
    reader = synthetic_reader()
    assert reader.get_object(999999) is None
    assert (0, 999999) in reader.resolved_objects
    assert reader.resolved_objects[(0, 999999)] is None
    for _ in range(100):
        assert reader.get_object(999999) is None
    tree = ast.parse(textwrap.dedent(inspect.getsource(PdfReader.get_object)))
    guards = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.If) and ast.unparse(n.test) == "key in self.resolved_objects"
    ]
    assert len(guards) == 1
    assert any(isinstance(n, ast.Return) for n in guards[0].body)


def test_115585_recovery_stream_read_and_cache_build_are_both_once_guarded():
    from pypdf import PdfReader

    assert version("pypdf") == "6.21.2"
    tree = ast.parse(textwrap.dedent(inspect.getsource(PdfReader._read_standard_xref_table)))
    guards = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.If) and ast.unparse(n.test) == "recovery_cache is None"
    ]
    assert len(guards) == 1
    for function in ("get_stream_data", "self._load_recovery_cache"):
        all_calls = [
            n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == function
        ]
        guarded = [
            n
            for n in ast.walk(guards[0])
            if isinstance(n, ast.Call) and ast.unparse(n.func) == function
        ]
        assert len(all_calls) == len(guarded) == 1
        assert all_calls[0] is guarded[0]


def test_115580_declared_xref_size_is_bounded_by_actual_stream_bytes():
    from pypdf.errors import LimitReachedError

    assert version("pypdf") == "6.21.2"
    with pytest.raises(LimitReachedError, match="Declared size 13 exceeds allowed limit of 5"):
        synthetic_reader()._read_standard_xref_table(io.BytesIO(b"ref\n0 13\n" + b"a" * 100))
