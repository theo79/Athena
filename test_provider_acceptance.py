"""M2.6 acceptance: deterministic restarts and explicitly gated live smoke command."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import provider_cli
import runtime_paths
import settings
from provider_configuration import ProviderConfig, ProviderConfigurationService
from test_conversation import final


@pytest.mark.parametrize('initial,target', [('openai','local'),('local','gemini'),('gemini','openrouter')])
def test_managed_selection_survives_conflicting_restart(tmp_path,monkeypatch,initial,target):
    path=settings.preferences_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'model_provider':initial,'model_name':'old','web_search_enabled':True}))
    endpoint='http://localhost:11434'
    provider=SimpleNamespace(generate=lambda messages:final('ok'),list_models=lambda:['selected'])
    environment={'MODEL_PROVIDER':initial,'MODEL_NAME':'old'}
    service=ProviderConfigurationService(path,environ=environment,provider_factory=lambda *a:provider)
    config=ProviderConfig(target,'selected',endpoint if target=='local' else None)
    assert service.switch(config,credential='synthetic-key',allow_network=True,acknowledge_cloud=True).ok
    fresh={'MODEL_PROVIDER':initial,'MODEL_NAME':'old','MODEL_CREDENTIAL_ENV':'OLD_SECRET_REFERENCE'}
    restarted=ProviderConfigurationService(path,environ=fresh)
    assert restarted.read().provider==target and restarted.read().model=='selected'
    assert 'MODEL_PROVIDER' in restarted.selection_status()['conflicts']
    legacy=tmp_path/'legacy.env'
    legacy.write_text(f'MODEL_PROVIDER={initial}\nMODEL_NAME=legacy\n')
    monkeypatch.setattr(runtime_paths,'config_path',lambda:legacy)
    for name,value in fresh.items(): monkeypatch.setenv(name,value)
    runtime_paths.load_configuration()
    assert settings.provider_name()==target and settings.model_name()=='selected'
    assert 'MODEL_PROVIDER' in ProviderConfigurationService().selection_status()['conflicts']
    assert json.loads(path.read_text())['web_search_enabled'] is True


def test_legacy_override_remains_supported(tmp_path):
    path=tmp_path/'legacy.json'
    path.write_text('{"model_provider":"openai","model_name":"saved"}')
    service=ProviderConfigurationService(path,environ={'MODEL_PROVIDER':'gemini','MODEL_NAME':'intentional'})
    assert service.read().provider=='gemini' and service.read().model=='intentional'
    assert service.selection_status()['policy']=='legacy_environment_first'


def test_failed_switch_does_not_change_precedence(tmp_path):
    path=tmp_path/'config.json'
    path.write_text('{"custom":true}')
    service=ProviderConfigurationService(path,environ={})
    before=path.read_bytes()
    assert not service.switch(ProviderConfig('openai','chosen'),allow_network=True,acknowledge_cloud=True).ok
    assert path.read_bytes()==before


def test_smoke_command_no_network_without_flag(monkeypatch,capsys):
    service=ProviderConfigurationService(environ={'MODEL_PROVIDER':'openai','MODEL_NAME':'chosen','OPENAI_API_KEY':'synthetic'})
    factory=Mock(side_effect=AssertionError('network without authorization'))
    service._factory=factory
    monkeypatch.setattr(provider_cli,'ProviderConfigurationService',lambda:service)
    monkeypatch.setattr(runtime_paths,'load_configuration',lambda:None)
    assert provider_cli.main(['config','smoke-test'])==1
    factory.assert_not_called()
    assert 'test_required' in capsys.readouterr().out


def test_explicit_smoke_reports_timing_without_content(monkeypatch,capsys):
    service=ProviderConfigurationService(environ={'MODEL_PROVIDER':'openai','MODEL_NAME':'chosen','OPENAI_API_KEY':'synthetic-secret'})
    provider=SimpleNamespace(generate=Mock(return_value=final('private response text')))
    service._factory=lambda *a:provider
    monkeypatch.setattr(provider_cli,'ProviderConfigurationService',lambda:service)
    monkeypatch.setattr(runtime_paths,'load_configuration',lambda:None)
    assert provider_cli.main(['config','smoke-test','--run-live','--acknowledge-cloud'])==0
    data=json.loads(capsys.readouterr().out)
    assert data['ok'] and data['elapsed_ms']>=0 and data['provider']=='openai'
    assert 'synthetic-secret' not in str(data) and 'private response text' not in str(data)
