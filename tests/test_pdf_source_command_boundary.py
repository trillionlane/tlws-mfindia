"""Actual unmocked source admission; old internal helpers are not process authority."""

import ast
import builtins
from pathlib import Path
import runpy
import sys

import psycopg
import pytest
import requests

from mfdataindia.enrichment import pdf_source_dispatch

ROOT = Path(__file__).resolve().parents[1]
REFRESH = ("refresh_official_facts_dev.py", "refresh_registered_facts_dev.py",
           "refresh_amc_books_dev.py")
COMMANDS = (
    ("official_enrichment_evidence.py", ["import", "--manifest", "missing", "--document", "missing"]),
    ("capture_official_sources.py", ["--archive", "missing", "extract-pdf", "--document", "missing", "--expected-sha", "0" * 64]),
    ("capture_official_sources.py", ["--archive", "missing", "discover-absl", "--year", "2026"]),
    ("capture_official_sources.py", ["--archive", "missing", "document", "--url", "https://example.invalid", "--official-host", "example.invalid", "--authority-reference", "none"]),
    ("discover_idcw_evidence.py", ["--manifest", "missing", "--source-dir", "missing", "--output", "missing"]),
    ("enrich_amc_factsheets.py", ["--dry-run"]),
    ("deploy_official_facts_dev.py", ["publish"]),
    ("operate_monthly_facts_dev.py", ["append"]),
    ("deploy_official_evidence_dev.py", ["import"]),
    ("deploy_idcw_catalog_dev.py", ["append"]),
    ("deploy_complete_portfolios_dev.py", ["import"]),
    ("deploy_complete_portfolios_dev.py", ["publish"]),
    ("deploy_distribution_observations_dev.py", ["append"]),
)


@pytest.mark.parametrize("filename", REFRESH)
def test_refresh_command_denies_before_registry_validation_runtime_import_or_io(filename, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    original = builtins.__import__
    denied_imports = {"book_runtime", "sbi_runtime", "kotak_runtime", "uti_runtime",
                      "uti_runtime_v2", "uti_runtime_v3", "uti_runtime_v4",
                      "multi_runtime", "refresh_runtime"}

    def checked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if denied_imports.intersection(fromlist or ()):
            pytest.fail("source runtime imported before compatibility dispatcher")
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", checked_import)
    module = runpy.run_path(str(ROOT / "scripts" / filename))
    with pytest.raises(ValueError, match="source authority unallocated"):
        module["run"](None, None)


@pytest.mark.parametrize("filename,args", COMMANDS, ids=[f + ":" + a[0] for f, a in COMMANDS])
def test_source_commands_reject_before_any_credentials_manifest_db_http_or_file(filename, args, monkeypatch, capsys):
    # Load script text first; the no-I/O tripwires are installed by this test
    # afterwards so importlib/pytest collection is not accidentally intercepted.
    script = ROOT / "scripts" / filename
    source = script.read_text()
    code = compile(source, str(script), "exec")
    calls = []
    real_guard = pdf_source_dispatch.require_source_authority

    def observed_real_guard():
        calls.append("real-deny")
        return real_guard()  # No mock admission or bypass.

    monkeypatch.setattr(pdf_source_dispatch, "require_source_authority", observed_real_guard)

    def forbidden(*a, **k):
        pytest.fail("DB/provider/file touched before source deny")

    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    # Python may inspect installed distribution METADATA during imports. This
    # is not source input; all manifest/config/PDF reads still trip the guard.
    for method in ("read_bytes", "read_text"):
        original = getattr(Path, method)

        def checked_read(path, *a, _read=original, **k):
            # Imports inspect installed code hashes and package metadata, not
            # caller source inputs. Keep this allowlist limited to code or
            # interpreter-managed package metadata; no config/PDF/manifest.
            if (path.suffix == ".py" or any(part.endswith((".dist-info", ".egg-info"))
                                            for part in path.parts)
                    or path.name == "entry_points.txt"):
                return _read(path, *a, **k)
            return forbidden(path)

        monkeypatch.setattr(Path, method, checked_read)
    monkeypatch.delenv("REVIEW_MANIFEST", raising=False)
    monkeypatch.setenv("MFDATAINDIA_DSN", "private-tripwire")
    monkeypatch.setattr(sys, "argv", [str(script), *args])
    try:
        exec(code, {"__name__": "__main__", "__file__": str(script)})
    except SystemExit as error:
        assert error.code == 1
    except ValueError as error:
        assert "source authority unallocated" in str(error)
    else:
        pytest.fail("source command did not fail closed")
    assert calls == ["real-deny"]
    output = capsys.readouterr()
    assert "private-tripwire" not in output.out + output.err


def test_direct_script_publication_function_denies_before_manifest_or_db():
    module = runpy.run_path(str(ROOT / "scripts/deploy_official_facts_dev.py"))
    with pytest.raises(ValueError, match="source authority unallocated"):
        module["execute"](None, None, "publish")


def test_idcw_public_run_denies_before_files(tmp_path):
    module = runpy.run_path(str(ROOT / "scripts/discover_idcw_evidence.py"))
    with pytest.raises(ValueError, match="source authority unallocated"):
        module["run"](tmp_path / "absent", tmp_path, tmp_path / "report")
    assert list(tmp_path.iterdir()) == []


def test_no_environment_override_or_caller_supplied_authority(monkeypatch):
    for name in ("PDF_SOURCE_ALLOW", "SKIP_PDF_SECURITY", "PYPDF_VERSION", "PDF_DEPENDENCY_CONTRACT"):
        monkeypatch.setenv(name, "6.21.1")
    with pytest.raises(ValueError, match="source authority unallocated"):
        pdf_source_dispatch.require_source_authority()


def test_script_inventory_forbids_new_direct_source_runtime_callers():
    # Review/Cloud Run accepts these executable source commands, not arbitrary
    # Python imports. Internal historical runtime modules remain hash-frozen.
    actual = set()
    runtime_names = {"book_runtime", "sbi_runtime", "kotak_runtime", "uti_runtime",
                     "uti_runtime_v2", "uti_runtime_v3", "uti_runtime_v4",
                     "multi_runtime", "refresh_runtime"}
    for script in (ROOT / "scripts").glob("*.py"):
        tree = ast.parse(script.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported = {a.name for a in node.names}
                if (node.module == "mfdataindia.enrichment" and imported & runtime_names
                        or node.module in {"mfdataindia.enrichment." + n for n in runtime_names}):
                    actual.add(script.name)
    assert actual == set(REFRESH)
    for filename in REFRESH:
        tree = ast.parse((ROOT / "scripts" / filename).read_text())
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
        assert isinstance(run.body[0], ast.Expr)
        assert ast.unparse(run.body[0]) == "require_source_authority()"


def test_supported_console_commands_do_not_expose_historical_pdf_libraries():
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["scripts"] == {"mfdataindia-amfi": "mfdataindia.ingest.amfi_navall:main"}
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        text = path.read_text()
        assert not any("python -m mfdataindia.enrichment." + n in text
                       for n in ("book_runtime", "uti_runtime", "kotak_runtime", "sbi_runtime"))
