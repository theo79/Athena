"""Offline native-search requests, normalized results and agent boundary checks."""
import json
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import httpx
import pytest
from google.genai import errors
from openai import BadRequestError, AuthenticationError, APIConnectionError

import agent
import tools
import search_provider as provider
from test_agent import scripted_model


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('WEB_SEARCH_ENABLED', 'true')
    monkeypatch.setenv('MODEL_PROVIDER', 'openrouter')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'offline-secret-placeholder')
    monkeypatch.setenv('GEMINI_API_KEY', 'offline-secret-placeholder')
    monkeypatch.delenv('MODEL_NAME', raising=False)
    monkeypatch.setattr(provider, 'OpenAI', MagicMock(side_effect=AssertionError('Unexpected network')))
    monkeypatch.setattr(provider.genai, 'Client', MagicMock(side_effect=AssertionError('Unexpected network')))


def fake_client(monkeypatch, name, response=None, error=None):
    client = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = client
    if name == 'gemini':
        monkeypatch.setenv('MODEL_PROVIDER', 'gemini')
        monkeypatch.setattr(provider.genai, 'Client', factory)
        call = client.models.generate_content
    else:
        monkeypatch.setattr(provider, 'OpenAI', factory)
        call = client.chat.completions.create
    call.return_value = response
    call.side_effect = error
    return call, factory


def test_registry():
    spec = agent.TOOLS['web_search']
    assert spec['permission'] == agent.NETWORK_READ
    assert spec['function'] is tools.web_search
    assert 'untrusted external content' in agent.SYSTEM_PROMPT


@pytest.mark.parametrize('args', [{}, {'query': ''}, {'query': ' '}, {'query': 12},
    {'query': 'x' * 501}, {'query': 'x', 'max_results': 0},
    {'query': 'x', 'max_results': 11}, {'query': 'x', 'max_results': True},
    {'query': 'x', 'max_results': 1.5}, {'query': 'x', 'max_results': '5'},
    {'query': 'x', 'max_results': None}, {'query': 'x', 'provider_option': 'advanced'}])
def test_argument_validation(args):
    assert agent.execute_tool('web_search', args).startswith('Invalid arguments:')


def test_disabled_default_and_explicit(monkeypatch):
    for value in (None, 'false', 'invalid'):
        if value is None:
            monkeypatch.delenv('WEB_SEARCH_ENABLED', raising=False)
        else:
            monkeypatch.setenv('WEB_SEARCH_ENABLED', value)
        result = tools.web_search('python')
        assert not result.success
        assert result == 'Web search is disabled in Athena settings.'


@pytest.mark.parametrize('name', ['openrouter', 'gemini'])
def test_missing_key(monkeypatch, name):
    monkeypatch.setenv('MODEL_PROVIDER', name)
    monkeypatch.delenv(provider.settings.KEY_NAMES[name])
    assert tools.web_search('python') == provider.AUTH_ERROR


def test_openrouter_request_and_sources(monkeypatch):
    response = NS(choices=[NS(message=NS(content='Answer', annotations=[
        {'type': 'url_citation', 'url_citation': {'title': 'Python', 'url': 'https://python.org', 'content': 'unused'}}]))])
    call, factory = fake_client(monkeypatch, 'openrouter', response)
    result = json.loads(tools.web_search(' Python ', 2))
    assert result == {'answer': 'Answer', 'sources': [{'title': 'Python', 'url': 'https://python.org'}],
                      'provider': 'openrouter', 'success': True}
    args = call.call_args.kwargs
    assert args['model'] == 'openrouter/free'
    assert args['extra_body'] == {'plugins': [{'id': 'web', 'max_results': 2}]}
    assert args['messages'][0]['content'].endswith('Python')
    assert factory.call_args.kwargs['api_key'] == 'offline-secret-placeholder'


def test_gemini_request_and_sources(monkeypatch):
    response = NS(text='Answer', candidates=[NS(grounding_metadata=NS(grounding_chunks=[
        NS(web=NS(title='Python', uri='https://python.org'))]))])
    call, factory = fake_client(monkeypatch, 'gemini', response)
    result = json.loads(tools.web_search('Python'))
    assert result['provider'] == 'gemini' and result['sources'][0]['url'] == 'https://python.org'
    args = call.call_args.kwargs
    assert args['model'] == provider.settings.DEFAULT_MODELS['gemini']
    assert args['config'].tools[0].google_search is not None
    assert factory.call_args.kwargs['api_key'] == 'offline-secret-placeholder'


@pytest.mark.parametrize('name', ['openrouter', 'gemini'])
def test_missing_metadata_and_secret_redaction(monkeypatch, name):
    response = (NS(text='offline-secret-placeholder', candidates=[]) if name == 'gemini' else
                NS(choices=[NS(message=NS(content='offline-secret-placeholder'))]))
    fake_client(monkeypatch, name, response)
    result = json.loads(tools.web_search('python'))
    assert result['answer'] == '[REDACTED]' and result['sources'] == []


@pytest.mark.parametrize('code,detail,expected', [
    (401, 'private', provider.AUTH_ERROR), (403, 'private', provider.AUTH_ERROR),
    (400, 'API key not valid', provider.AUTH_ERROR),
    (400, 'Search grounding not supported', provider.UNSUPPORTED),
    (429, 'private', provider.API_ERROR), (500, 'private', provider.API_ERROR)])
def test_gemini_errors(monkeypatch, code, detail, expected):
    fake_client(monkeypatch, 'gemini', error=errors.ClientError(code, {'error': {'message': detail}}))
    assert tools.web_search('python') == expected


@pytest.mark.parametrize('code,detail,expected', [(401, 'private', provider.AUTH_ERROR),
    (400, 'Search not supported', provider.UNSUPPORTED), (400, 'private', provider.API_ERROR)])
def test_openrouter_errors(monkeypatch, code, detail, expected):
    response = httpx.Response(code, request=httpx.Request('POST', 'https://example.com'))
    cls = AuthenticationError if code == 401 else BadRequestError
    fake_client(monkeypatch, 'openrouter', error=cls(detail, response=response, body=None))
    assert tools.web_search('python') == expected


@pytest.mark.parametrize('name', ['openrouter', 'gemini'])
def test_empty_answer(monkeypatch, name):
    response = NS(text=None, candidates=[]) if name == 'gemini' else NS(choices=[])
    fake_client(monkeypatch, name, response)
    assert tools.web_search('python') == provider.API_ERROR


def test_bounded_sources():
    result = provider._normalize('x' * 20000, [
        {'url': 'javascript:alert(1)'}, {'url': 'https://one.example', 'title': 'x' * 500},
        {'url': 'https://one.example'}, {'uri': 'https://two.example'},
        {'url': 'https://three.example'}], 'gemini', 2)
    assert len(result['answer']) == 12000
    assert len(result['sources']) == 2 and len(result['sources'][0]['title']) == 200


def test_failure_reaches_agent(monkeypatch):
    monkeypatch.setenv('WEB_SEARCH_ENABLED', 'false')
    observed = scripted_model(monkeypatch, [
        '{"action":"tool","tool":"web_search","args":{"query":"python"}}',
        '{"action":"final","answer":"Search unavailable"}'])
    assert agent.run_agent('search python') == 'Search unavailable'
    assert 'disabled' in observed[1][-1]['content']


def test_network_failure(monkeypatch):
    fake_client(monkeypatch, 'openrouter', error=APIConnectionError(request=httpx.Request('POST', 'https://example.com')))
    assert tools.web_search('python') == provider.API_ERROR


def test_configuration_has_no_retired_search_service():
    from pathlib import Path
    retired = 'ta' + 'vily'
    for name in ('search_provider.py', '.env.example', 'requirements.txt', 'pyproject.toml'):
        assert retired not in Path(name).read_text(encoding='utf-8').lower()
