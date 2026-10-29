"""Offline provider configuration, switching and presentation tests."""
from dataclasses import asdict
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from google.genai import errors
from openai import APIStatusError, APITimeoutError, APIConnectionError, AuthenticationError, RateLimitError

import agent
import models
import provider_cli
import provider_configuration as configuration
from provider_configuration import ProviderConfig, ProviderConfigurationService
import runtime_paths
import settings
from test_conversation import FakeProvider, final
from test_memory_service import services
from memory_retrieval import MemoryRetrieval


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in ('MODEL_PROVIDER','MODEL_NAME','MODEL_CREDENTIAL_ENV','OLLAMA_BASE_URL',
                 'OPENAI_API_KEY','GEMINI_API_KEY','OPENROUTER_API_KEY','WEB_SEARCH_ENABLED'):
        monkeypatch.setenv(name, '')
        monkeypatch.delenv(name)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(runtime_paths, 'config_path', lambda: tmp_path / 'legacy.env')
    monkeypatch.setattr(models, 'OpenAI', Mock(side_effect=AssertionError('Unmocked SDK request')))
    monkeypatch.setattr(models.genai, 'Client', Mock(side_effect=AssertionError('Unmocked SDK request')))


@pytest.fixture
def setup(tmp_path):
    factory = Mock(return_value=SimpleNamespace(generate=Mock(return_value=final('connection ok')),
                                               list_models=Mock(return_value=['local-model:latest'])))
    environment = {}
    service = ProviderConfigurationService(tmp_path / 'config # % μ.json', environ=environment, provider_factory=factory)
    return service, environment, factory


def cloud(name='openai'):
    return ProviderConfig(name, 'selected-model')


def test_first_local_save_and_reload(setup):
    service, env, factory = setup
    config = ProviderConfig('local','local-model','http://127.0.0.1:11434')
    result = service.switch(config, allow_network=True)
    assert result.ok and result.capabilities.text_generation and result.capabilities.action_protocol
    assert result.capabilities.native_tools is None and result.capabilities.structured_outputs is None
    assert service.read() == config
    assert ProviderConfigurationService(service.path, environ={}).read() == config
    assert not env.get('OPENAI_API_KEY')
    factory.return_value.list_models.assert_called_once()


@pytest.mark.parametrize('name', ['openai','gemini','openrouter'])
def test_cloud_save_with_environment_reference(setup, name):
    service, env, factory = setup
    key = 'synthetic-private-value'
    result = service.switch(cloud(name), credential=key, allow_network=True, acknowledge_cloud=True)
    assert result.ok
    data = json.loads(service.path.read_text(encoding='utf-8'))
    assert data['credential_env'] == configuration.KEY_NAMES[name]
    assert key not in service.path.read_text(encoding='utf-8') + repr(result) + repr(service.read())
    assert env[configuration.KEY_NAMES[name]] == key
    assert ProviderConfigurationService(service.path, environ={}).read().provider == name
    assert service.create_provider(service.read()) is factory.return_value


def test_requires_explicit_test_and_cloud_ack(setup):
    service, env, factory = setup
    config = cloud()
    assert service.test(config, credential='fake').code == 'test_required'
    assert service.switch(config, credential='fake', allow_network=True).code == 'cloud_ack_required'
    factory.assert_not_called()
    assert not service.path.exists() and not env


def test_missing_credential(setup):
    service, env, factory = setup
    assert service.switch(cloud(), allow_network=True, acknowledge_cloud=True).code == 'missing_credentials'
    factory.assert_not_called()
    assert not service.path.exists()


@pytest.mark.parametrize('code', ['invalid_credentials','model_unavailable','unreachable','timeout',
                                  'rate_limit','unsupported_capability','provider_error'])
def test_failure_preserves_file_environment_and_settings(setup, code):
    service, env, factory = setup
    original = {'model_provider':'local','model_name':'previous','ollama_base_url':'http://localhost:11434',
                'web_search_enabled':True,'identity':{'name':'Athena'},'permissions':{'approval':True}}
    service.path.write_text(json.dumps(original),encoding='utf-8')
    env.update(MODEL_PROVIDER='local',MODEL_NAME='previous')
    before = service.path.read_bytes(),dict(env)
    factory.return_value.generate.return_value = models.ModelFailure('never expose this secret', code)
    result = service.switch(cloud(), credential='secret-candidate', allow_network=True, acknowledge_cloud=True)
    assert result.code == code and not result.ok
    assert 'secret' not in repr(result)
    assert (service.path.read_bytes(),dict(env)) == before


def test_switch_success_preserves_unrelated_data(setup, tmp_path):
    service, env, factory = setup
    data={'web_search_enabled':True,'identity':'Athena','permissions':{'write':False},'custom':{'x':1}}
    service.path.write_text(json.dumps(data))
    memory = tmp_path/'memory.json'
    experience = tmp_path/'experiences.json'
    memory.write_bytes(b'[{"text":"private saved memory"}]')
    experience.write_bytes(b'[{"id":"unchanged"}]')
    before = memory.read_bytes(),experience.read_bytes()
    assert service.switch(cloud(),credential='one',allow_network=True,acknowledge_cloud=True).ok
    assert service.switch(ProviderConfig('local','local-model','http://localhost:11434'),allow_network=True).ok
    persisted=json.loads(service.path.read_text())
    assert all(persisted[k]==v for k,v in data.items())
    assert (memory.read_bytes(),experience.read_bytes())==before
    assert env['MODEL_PROVIDER']=='local' and 'MODEL_CREDENTIAL_ENV' not in env


def test_save_failure_preserves_previous_configuration(setup, monkeypatch):
    service,env,factory=setup
    service.path.write_text('{"custom":true}')
    before=service.path.read_bytes(),dict(env)
    monkeypatch.setattr(configuration.os,'replace',Mock(side_effect=PermissionError('credential-secret')))
    result=service.switch(cloud(),credential='new-secret',allow_network=True,acknowledge_cloud=True)
    assert result.code=='storage_error'
    assert (service.path.read_bytes(),dict(env))==before
    assert not list(service.path.parent.glob('.settings-*'))


def test_concurrent_configuration_change_during_test(setup):
    service,env,factory=setup
    def change(messages):
        service.path.write_text('{"external":"changed"}')
        return final('ok')
    factory.return_value.generate.side_effect=change
    result=service.switch(cloud(),credential='new-secret',allow_network=True,acknowledge_cloud=True)
    assert result.code=='configuration_changed' and not env
    assert json.loads(service.path.read_text())=={'external':'changed'}


@pytest.mark.parametrize('config', [ProviderConfig('unknown','model'),ProviderConfig('openai',''),
    ProviderConfig('local','model','https://example.com'),ProviderConfig('local','model','http://user:secret@localhost'),
    ProviderConfig('local','model','http://localhost?key=secret'),ProviderConfig('openai','model',endpoint='http://localhost'),
    ProviderConfig('gemini','model',credential_env='invalid name')])
def test_invalid_configuration_has_no_writes_or_network(setup,config):
    service,env,factory=setup
    assert service.switch(config,allow_network=True,acknowledge_cloud=True).code=='invalid_configuration'
    assert not service.path.exists() and not env
    factory.assert_not_called()


def test_model_capability_mismatch_and_no_tool_execution(setup):
    service,env,factory=setup
    for output in ('plain text',json.dumps({'action':'tool','tool':'save_memory','args':{'text':'unapproved'}}),None):
        factory.return_value.generate.return_value=output
        result=service.switch(cloud(),credential='fake',allow_network=True,acknowledge_cloud=True)
        assert result.code=='unsupported_capability'
    assert not service.path.exists()


def test_model_must_exist_locally(setup):
    service,env,factory=setup
    result=service.switch(ProviderConfig('local','missing','http://localhost:11434'),allow_network=True)
    assert result.code=='model_unavailable'
    factory.return_value.generate.assert_not_called()


def test_probe_sends_only_synthetic_context(setup):
    service,env,factory=setup
    service.test(cloud(),credential='hidden',allow_network=True,acknowledge_cloud=True)
    messages=factory.return_value.generate.call_args.args[0]
    assert len(messages)==1 and 'Connection test only' in messages[0]['content']
    assert 'hidden' not in json.dumps(messages)


def test_arbitrary_provider_exception_is_not_exposed(setup):
    service,env,factory=setup
    factory.side_effect=RuntimeError('Authorization: Bearer synthetic-secret')
    result=service.test(cloud(),credential='synthetic-secret',allow_network=True,acknowledge_cloud=True)
    assert result.code=='provider_error' and 'synthetic-secret' not in repr(result)


def test_legacy_selection_and_aliases(setup):
    service,env,factory=setup
    env.update(MODEL_PROVIDER='ollama',MODEL_NAME='legacy-model',OLLAMA_BASE_URL='http://localhost:11434')
    assert service.read()==ProviderConfig('local','legacy-model','http://localhost:11434')
    assert not service.path.exists()


def test_unsupported_saved_config_is_not_overwritten(setup):
    service,env,factory=setup
    for value in ('malformed','[]','{"provider_config_version":2}'):
        service.path.write_text(value)
        assert not service.switch(cloud(),credential='fake',allow_network=True,acknowledge_cloud=True).ok
        assert service.path.read_text()==value


@pytest.mark.parametrize('provider,cls', [('local',models.OllamaProvider),('openai',models.OpenAIProvider),
                                        ('gemini',models.GeminiProvider),('openrouter',models.OpenRouterProvider)])
def test_correct_adapter_initialization(tmp_path,provider,cls):
    service=ProviderConfigurationService(tmp_path/'config.json',environ={})
    config=ProviderConfig(provider,'selected-model','http://localhost:11434') if provider=='local' else cloud(provider)
    adapter=service.create_provider(config,credential='fake')
    assert isinstance(adapter,cls) and adapter.model_name=='selected-model'


def test_openai_responses_adapter(monkeypatch):
    create=Mock(return_value=SimpleNamespace(output_text=final('done')))
    constructor=Mock(return_value=SimpleNamespace(responses=SimpleNamespace(create=create)))
    monkeypatch.setattr(models,'OpenAI',constructor)
    provider=models.OpenAIProvider('user-selected-model',api_key='test-only-secret')
    messages=[{'role':'user','content':'synthetic request'}]
    assert provider.generate(messages)==final('done')
    create.assert_called_once_with(model='user-selected-model',input=messages,store=False)
    assert constructor.call_args.kwargs['base_url']=='https://api.openai.com/v1'
    assert constructor.call_args.kwargs['max_retries']==0


@pytest.mark.parametrize('code,expected', [(401,'invalid_credentials'),(403,'invalid_credentials'),
    (404,'model_unavailable'),(429,'rate_limit'),(400,'unsupported_capability'),(503,'provider_error')])
def test_openai_status_categories(monkeypatch,code,expected):
    request=httpx.Request('POST','https://example.test')
    error=APIStatusError('private-secret-body',response=httpx.Response(code,request=request),body={'secret':'hidden'})
    provider=models.OpenAIProvider('chosen',api_key='fake')
    provider.client=SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=error)))
    result=provider.generate([])
    assert result.code==expected and 'private-secret' not in repr(result)


@pytest.mark.parametrize('error,expected', [(APITimeoutError(request=httpx.Request('POST','https://example.test')),'timeout'),
    (APIConnectionError(request=httpx.Request('POST','https://example.test'),message='hidden'),'unreachable')])
def test_openai_transport_categories(error,expected):
    provider=models.OpenAIProvider('chosen',api_key='fake')
    provider.client=SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=error)))
    assert provider.generate([]).code==expected


def test_hidden_key_never_written_or_echoed(setup,monkeypatch,capsys):
    service,env,factory=setup
    answers=iter(['2','1','1','','selected-model','1'])
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    monkeypatch.setattr(provider_cli.getpass,'getpass',lambda _:'synthetic-private-api-key')
    assert provider_cli.configure_provider(service) is factory.return_value
    assert 'synthetic-private-api-key' not in capsys.readouterr().out+service.path.read_text()
    assert not (service.path.parent/'.env').exists()


def test_local_ui_manual_entry_without_credentials(setup,monkeypatch):
    service,env,factory=setup
    answers=iter(['1','http://localhost:11434','2','local-model','1'])
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    monkeypatch.setattr(provider_cli.getpass,'getpass',lambda _:pytest.fail('local credential prompt'))
    assert provider_cli.configure_provider(service) is factory.return_value


def test_cloud_ack_cancel_does_not_test_or_save(setup,monkeypatch):
    service,env,factory=setup
    answers=iter(['2','1','2'])
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    assert provider_cli.configure_provider(service) is None
    factory.assert_not_called()
    assert not service.path.exists()


def test_cli_config_command_uses_shared_service(monkeypatch):
    sentinel=object()
    configure=Mock(return_value=sentinel)
    monkeypatch.setattr(provider_cli,'configure_provider',configure)
    monkeypatch.setattr(runtime_paths,'load_configuration',Mock())
    assert provider_cli.main(['config','provider'])==0
    assert isinstance(configure.call_args.args[0],ProviderConfigurationService)


def test_in_chat_switch_preserves_conversation_and_approval(monkeypatch,tmp_path):
    old=FakeProvider([final('before')])
    new=FakeProvider([json.dumps({'action':'tool','tool':'save_memory','args':{'text':'unapproved'}}),final('after')])
    new.provider_name='local'
    new.model_name='chosen'
    monkeypatch.setattr(agent,'model_provider',old)
    monkeypatch.setattr(provider_cli,'configure_provider',Mock(return_value=new))
    answers=iter(['first','/config provider','second','no','exit'])
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    agent.main()
    assert {'role':'assistant','content':'before'} in new.requests[0]
    assert 'Action rejected' in new.requests[1][-1]['content']
    import tools
    assert not tools.MEMORY_FILE.exists()


def test_sqlite_compatibility_across_provider_switch(services,setup):
    from memory_service import MemoryDraft
    service,env,factory=setup
    memory=services()
    identity=memory.save_memory(MemoryDraft('weather sensors persistent fact'))
    assert service.switch(cloud(),credential='fake',allow_network=True,acknowledge_cloud=True).ok
    assert MemoryRetrieval(memory).retrieve('weather sensors').memories[0]['id']==identity
    assert service.switch(ProviderConfig('local','local-model','http://localhost:11434'),allow_network=True).ok
    assert memory.read_memory(identity).draft.content=='weather sensors persistent fact'


def test_custom_credential_reference_is_redacted(monkeypatch):
    from tools import redact_secrets
    monkeypatch.setenv('MODEL_CREDENTIAL_ENV','MY_VENDOR')
    monkeypatch.setenv('MY_VENDOR','a-long-private-key-value')
    assert redact_secrets('value=a-long-private-key-value')=='value=[REDACTED]'


def test_read_and_construction_do_not_connect(setup,monkeypatch):
    service,env,factory=setup
    service.path.write_text('{"model_provider":"openai","model_name":"chosen","credential_env":"OPENAI_API_KEY"}')
    env['OPENAI_API_KEY']='private-read-test-key'
    assert service.read().provider=='openai'
    assert 'private-read-test-key' not in repr(service.read())
    factory.assert_not_called()


def test_test_operation_does_not_change_persistent_or_process_state(setup):
    service,env,factory=setup
    before=dict(env)
    result=service.test(cloud(),credential='private-test-key',allow_network=True,acknowledge_cloud=True)
    assert result.ok and not service.path.exists() and dict(env)==before


def test_config_show_does_not_display_credentials(monkeypatch,capsys):
    path=settings.preferences_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"model_provider":"openai","model_name":"chosen","credential_env":"MY_VENDOR"}')
    monkeypatch.setenv('MY_VENDOR','hidden-provider-credential')
    assert provider_cli.main(['config','show'])==0
    output=capsys.readouterr().out
    assert 'MY_VENDOR' in output and 'hidden-provider-credential' not in output
    models.OpenAI.assert_not_called()


def test_cancelling_in_chat_switch_keeps_cached_provider(monkeypatch):
    old=FakeProvider([final('before'),final('after')])
    monkeypatch.setattr(agent,'model_provider',old)
    monkeypatch.setattr(provider_cli,'configure_provider',Mock(return_value=None))
    answers=iter(['first','/config provider','second','exit'])
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    agent.main()
    assert agent.model_provider is old and len(old.requests)==2
    assert {'role':'assistant','content':'before'} in old.requests[1]


def test_local_failure_never_selects_cloud(setup):
    service,env,factory=setup
    factory.return_value.list_models.return_value=models.ModelFailure('failed','unreachable')
    result=service.switch(ProviderConfig('local','chosen','http://localhost:11434'),allow_network=True)
    assert result.code=='unreachable' and not service.path.exists()
    assert all(call.args[0].provider=='local' for call in factory.call_args_list)
    models.OpenAI.assert_not_called()
    models.genai.Client.assert_not_called()


def test_environment_provider_does_not_inherit_other_model_or_credential_reference(setup):
    service,env,factory=setup
    service.path.write_text('{"model_provider":"openai","model_name":"old","credential_env":"OLD_REFERENCE"}')
    env['MODEL_PROVIDER']='gemini'
    config=service.read()
    assert config.model==settings.DEFAULT_MODELS['gemini'] and config.credential_env=='GEMINI_API_KEY'


def test_gemini_invalid_key_error_category(monkeypatch):
    provider=models.GeminiProvider('chosen',api_key='fake')
    provider.client=SimpleNamespace(models=SimpleNamespace(generate_content=Mock(side_effect=
        errors.APIError(400,{'error':{'message':'API key not valid. synthetic-secret'}}))))
    result=provider.generate([{'role':'user','content':'synthetic'}])
    assert result.code=='invalid_credentials' and 'synthetic-secret' not in repr(result)


def test_environment_reference_name_is_not_treated_as_the_secret(monkeypatch):
    from tools import redact_secrets
    monkeypatch.setenv('MODEL_CREDENTIAL_ENV','OPENAI_API_KEY')
    monkeypatch.setenv('OPENAI_API_KEY','actual-private-test-key')
    assert redact_secrets('Use OPENAI_API_KEY; never actual-private-test-key')=='Use OPENAI_API_KEY; never [REDACTED]'
