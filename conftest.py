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


@pytest.fixture(autouse=True)
def isolated_memories(tmp_path, monkeypatch):
    """Automatic retrieval must never read real user memory during tests."""
    import tools
    monkeypatch.setattr(tools, 'MEMORY_FILE', tmp_path / 'memory.json')


@pytest.fixture(autouse=True)
def isolated_provider_configuration(monkeypatch):
    for name in ('MODEL_PROVIDER','MODEL_NAME','MODEL_CREDENTIAL_ENV','OLLAMA_BASE_URL',
                 'OPENAI_API_KEY','GEMINI_API_KEY','OPENROUTER_API_KEY'):
        monkeypatch.setenv(name, '')
        monkeypatch.delenv(name)
