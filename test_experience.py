import json
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest
import agent
import experience
from models import ModelFailure
from test_agent import scripted_model


def records():
    return json.loads(experience.EXPERIENCES_FILE.read_text(encoding='utf-8'))


def test_missing_store():
    assert experience.load_experiences() == []
    assert records() == []


@pytest.mark.parametrize('content', ['', '{broken', '{}', 'null'])
def test_invalid_store(content):
    experience.EXPERIENCES_FILE.write_text(content, encoding='utf-8')
    assert experience.load_experiences() == []
    experience.save_experience({'id': 'test'})
    assert records() == [{'id': 'test'}]


def test_success_metadata_and_unique_records():
    class Fake:
        provider_name = 'fake'
        model_name = 'test-model'
        def generate(self, messages):
            return '{"action":"final","answer":"hello"}'
    for task in ['one', 'two']:
        assert agent.run_agent(task, provider=Fake()) == 'hello'
    first, second = records()
    assert first['status'] == 'completed' and first['steps'] == 1
    assert first['quality'] == 'unverified'
    assert first['lesson'] is None and first['lesson_status'] is None
    assert first['provider'] == 'fake' and first['model'] == 'test-model'
    assert first['final_answer'] == 'hello'
    assert first['error_type'] is None
    assert first['timestamp']
    assert UUID(first['id']) != UUID(second['id'])


def test_tool_recovery(monkeypatch):
    scripted_model(monkeypatch, [
        '{"action":"tool","tool":"read_text_file","args":{"path":".env"}}',
        '{"action":"final","answer":"Access refused"}'])
    assert agent.run_agent('Read my .env file') == 'Access refused'
    record, = records()
    assert record['status'] == 'completed' and record['steps'] == 2
    event, = record['tools_used']
    assert event['success'] is False and event['permission'] == 'READ_ONLY'
    assert event['args'] == {'path': '.env'} and event['step'] == 1
    assert event['result_summary'] == 'Access denied'


def test_model_failure(monkeypatch):
    scripted_model(monkeypatch, [ModelFailure('Model provider rate limit reached.')])
    agent.run_agent('test')
    record, = records()
    assert record['status'] == 'model_failure' and record['steps'] == 1
    assert record['error_type'] == 'model_provider_error'


def test_max_steps(monkeypatch):
    scripted_model(monkeypatch, ['{"action":"tool","tool":"get_current_time"}'] * 5)
    assert agent.run_agent('test') == 'Maximum number of steps reached.'
    record, = records()
    assert record['status'] == 'max_steps' and record['budget_exhausted']
    assert record['steps'] == 5 and len(record['tools_used']) == 5


def test_parse_failure(monkeypatch):
    scripted_model(monkeypatch, ['bad response'] * 5)
    agent.run_agent('test')
    record, = records()
    assert record['status'] == 'parse_failure' and record['parse_failures'] == 5
    assert 'bad response' not in json.dumps(record)


def test_tool_failure_terminal(monkeypatch):
    scripted_model(monkeypatch, ['{"action":"tool","tool":"unknown"}'] * 5)
    agent.run_agent('test')
    record, = records()
    assert record['status'] == 'tool_failure'
    assert all(not event['success'] for event in record['tools_used'])


def test_parse_recovery(monkeypatch):
    scripted_model(monkeypatch, ['bad', '{"action":"final","answer":"done"}'])
    agent.run_agent('test')
    record, = records()
    assert record['status'] == 'completed' and record['parse_failures'] == 1


def test_final_answer_limit(monkeypatch):
    scripted_model(monkeypatch, [json.dumps({'action': 'final', 'answer': 'a' * 6000})])
    assert len(agent.run_agent('test')) == 6000
    answer = records()[0]['final_answer']
    assert len(answer) <= 5000 and answer.endswith('[truncated]')


def test_large_tool_output_omitted(monkeypatch):
    monkeypatch.setattr(agent, 'execute_tool', Mock(return_value='private-file-content' * 10000))
    scripted_model(monkeypatch, ['{"action":"tool","tool":"read_text_file","args":{"path":"notes.txt"}}',
                                '{"action":"final","answer":"done"}'])
    agent.run_agent('read notes')
    output = experience.EXPERIENCES_FILE.read_text()
    assert 'private-file-content' not in output and len(output) < 2000
    assert records()[0]['tools_used'][0]['success']


def test_search_metadata_only(monkeypatch):
    monkeypatch.setattr(agent, 'execute_tool', Mock(return_value='[{"snippet":"private result"}]'))
    scripted_model(monkeypatch, ['{"action":"tool","tool":"web_search","args":{"query":"Python","max_results":1}}',
                                '{"action":"final","answer":"done"}'])
    agent.run_agent('search Python')
    event = records()[0]['tools_used'][0]
    assert event['args'] == {'query': 'Python', 'max_results': 1}
    assert event['result_summary'] == 'Returned 1 search results'
    assert 'private result' not in experience.EXPERIENCES_FILE.read_text()


@pytest.mark.parametrize('text', ['Authorization: Bearer top-secret', 'API_KEY=top-secret',
    'HOST=private-host\nUSER=private-person', 'password: top-secret'])
def test_sensitive_text_omitted(monkeypatch, text):
    scripted_model(monkeypatch, [json.dumps({'action': 'final', 'answer': text})])
    agent.run_agent(text)
    stored = experience.EXPERIENCES_FILE.read_text()
    assert 'top-secret' not in stored and 'private-host' not in stored
    assert 'private-person' not in stored


def test_environment_key_redacted(monkeypatch):
    monkeypatch.setenv('SAMPLE_API_KEY', 'unique-known-credential')
    scripted_model(monkeypatch, [json.dumps({'action': 'final', 'answer': 'unique-known-credential'})])
    agent.run_agent('Here is unique-known-credential')
    assert 'unique-known-credential' not in experience.EXPERIENCES_FILE.read_text()


def test_memory_text_omitted():
    run = experience.Experience('remember a fact')
    run.tool('save_memory', {'text': 'private fact'}, 'WRITE_LOCAL', 'Memory saved successfully.', 1)
    assert 'private fact' not in json.dumps(run.record)


def test_short_key_and_json_authorization(monkeypatch):
    monkeypatch.setenv('TEST_API_KEY', 'abc123')
    assert 'abc123' not in experience.safe_text('value abc123')
    assert 'private' not in experience.safe_text('{"Authorization": "private"}')


def test_telemetry_constructor_failure_nonfatal(monkeypatch):
    monkeypatch.setattr(experience, 'Experience', Mock(side_effect=RuntimeError('bug')))
    scripted_model(monkeypatch, ['{"action":"final","answer":"hello"}'])
    assert agent.run_agent('test') == 'hello'


@pytest.mark.parametrize('debug', [False, True])
def test_save_failure_nonfatal(monkeypatch, capsys, debug):
    import diagnostics
    monkeypatch.setattr(diagnostics, 'DEBUG', debug)
    monkeypatch.setattr(experience, 'save_experience', Mock(side_effect=OSError('sensitive path')))
    scripted_model(monkeypatch, ['{"action":"final","answer":"hello"}'])
    assert agent.run_agent('test') == 'hello'
    output = capsys.readouterr().out
    assert ('Warning: experience logging failed' in output) == debug
    assert 'sensitive path' not in output


def test_agent_bug_still_propagates(monkeypatch):
    monkeypatch.setattr(agent, 'call_model', Mock(side_effect=TypeError('private exception')))
    with pytest.raises(TypeError):
        agent.run_agent('test')
    record, = records()
    assert record['status'] == 'internal_error'
    assert 'private exception' not in json.dumps(record)
