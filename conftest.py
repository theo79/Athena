"""Keep every test's telemetry out of the real experience store."""
import pytest
import experience
import diagnostics


@pytest.fixture(autouse=True)
def isolated_experiences(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "DEBUG", False)
    monkeypatch.setattr(experience, "EXPERIENCES_FILE", tmp_path / "experiences.json")


@pytest.fixture(autouse=True)
def isolated_user_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.delenv("WEB_SEARCH_ENABLED", raising=False)
