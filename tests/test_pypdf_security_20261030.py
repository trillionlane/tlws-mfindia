"""Bounded upstream security regressions and dormant successor lineage."""

import ast
import hashlib
import inspect
import io
from pathlib import Path
import runpy
import subprocess
import textwrap
import tomllib

import pypdf
import pytest

from pypdf_patch_preservation import PLAN, ROOT

BASE = "03f3932eb22218478f82e7985e1d0ba527f22cde"


def source(function):
    return textwrap.dedent(inspect.getsource(function))


def tree(function):
    return ast.parse(source(function))


def test_119208_page_labels_single_pass_instead_of_per_page_tree_walk():
    from pypdf import _page_labels

    assert hasattr(_page_labels, "index2labels")
    body = source(pypdf.PdfReader.page_labels.fget)
    assert "return index2labels(self, len(self.pages))" in body
    assert "page_index2page_label(self, i)" not in body
    writer = pypdf.PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=10, height=10)
    writer.set_page_label(0, 3, prefix="internal-")
    out = io.BytesIO()
    writer.write(out)
    assert pypdf.PdfReader(io.BytesIO(out.getvalue())).page_labels == ["internal-"] * 4


def test_119206_decrypted_page_count_uses_flattened_tree_not_declared_count():
    from pypdf.generic import NameObject, NumberObject

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=10, height=10)
    writer.encrypt("synthetic-only")
    out = io.BytesIO()
    writer.write(out)
    reader = pypdf.PdfReader(io.BytesIO(out.getvalue()))
    reader.decrypt("synthetic-only")
    reader.root_object["/Pages"][NameObject("/Count")] = NumberObject(17)
    assert reader.get_num_pages() == 1


def test_119209_xref_stream_total_has_one_global_budget_before_processing():
    signature = inspect.signature(pypdf.PdfReader)
    assert signature.parameters["xref_stream_maximum_entries"].default == 2_000_000
    body = source(pypdf.PdfReader._read_pdf15_xref_stream)
    assert "self._xref_stream_entry_count += sum(index_pairs[1::2])" in body
    assert "self._xref_stream_entry_count > self._xref_stream_maximum_entries" in body
    assert body.index("raise LimitReachedError") < body.index("stream_data = BytesIO")
    reset = source(pypdf.PdfReader._read_xref_tables_and_trailers)
    assert "self._xref_stream_entry_count = 0" in reset


def test_119207_dictionary_exception_output_bounded_and_memory_failure_not_recovered():
    from pypdf.generic import DictionaryObject

    body = source(DictionaryObject.read_from_stream)
    assert "repr(exc)[:500]" in body
    handlers = [n for n in ast.walk(tree(DictionaryObject.read_from_stream))
                if isinstance(n, ast.ExceptHandler)]
    fatal = [n for n in handlers if n.type is not None
             and "MemoryError" in ast.unparse(n.type)]
    assert len(fatal) == 1
    assert any(isinstance(n, ast.Raise) and n.cause is not None for n in fatal[0].body)
    assert "Error reading PDF file." in body


def test_119203_trailer_eof_loop_requires_forward_progress():
    body = source(pypdf.PdfReader._read_standard_xref_table)
    assert "previous_position: Optional[int] = None" in body
    assert "rewound_position == previous_position" in body
    assert "Xref parsing stuck at position" in body
    nodes = [n for n in ast.walk(tree(pypdf.PdfReader._read_standard_xref_table))
             if isinstance(n, ast.If) and ast.unparse(n.test) == "rewound_position == previous_position"]
    assert len(nodes) == 1 and any(isinstance(n, ast.Raise) for n in nodes[0].body)


def test_119205_dictionary_clone_tracks_source_reference_not_new_destination():
    from pypdf.generic import DictionaryObject

    body = source(DictionaryObject._clone)
    for literal in ("source_reference = cur_obj.indirect_reference",
                    "source_idnum = source_reference.idnum",
                    "source_generation = source_reference.generation",
                    "(source_idnum, source_generation) in visited",
                    "visited.add((source_idnum, source_generation))"):
        assert literal in body
    assert body.index("source_reference =") < body.index("source_idnum =")


def test_119204_flattened_attribute_budget_charged_before_page_allocation():
    from pypdf._configuration import Configuration
    from pypdf._doc_common import PdfDocCommon

    assert Configuration().page_tree_maximum_page_entries == 2_000_000
    body = source(PdfDocCommon._flatten_leaf_page)
    assert "remaining_page_entries.entry_count -= entry_count" in body
    assert "remaining_page_entries.entry_count < 0" in body
    assert body.index("raise LimitReachedError") < body.index("page = PageObject")
    assert "sum((attribute not in node for attribute in item.inherit))" in ast.unparse(tree(PdfDocCommon._flatten_leaf_page))
    assert "configuration.page_tree_maximum_page_entries" in source(PdfDocCommon._flatten)


def test_only_pypdf_lock_and_its_exact_project_requirement_changed():
    old = tomllib.loads(subprocess.check_output(["git", "show", BASE + ":uv.lock"], cwd=ROOT).decode())
    new = tomllib.loads((ROOT / "uv.lock").read_text())
    assert old.keys() == new.keys()
    old_packages = {p["name"]: p for p in old.pop("package")}
    new_packages = {p["name"]: p for p in new.pop("package")}
    assert old == new and old_packages.keys() == new_packages.keys()
    assert old_packages.pop("pypdf")["version"] == "6.21.2"
    patched = new_packages.pop("pypdf")
    assert patched["version"] == pypdf.__version__ == "6.21.4"
    assert patched["wheels"][0]["hash"] == "sha256:33e4fb41ca352810eb4e9838aa0d82d6888a5f8bf682f43e99cce5faa666255c"
    assert patched["sdist"]["hash"] == "sha256:c8817e3f888d47a087872ded69812c1ef30cacb871d6e053e0c00bbaf0bc2163"
    for packages in (old_packages, new_packages):
        project = packages["mfdataindia"]
        requirement = next(x for x in project["metadata"]["requires-dist"] if x["name"] == "pypdf")
        assert requirement.pop("specifier") in ("==6.21.2", "==6.21.4")
    assert old_packages == new_packages
    assert new_packages["filelock"]["version"] == "4.2.1" and "pillow" not in new_packages


def test_all_historical_pdf_source_and_smoke_bytes_modes_remain_exact():
    records = subprocess.check_output(["git", "ls-tree", "-rz", BASE], cwd=ROOT).split(b"\0")
    checked = []
    for row in records:
        if not row:
            continue
        identity, encoded = row.split(b"\t", 1)
        name = encoded.decode()
        if name.startswith("src/mfdataindia/enrichment/") or name == "scripts/smoke_pdf_security_compatibility.py":
            assert name not in PLAN["modified"]
            mode, kind, blob = identity.decode().split()
            raw = (ROOT / name).read_bytes()
            assert kind == "blob" and not (ROOT / name).is_symlink()
            assert bool((ROOT / name).stat().st_mode & 0o111) == (mode == "100755")
            assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == blob
            checked.append(name)
    assert len(checked) > 30


@pytest.mark.parametrize("target", ["contract", "layout", "uti", "smoke"])
def test_previous_runtime_contracts_reject_before_any_spawn(monkeypatch, target):
    from mfdataindia.enrichment import layout_book_pdf_v3, pdf_security_v2, uti_book_pdf_v5

    assert pypdf.__version__ == "6.21.4"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("old worker or command spawned"))
    with pytest.raises(ValueError, match="exact reviewed patched pypdf"):
        if target == "contract":
            pdf_security_v2.require_runtime()
        elif target == "layout":
            layout_book_pdf_v3.extract(b"%PDF-", profile="uti")
        elif target == "uti":
            uti_book_pdf_v5.extract(b"%PDF-")
        else:
            runpy.run_path(str(ROOT / "scripts/smoke_pdf_security_compatibility.py"))["main"]()


@pytest.mark.parametrize("filename", [
    "pdf_security_v3.py", "layout_book_pdf_v4.py", "_layout_book_pdf_worker_v4.py",
    "_uti_book_pdf_worker_v6.py", "uti_book_pdf_v5.py", "_uti_book_pdf_worker_v5.py",
])
def test_new_uti_engine_seals_new_worker_and_complete_previous_engine(monkeypatch, filename):
    from mfdataindia.enrichment import uti_book_pdf_v5, uti_book_pdf_v6
    from mfdataindia.review.policy import fingerprint

    assert uti_book_pdf_v6.policy()["retained_security_policy_hash"] == fingerprint(uti_book_pdf_v5.policy())
    target = ROOT / "src/mfdataindia/enrichment" / filename
    read = Path.read_bytes
    before = uti_book_pdf_v6.engine_hash()
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", lambda p: read(p) + (b"\n# tamper" if p.resolve() == target else b""))
        assert uti_book_pdf_v6.engine_hash() != before
    assert uti_book_pdf_v6.engine_hash() == before
