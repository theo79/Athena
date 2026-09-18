"""Provider configuration and substitution tests; all SDK calls are mocked."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import agent
import models
from test_model_errors import status_error, successful_response
from openai import RateLimitError, APITimeoutError
import httpx2


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    for name in ('MODEL_PROVIDER', 'MODEL_NAME', 'OPENROUTER_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(agent, 'model_provider', None)
    monkeypatch.setattr(models, 'OpenAI', Mock(side_effect=AssertionError('Unexpected SDK client')))


@pytest.mark.parametrize('name', [None, 'openrouter'])
def test_provider_selection(monkeypatch, name):
    if name is not None:
        monkeypatch.setenv('MODEL_PROVIDER', name)
    provider = models.get_model_provider()
    assert isinstance(provider, models.OpenRouterProvider)
    assert provider.model_name == 'openrouter/free'


def test_custom_model_and_success(monkeypatch):
    monkeypatch.setenv('MODEL_NAME', 'configured/model')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-only-key')
    create = Mock(return_value=successful_response('hello'))
    constructor = Mock(return_value=SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(models, 'OpenAI', constructor)
    provider = models.get_model_provider()
    messages = [{'role': 'user', 'content': 'test'}]
    assert provider.generate(messages) == 'hello'
    create.assert_called_once_with(model='configured/model', messages=messages)
    assert constructor.call_args.kwargs['max_retries'] == 0
    assert constructor.call_args.kwargs['api_key'] == 'test-only-key'


def test_unsupported_provider(monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER', 'xyz')
    with pytest.raises(models.ModelConfigurationError, match='Unsupported model provider: xyz'):
        models.get_model_provider()
    assert agent.run_agent('test') == 'Unsupported model provider: xyz'
    models.OpenAI.assert_not_called()


@pytest.mark.parametrize('key', [None, '', '   '])
def test_missing_key(monkeypatch, key):
    if key is not None:
        monkeypatch.setenv('OPENROUTER_API_KEY', key)
    result = models.get_model_provider().generate([])
    assert isinstance(result, models.ModelFailure)
    assert 'OPENROUTER_API_KEY is not configured' in result.message
    models.OpenAI.assert_not_called()


@pytest.mark.parametrize('error,expected', [
    (status_error(RateLimitError, 429), 'rate limit'),
    (APITimeoutError(request=httpx2.Request('POST', 'https://example.test')), 'timed out'),
])
def test_provider_failure(error, expected):
    provider = models.OpenRouterProvider()
    create = Mock(side_effect=error)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = provider.generate([])
    assert isinstance(result, models.ModelFailure)
    assert expected in result.message
    create.assert_called_once()


def test_agent_has_no_sdk_dependency():
    source = Path(agent.__file__).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not node.module.startswith('openai')
        elif isinstance(node, ast.Import):
            assert all(not name.name.startswith('openai') for name in node.names)
    assert 'OpenRouter' not in source and 'openrouter' not in source
    assert 'APIStatusError' not in source and 'RateLimitError' not in source


def test_fake_provider_without_configuration(monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER', 'unsupported-on-purpose')
    class FakeProvider:
        def generate(self, messages):
            assert messages[-1] == {'role': 'user', 'content': 'test'}
            return '{"action":"final","answer":"hello"}'
    assert agent.run_agent('test', provider=FakeProvider()) == 'hello'
    models.OpenAI.assert_not_called()


def test_cached_provider_is_reused(monkeypatch):
    fake = Mock()
    fake.generate.return_value = '{"action":"final","answer":"hello"}'
    factory = Mock(return_value=fake)
    monkeypatch.setattr(agent, 'get_model_provider', factory)
    assert agent.run_agent('one') == 'hello'
    assert agent.run_agent('two') == 'hello'
    factory.assert_called_once()
    assert fake.generate.call_count == 2
