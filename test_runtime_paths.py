"""Packaged path/configuration checks without real credentials or user data."""
from pathlib import Path
import sys

import pytest

import agent
import experience
import runtime_paths as paths
import tools


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    app = tmp_path / "application"
    app.mkdir()
    extraction = tmp_path / "_MEI-isolated"
    extraction.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "Athena.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(extraction), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-data"))
    for name in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "MODEL_PROVIDER", "MODEL_NAME",
                 "PYTHON_DOTENV_DISABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(extraction)
    return app, extraction


def test_source_paths_ignore_working_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.chdir(tmp_path)
    source = Path(paths.__file__).resolve().parent
    assert paths.application_dir() == source
    assert paths.config_path() == source / ".env"
    assert paths.data_dir() == source
    assert tools.MEMORY_FILE == paths.memory_path() == source / "memory.json"
    assert paths.experience_path() == source / "experiences.json"


def test_packaged_paths(frozen, tmp_path):
    app, extraction = frozen
    assert paths.application_dir() == app
    assert paths.config_path() == app / ".env"
    assert paths.data_dir() == tmp_path / "user-data" / "Athena"
    assert paths.memory_path() == paths.data_dir() / "memory.json"
    assert paths.experience_path() == paths.data_dir() / "experiences.json"
    assert paths.prepare_data_dir().is_dir()
    assert not list(extraction.iterdir())
    assert not list(app.iterdir())


@pytest.mark.parametrize("value", [None, "", "relative"])
def test_localappdata_fallback(frozen, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("LOCALAPPDATA")
    else:
        monkeypatch.setenv("LOCALAPPDATA", value)
    assert paths.data_dir() == Path.home() / "AppData" / "Local" / "Athena"


def test_env_is_external_and_does_not_override_environment(frozen, monkeypatch):
    app, extraction = frozen
    (extraction / ".env").write_text("MODEL_PROVIDER=wrong\n")
    (app / ".env").write_text("MODEL_PROVIDER=gemini\nGEMINI_API_KEY=offline-placeholder\n")
    monkeypatch.setenv("MODEL_PROVIDER", "openrouter")
    paths.load_configuration()
    import os
    assert os.environ["MODEL_PROVIDER"] == "openrouter"
    assert os.environ["GEMINI_API_KEY"] == "offline-placeholder"


def test_missing_config_cli_exits_without_provider(frozen, monkeypatch, capsys):
    monkeypatch.setattr(agent, "model_provider", None)
    monkeypatch.setattr(agent, "get_model_provider", lambda: pytest.fail("must not construct provider"))
    def cancel(_):
        raise EOFError
    monkeypatch.setattr("builtins.input", cancel)
    agent.main()
    output = capsys.readouterr().out
    assert "Welcome to Athena." in output
    assert "Setup cancelled" in output
    assert "Traceback" not in output and "Ready." not in output


@pytest.mark.parametrize("provider,key", [("openrouter", "OPENROUTER_API_KEY"), ("gemini", "GEMINI_API_KEY")])
def test_missing_provider_key(frozen, provider, key):
    app, _ = frozen
    (app / ".env").write_text(f"MODEL_PROVIDER={provider}\n")
    assert paths.packaged_startup_error() is None
    import settings
    assert not settings.valid_configuration()


def test_invalid_provider_does_not_echo_value(frozen):
    app, _ = frozen
    (app / ".env").write_text("MODEL_PROVIDER=private-value-do-not-print\n")
    error = paths.packaged_startup_error()
    assert error is None
    import settings
    assert not settings.valid_configuration()


def test_unreadable_config_is_safe(frozen, monkeypatch):
    app, _ = frozen
    (app / ".env").touch()
    def denied():
        raise PermissionError("private-path-and-value")
    monkeypatch.setattr(paths, "load_configuration", denied)
    assert paths.packaged_startup_error() == "Athena could not read configuration or prepare its user data directory. Check file permissions."


def test_unwritable_data_is_safe(frozen):
    app, _ = frozen
    (app / ".env").write_text("OPENROUTER_API_KEY=offline-placeholder\n")
    paths.data_dir().parent.mkdir()
    paths.data_dir().write_text("directory blocked by file")
    assert "Check file permissions" in paths.packaged_startup_error()


@pytest.mark.parametrize("name", ["memory.json", "experiences.json"])
def test_legacy_copy_never_overwrites_or_deletes(frozen, name):
    app, extraction = frozen
    raw = b'[{"text":"legacy"}]'
    (app / name).write_bytes(raw)
    (app / (name + ".bak")).write_bytes(b"[]")
    paths.prepare_data_dir()
    assert (paths.data_dir() / name).read_bytes() == raw
    assert (paths.data_dir() / (name + ".bak")).read_bytes() == b"[]"
    assert (app / name).read_bytes() == raw
    (paths.data_dir() / name).write_bytes(b"[]")
    paths.prepare_data_dir()
    assert (paths.data_dir() / name).read_bytes() == b"[]"
    assert not list(extraction.iterdir())


def test_failed_legacy_copy_removes_partial_destination(frozen, monkeypatch):
    app, _ = frozen
    (app / "memory.json").write_bytes(b"[]")
    def fail_copy(source, destination):
        destination.write(b"partial")
        raise OSError("simulated failure")
    monkeypatch.setattr(paths.shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError):
        paths.prepare_data_dir()
    assert not paths.memory_path().exists()
    assert (app / "memory.json").read_bytes() == b"[]"
    assert not list(paths.data_dir().glob("*.tmp"))


def test_legacy_copy_does_not_replace_racing_destination(frozen):
    app, _ = frozen
    source = app / "memory.json"
    source.write_bytes(b"[]")
    paths.data_dir().mkdir(parents=True)
    paths.memory_path().write_bytes(b'[{"text":"newer"}]')
    paths._copy_legacy(source, paths.memory_path())
    assert paths.memory_path().read_bytes() == b'[{"text":"newer"}]'
    assert not list(paths.data_dir().glob("*.tmp"))


def test_packaged_stores_keep_permission_and_recovery_behavior(frozen, monkeypatch):
    app, extraction = frozen
    paths.prepare_data_dir()
    monkeypatch.setattr(tools, "MEMORY_FILE", paths.memory_path())
    monkeypatch.setattr(experience, "EXPERIENCES_FILE", paths.experience_path())
    result = agent.execute_tool("save_memory", {"text": "offline preference"})
    assert not result.success and not paths.memory_path().exists()
    result = agent.execute_tool("save_memory", {"text": "offline preference"}, confirmation=lambda _: True)
    assert result.success
    tools.MEMORY_FILE.write_bytes(b"corrupt")
    tools.load_memory()
    assert list(paths.data_dir().glob("memory.json.corrupt-*"))
    assert paths.memory_path().with_name("memory.json.bak").exists()
    experience.load_experiences()
    assert paths.experience_path().exists()
    assert not list(app.iterdir()) and not list(extraction.iterdir())
