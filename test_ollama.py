"""Ollama tests use an in-memory HTTP transport, never a live server."""
import json
from unittest.mock import Mock

import httpx
import pytest

import agent
import models
import runtime_paths
import search_provider
import settings
import tools


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in ('MODEL_PROVIDER', 'MODEL_NAME', 'OLLAMA_BASE_URL', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY', 'WEB_SEARCH_ENABLED'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runtime_paths, 'config_path', lambda: tmp_path / 'legacy.env')
    monkeypatch.setattr(agent, 'model_provider', None)
    monkeypatch.setattr(models, 'OpenAI', Mock(side_effect=AssertionError('Cloud call')))
    monkeypatch.setattr(models.genai, 'Client', Mock(side_effect=AssertionError('Cloud call')))


@pytest.fixture
def server(monkeypatch):
    original = httpx.Client
    requests = []
    def install(data=None, status=200, error=None, raw=None):
        def respond(request):
            requests.append(request)
            if error:
                raise error
            return httpx.Response(status, json=data, request=request) if raw is None else httpx.Response(status, content=raw, request=request)
        def factory(**kwargs):
            return original(transport=httpx.MockTransport(respond), **kwargs)
        monkeypatch.setattr(models.httpx, 'Client', factory)
        return requests
    # A test that forgets a response must fail before network access.
    monkeypatch.setattr(models.httpx, 'Client', Mock(side_effect=AssertionError('Unexpected HTTP call')))
    return install


def test_factory_no_key(server, monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER', 'ollama')
    provider = models.get_model_provider()
    assert isinstance(provider, models.OllamaProvider)
    assert provider.base_url == 'http://localhost:11434'
    assert provider.model_name == 'llama3.2'
    assert settings.valid_configuration()
    assert settings.ensure_configuration()


@pytest.mark.parametrize('url', ['http://localhost:11434', 'http://127.0.0.1:12345/', 'http://server.local:11434/prefix'])
def test_chat(server, monkeypatch, url):
    monkeypatch.setenv('MODEL_PROVIDER', 'ollama')
    monkeypatch.setenv('MODEL_NAME', 'custom:tag')
    monkeypatch.setenv('OLLAMA_BASE_URL', url)
    result = '{"action":"final","answer":"done"}'
    requests = server({'message': {'role': 'assistant', 'content': result}, 'done': True})
    messages = [{'role': 'system', 'content': 'Return JSON'}, {'role': 'user', 'content': 'question'},
                {'role': 'assistant', 'content': 'prior'}, {'role': 'user', 'content': 'Tool result: data'}]
    before = json.dumps(messages)
    assert models.get_model_provider().generate(messages) == result
    request, = requests
    assert str(request.url) == url.rstrip('/') + '/api/chat'
    assert request.method == 'POST'
    assert json.loads(request.content) == {'model': 'custom:tag', 'messages': messages, 'stream': False, 'format': 'json'}
    assert 'authorization' not in request.headers
    assert request.extensions['timeout']['read'] == 120
    assert request.extensions['timeout']['connect'] == 5
    assert json.dumps(messages) == before


@pytest.mark.parametrize('error,expected', [(httpx.ConnectError('secret'), 'not reachable'),
    (httpx.ReadTimeout('secret'), 'timed out'), (httpx.RemoteProtocolError('secret'), 'not reachable')])
def test_transport_errors(server, error, expected):
    server(error=error)
    result = models.OllamaProvider().generate([])
    assert isinstance(result, models.ModelFailure)
    assert expected in result.message and 'secret' not in result.message


def test_missing_model(server):
    server({'error': 'model missing private-data'}, status=404)
    result = models.OllamaProvider(model_name='llama3.2').generate([])
    assert "model 'llama3.2' is not installed" in result.message
    assert 'ollama pull llama3.2' in result.message and 'private-data' not in result.message


@pytest.mark.parametrize('status', [301, 401, 403, 429, 500])
def test_http_errors(server, status):
    requests = server({'error': 'private-data'}, status=status)
    result = models.OllamaProvider().generate([])
    assert isinstance(result, models.ModelFailure)
    assert 'private-data' not in result.message
    assert len(requests) == 1


@pytest.mark.parametrize('data', [None, [], {}, {'error': 'private'}, {'message': []},
    {'message': {'role': 'assistant', 'content': ''}, 'done': True},
    {'message': {'role': 'assistant', 'content': 123}, 'done': True},
    {'message': {'role': 'assistant', 'content': 'text'}, 'done': False},
    {'message': {'role': 'user', 'content': 'text'}, 'done': True}])
def test_invalid_response(server, data):
    server(data)
    result = models.OllamaProvider().generate([])
    assert isinstance(result, models.ModelFailure) and 'invalid' in result.message


def test_malformed_json(server):
    server(raw=b'not-json private-data')
    assert 'invalid' in models.OllamaProvider().generate([]).message


@pytest.mark.parametrize('url', ['bad', 'ftp://localhost', 'http://user:secret@localhost:11434', 'http://localhost?key=secret'])
def test_invalid_base_url_no_request(server, url):
    provider = models.OllamaProvider(base_url=url)
    assert 'invalid' in provider.generate([]).message
    assert 'secret' not in provider.status_lines()


def test_model_listing(server):
    requests = server({'models': [{'name': 'llama3.2:latest'}, {'name': 'qwen3:8b'}, {'name': 'qwen3:8b'}]})
    assert models.OllamaProvider().list_models() == ['llama3.2:latest', 'qwen3:8b']
    request, = requests
    assert request.method == 'GET' and request.url.path == '/api/tags'
    assert request.extensions['timeout']['read'] == 3


@pytest.mark.parametrize('data', [{}, {'models': None}, {'models': [None]}, {'models': [{'name': 'bad\nname'}]}])
def test_invalid_model_listing(server, data):
    server(data)
    assert isinstance(models.OllamaProvider().list_models(), models.ModelFailure)


def test_setup_no_key(server, monkeypatch, capsys):
    server({'models': [{'name': 'llama3.2:latest'}, {'name': 'qwen3:8b'}]})
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://127.0.0.1:12345')
    answers = iter(['3', '9', '2'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(settings.getpass, 'getpass', lambda _: pytest.fail('No key prompt allowed'))
    assert settings.ensure_configuration()
    output = capsys.readouterr().out
    assert 'Ollama is running.' in output and '2. qwen3:8b' in output
    assert 'Web search is not available in local Ollama mode yet.' in output
    assert json.loads(settings.preferences_path().read_text()) == {
        'model_provider': 'ollama', 'model_name': 'qwen3:8b', 'web_search_enabled': False,
        'ollama_base_url': 'http://127.0.0.1:12345'}
    assert not (runtime_paths.user_config_dir() / '.env').exists()
    for name in ('MODEL_PROVIDER', 'MODEL_NAME', 'OLLAMA_BASE_URL', 'WEB_SEARCH_ENABLED'):
        monkeypatch.delenv(name, raising=False)
    runtime_paths.load_configuration()
    assert settings.valid_configuration() and settings.provider_name() == 'ollama'
    assert models.get_model_provider().base_url == 'http://127.0.0.1:12345'
    assert settings.model_name() == 'qwen3:8b'


@pytest.mark.parametrize('unreachable', [False, True])
def test_setup_unavailable(server, monkeypatch, capsys, unreachable):
    server({'models': []}, error=httpx.ConnectError('private') if unreachable else None)
    monkeypatch.setattr('builtins.input', lambda _: '3')
    assert not settings.ensure_configuration()
    output = capsys.readouterr().out
    assert ('not reachable at http://localhost:11434' if unreachable else 'No Ollama models are installed.') in output
    assert ('ollama serve' if unreachable else 'ollama pull llama3.2') in output
    assert not settings.preferences_path().exists()


@pytest.mark.parametrize('value', ['true', 'false'])
def test_web_unavailable(server, monkeypatch, value):
    monkeypatch.setenv('MODEL_PROVIDER', 'ollama')
    monkeypatch.setenv('WEB_SEARCH_ENABLED', value)
    assert not settings.web_enabled() and settings.web_status() == 'unavailable'
    result = tools.web_search('news')
    assert not result.success and result == 'Web search is not available with Ollama yet.'
    with pytest.raises(settings.WebSearchUnavailable):
        settings.set_web_enabled(True)
    assert not settings.preferences_path().exists()


@pytest.mark.parametrize('connected', [True, False])
def test_status_settings(server, monkeypatch, capsys, connected):
    server({'models': []}, error=None if connected else httpx.ConnectError('private'))
    monkeypatch.setenv('MODEL_PROVIDER', 'ollama')
    monkeypatch.setenv('WEB_SEARCH_ENABLED', 'true')
    monkeypatch.setattr(agent, 'model_provider', models.get_model_provider())
    answers = iter(['/status', '/settings web on', '/settings web off', 'exit'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    agent.main()
    output = capsys.readouterr().out
    assert 'Provider: Ollama' in output and 'Model: llama3.2' in output
    assert 'Web search: unavailable' in output and 'Web search: enabled' not in output
    assert f"Ollama: {'connected' if connected else 'unavailable'}" in output
    assert 'Base URL: http://localhost:11434' in output
    assert 'Web search is not available with Ollama yet.' in output
    assert settings.COST_NOTICE not in output and 'private' not in output
    assert json.loads(settings.preferences_path().read_text())['web_search_enabled'] is False


def test_agent_loop(server, monkeypatch):
    server({'message': {'role': 'assistant', 'content': '{"action":"final","answer":"Local answer"}'}, 'done': True})
    assert agent.run_agent('hello', provider=models.OllamaProvider()) == 'Local answer'
