"""Offline Gemini adapter tests. No credentials or live quota are used."""
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

import httpx
import pytest
from google.genai import errors

import agent
import models


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('MODEL_NAME', raising=False)
    monkeypatch.setenv('MODEL_PROVIDER', 'gemini')
    monkeypatch.setattr(agent, 'model_provider', None)
    monkeypatch.setattr(models.genai, 'Client', Mock(side_effect=AssertionError('Unexpected SDK call')))
    monkeypatch.setattr(models, 'OpenAI', Mock(side_effect=AssertionError('No fallback allowed')))


def client(monkeypatch, text='hello', error=None):
    generate = Mock(side_effect=error, return_value=SimpleNamespace(text=text))
    constructor = Mock(return_value=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(models.genai, 'Client', constructor)
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-secret-for-tests')
    return constructor, generate


def test_factory():
    provider = models.get_model_provider()
    assert isinstance(provider, models.GeminiProvider)
    assert provider.model_name == 'gemini-3.1-flash-lite'


@pytest.mark.parametrize('key', [None, '', ' '])
def test_missing_key(monkeypatch, key):
    if key is not None:
        monkeypatch.setenv('GEMINI_API_KEY', key)
    result = models.get_model_provider().generate([])
    assert isinstance(result, models.ModelFailure)
    assert 'GEMINI_API_KEY is not configured' in result.message
    models.genai.Client.assert_not_called()


def test_conversion_and_custom_model(monkeypatch):
    constructor, generate = client(monkeypatch)
    monkeypatch.setenv('MODEL_NAME', 'custom-gemini-model')
    messages = [{'role': 'system', 'content': 'instructions'},
                {'role': 'user', 'content': 'question'},
                {'role': 'assistant', 'content': 'tool call'},
                {'role': 'user', 'content': 'Tool result: example'}]
    assert models.get_model_provider().generate(messages) == 'hello'
    kwargs = generate.call_args.kwargs
    assert kwargs['model'] == 'custom-gemini-model'
    assert [(c.role, c.parts[0].text) for c in kwargs['contents']] == [
        ('user', 'question'), ('model', 'tool call'), ('user', 'Tool result: example')]
    assert kwargs['config'].system_instruction == 'instructions'
    assert kwargs['config'].automatic_function_calling.disable is True
    options = constructor.call_args.kwargs['http_options']
    assert options.retry_options.attempts == 1 and options.timeout == 30000
    assert constructor.call_args.kwargs['vertexai'] is False
    assert messages[2]['role'] == 'assistant'


@pytest.mark.parametrize('code,expected', [(429, 'rate limit'), (401, 'authentication'),
    (403, 'authentication'), (408, 'timed out'), (504, 'timed out'), (500, 'API error: 500'),
    (400, 'API error: 400'), (404, 'API error: 404')])
def test_api_errors(monkeypatch, capsys, code, expected):
    _, generate = client(monkeypatch, error=errors.APIError(code, {'error': {'message': 'sensitive-response'}}))
    result = agent.run_agent('hello')
    assert expected in result
    assert 'sensitive-response' not in result + capsys.readouterr().out
    generate.assert_called_once()
    models.OpenAI.assert_not_called()


@pytest.mark.parametrize('error,expected', [(httpx.ConnectError('private'), 'connect'),
    (httpx.ReadTimeout('private'), 'timed out')])
def test_transport_errors(monkeypatch, error, expected):
    client(monkeypatch, error=error)
    failure = models.get_model_provider().generate([])
    assert isinstance(failure, models.ModelFailure)
    assert expected in failure.message and 'private' not in failure.message


def test_empty_response(monkeypatch):
    client(monkeypatch, text=None)
    assert 'no text' in models.get_model_provider().generate([]).message


def test_fake_provider_uses_agent_tools(monkeypatch):
    class FakeGemini:
        def __init__(self):
            self.calls = 0
        def generate(self, messages):
            self.calls += 1
            if self.calls == 1:
                return '{"action":"tool","tool":"get_current_time","args":{}}'
            assert messages[-1]['content'].startswith('Tool result: ')
            return '{"action":"final","answer":"done"}'
    fake = FakeGemini()
    assert agent.run_agent('time?', provider=fake) == 'done'
    assert fake.calls == 2
    models.genai.Client.assert_not_called()


def test_no_sdk_in_agent():
    source = Path(agent.__file__).read_text()
    assert 'gemini' not in source.lower()
    assert 'genai' not in source


def test_startup_visibility(monkeypatch, capsys):
    monkeypatch.setattr('builtins.input', lambda prompt: 'exit')
    agent.main()
    output = capsys.readouterr().out
    assert output == 'Athena v0.11\nReady.\n\n'
    assert 'API_KEY' not in output


def test_programming_error_visible(monkeypatch):
    client(monkeypatch, error=TypeError('bug'))
    with pytest.raises(TypeError, match='bug'):
        models.get_model_provider().generate([])
