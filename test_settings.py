"""First-run and persistent preferences, entirely offline and isolated."""
import json
import os
import pytest

import agent
import runtime_paths
import settings
from types import SimpleNamespace
from unittest.mock import Mock
from provider_configuration import ProviderConfigurationService


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(runtime_paths, 'config_path', lambda: tmp_path / 'legacy.env')
    for key in ('MODEL_PROVIDER', 'MODEL_NAME', 'MODEL_CREDENTIAL_ENV', 'WEB_SEARCH_ENABLED', 'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY'):
        monkeypatch.setenv(key, '')
        monkeypatch.delenv(key)


@pytest.mark.parametrize('choice,provider', [('3', 'openrouter'), ('2', 'gemini'), ('1', 'openai')])
@pytest.mark.parametrize('enabled', [True, False])
def test_first_run(monkeypatch, capsys, choice, provider, enabled):
    monkeypatch.setenv('WEB_SEARCH_ENABLED', str(enabled).lower())
    settings.preferences_path().parent.mkdir(parents=True)
    settings.preferences_path().write_text(json.dumps({'web_search_enabled':enabled}))
    answers = iter(['2', choice, '1', '', 'chosen-model', '1'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(settings.getpass, 'getpass', lambda _: 'offline-secret-value')
    provider_instance=SimpleNamespace(generate=Mock(return_value='{"action":"final","answer":"ok"}'))
    monkeypatch.setattr(ProviderConfigurationService, 'create_provider', lambda *a, **k: provider_instance)
    assert settings.ensure_configuration()
    data=json.loads(settings.preferences_path().read_text())
    assert data['model_provider']==provider and data['model_name']=='chosen-model'
    assert data['web_search_enabled'] is enabled
    assert data['credential_env']==settings.KEY_NAMES[provider]
    assert 'offline-secret-value' not in settings.preferences_path().read_text()+capsys.readouterr().out
    assert not (runtime_paths.user_config_dir()/'.env').exists()
    for key in ('MODEL_PROVIDER','MODEL_NAME','MODEL_CREDENTIAL_ENV',settings.KEY_NAMES[provider]):
        monkeypatch.delenv(key,raising=False)
    runtime_paths.load_configuration()
    assert settings.provider_name()==provider and not settings.valid_configuration()
    monkeypatch.setenv(settings.KEY_NAMES[provider],'offline-environment-value')
    assert settings.valid_configuration()


def test_valid_legacy_skips_setup(monkeypatch):
    runtime_paths.config_path().write_text('MODEL_PROVIDER=gemini\nGEMINI_API_KEY=offline-key\nMODEL_NAME=custom-model\n')
    runtime_paths.load_configuration()
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('setup not expected'))
    assert settings.ensure_configuration()
    assert settings.model_name() == 'custom-model' and not settings.web_enabled()


def test_preserves_model(monkeypatch):
    answers=iter(['2','3','1','','custom-model','1'])
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    monkeypatch.setattr(settings.getpass,'getpass',lambda _:'offline-key')
    provider=SimpleNamespace(generate=Mock(return_value='{"action":"final","answer":"ok"}'))
    monkeypatch.setattr(ProviderConfigurationService,'create_provider',lambda *a,**k:provider)
    assert settings.ensure_configuration()
    assert settings.model_name()=='custom-model'


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
    answers=iter(['2','1','1','','selected-model'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
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
