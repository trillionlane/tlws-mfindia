import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def module():
    path = ROOT / "scripts" / "install_fund_tagging.py"
    spec = importlib.util.spec_from_file_location("install_fund_tagging", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def transaction(self):
        return self


def test_install_requires_configuration_and_emits_no_environment(monkeypatch, capsys):
    loaded = module()
    monkeypatch.delenv("MFDATAINDIA_DSN", raising=False)

    assert loaded.main() == 1
    assert json.loads(capsys.readouterr().out) == {
        "reason": "TAGGING_INSTALL_NOT_CONFIGURED",
        "state": "FAILED",
    }


def test_install_uses_one_direct_owner_transaction(monkeypatch, capsys):
    loaded = module()
    connection = Connection()
    observed = {}
    monkeypatch.setenv("MFDATAINDIA_DSN", "secret-value")

    def connect(dsn, **kwargs):
        observed.update(dsn=dsn, kwargs=kwargs)
        return connection

    def install(conn, root):
        assert conn is connection and root == ROOT
        return {"state": "INSTALLED", "source_rows_changed": 0}

    monkeypatch.setattr(loaded.psycopg, "connect", connect)
    monkeypatch.setattr(loaded.store, "install", install)

    assert loaded.main() == 0
    assert observed["dsn"] == "secret-value"
    assert observed["kwargs"]["autocommit"] is True
    assert json.loads(capsys.readouterr().out) == {
        "source_rows_changed": 0,
        "state": "INSTALLED",
    }


def test_install_failure_is_bounded_and_value_safe(monkeypatch, capsys):
    loaded = module()
    monkeypatch.setenv("MFDATAINDIA_DSN", "must-not-appear")
    monkeypatch.setattr(loaded.psycopg, "connect", lambda *_args, **_kwargs: Connection())
    monkeypatch.setattr(
        loaded.store,
        "install",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private SQL")),
    )

    assert loaded.main() == 1
    output = capsys.readouterr().out
    assert "must-not-appear" not in output and "private SQL" not in output
    assert json.loads(output) == {"reason": "TAGGING_INSTALL_FAILED", "state": "FAILED"}
