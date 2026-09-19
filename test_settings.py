"""First-run and persistent preferences, entirely offline and isolated."""
import json
import os
import pytest

import agent
import runtime_paths
import settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(runtime_paths, 'config_path', lambda: tmp_path / 'legacy.env')
    for key in ('MODEL_PROVIDER', 'MODEL_NAME', 'WEB_SEARCH_ENABLED', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY'):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize('choice,provider', [('1', 'openrouter'), ('2', 'gemini')])
@pytest.mark.parametrize('web,enabled', [('1', True), ('2', False)])
def test_first_run(monkeypatch, capsys, choice, provider, web, enabled):
    answers = iter([choice, web])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(settings.getpass, 'getpass', lambda _: 'offline-secret-value')
    assert settings.ensure_configuration()
    data = json.loads(settings.preferences_path().read_text())
    assert data == {'model_provider': provider, 'model_name': settings.DEFAULT_MODELS[provider], 'web_search_enabled': enabled}
    assert 'offline-secret-value' not in settings.preferences_path().read_text()
    assert 'offline-secret-value' not in capsys.readouterr().out
    assert settings.valid_configuration() and settings.web_enabled() == enabled
    for key in ('MODEL_PROVIDER', 'MODEL_NAME', 'WEB_SEARCH_ENABLED', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    runtime_paths.load_configuration()
    assert settings.provider_name() == provider
    assert settings.valid_configuration() and settings.web_enabled() == enabled


def test_valid_legacy_skips_setup(monkeypatch):
    runtime_paths.config_path().write_text('MODEL_PROVIDER=gemini\nGEMINI_API_KEY=offline-key\nMODEL_NAME=custom-model\n')
    runtime_paths.load_configuration()
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('setup not expected'))
    assert settings.ensure_configuration()
    assert settings.model_name() == 'custom-model' and not settings.web_enabled()


def test_preserves_model(monkeypatch):
    monkeypatch.setenv('MODEL_NAME', 'custom-model')
    answers = iter(['1', '2'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(settings.getpass, 'getpass', lambda _: 'offline-key')
    assert settings.ensure_configuration()
    assert settings.model_name() == 'custom-model'


def test_invalid_settings_fail_closed():
    settings.preferences_path().parent.mkdir(parents=True)
    for data in ('broken', '[]', '{"web_search_enabled": "true"}'):
        settings.preferences_path().write_text(data)
        assert settings.load_preferences() == {}
        assert not settings.web_enabled()


def test_cancel(monkeypatch):
    def cancel(_):
        raise EOFError
    monkeypatch.setattr('builtins.input', cancel)
    assert not settings.ensure_configuration()
    assert not settings.preferences_path().exists()


def test_hidden_input_required(monkeypatch, capsys):
    import warnings
    monkeypatch.setattr('builtins.input', lambda _: '1')
    def no_terminal(_):
        warnings.warn('cannot hide', settings.getpass.GetPassWarning)
    monkeypatch.setattr(settings.getpass, 'getpass', no_terminal)
    assert not settings.ensure_configuration()
    assert 'Hidden key input is unavailable' in capsys.readouterr().out


def test_settings_commands(monkeypatch, capsys):
    from test_conversation import FakeProvider
    model = FakeProvider([])
    model.provider_name = 'openrouter'
    model.model_name = settings.DEFAULT_MODELS['openrouter']
    monkeypatch.setattr(agent, 'model_provider', model)
    answers = iter(['/settings', '/settings web on', '/status', '/settings web off', 'exit'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    agent.main()
    output = capsys.readouterr().out
    assert 'Provider: OpenRouter' in output and 'Model: openrouter/free' in output
    assert 'Web search: enabled' in output and 'Web search: disabled' in output
    assert settings.COST_NOTICE in output
    assert json.loads(settings.preferences_path().read_text())['web_search_enabled'] is False
    assert not model.requests


def test_saved_web_overrides_legacy_but_environment_wins(monkeypatch):
    runtime_paths.config_path().write_text('WEB_SEARCH_ENABLED=true\n')
    settings.save_preferences('gemini', 'custom', False)
    runtime_paths.load_configuration()
    assert not settings.web_enabled()
    monkeypatch.setenv('WEB_SEARCH_ENABLED', 'true')
    runtime_paths.load_configuration()
    assert settings.web_enabled()


def test_failed_save_does_not_toggle(monkeypatch):
    def denied(*args):
        raise PermissionError('private')
    monkeypatch.setattr(settings, 'save_preferences', denied)
    with pytest.raises(PermissionError):
        settings.set_web_enabled(True)
    assert not settings.web_enabled()
