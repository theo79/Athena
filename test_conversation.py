import json
from unittest.mock import Mock
import pytest
import agent
import experience
import tools
from conversation import ConversationSession
from models import ModelFailure


class FakeProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
    def generate(self, messages):
        self.requests.append([dict(message) for message in messages])
        return next(self.responses)


def final(answer):
    return json.dumps({'action': 'final', 'answer': answer})


def test_multiturn_and_experiences():
    session = ConversationSession()
    provider = FakeProvider([final('Noted'), final('ESP32')])
    assert agent.run_agent('I use ESP32', provider, session) == 'Noted'
    assert agent.run_agent('Which device?', provider, session) == 'ESP32'
    assert provider.requests[1] == [
        {'role': 'system', 'content': agent.SYSTEM_PROMPT},
        {'role': 'user', 'content': 'I use ESP32'},
        {'role': 'assistant', 'content': 'Noted'},
        {'role': 'user', 'content': 'Which device?'}]
    assert len(session.recent_messages()) == 4
    assert all(m['role'] != 'system' for m in session.recent_messages())
    records = experience.load_experiences()
    assert len(records) == 2 and records[1]['task'] == 'Which device?'
    assert 'I use ESP32' not in json.dumps(records[1])


def test_tools_not_retained(monkeypatch):
    session = ConversationSession()
    provider = FakeProvider(['{"action":"tool","tool":"get_current_time"}', final('done')])
    monkeypatch.setattr(agent, 'execute_tool', Mock(return_value='raw tool output' * 10000))
    agent.run_agent('time?', provider, session)
    assert session.recent_messages() == [
        {'role': 'user', 'content': 'time?'}, {'role': 'assistant', 'content': 'done'}]


def test_new_session_and_limit():
    session = ConversationSession()
    for i in range(12):
        session.append_turn(f'user {i}', f'answer {i}')
    messages = session.recent_messages()
    assert len(messages) == 20 and messages[0]['content'] == 'user 2'
    assert ConversationSession().recent_messages() == []
    messages[0]['content'] = 'mutated'
    messages.append({'role': 'system', 'content': 'bad'})
    assert session.recent_messages()[0]['content'] == 'user 2'


@pytest.mark.parametrize('response', [ModelFailure('Unavailable'), 'malformed',
    '{"action":"tool","tool":"unknown"}'])
def test_failed_turn_not_stored(response):
    session = ConversationSession()
    session.append_turn('earlier', 'answer')
    agent.run_agent('failed', FakeProvider([response] * 5), session)
    assert len(session.recent_messages()) == 2


def test_without_session():
    assert agent.run_agent('test', FakeProvider([final('hello')])) == 'hello'


def test_cli_commands_and_storage(monkeypatch, tmp_path, capsys):
    memory = tmp_path / 'memory.json'
    memory.write_text('[{"text":"existing"}]')
    monkeypatch.setattr(tools, 'MEMORY_FILE', memory)
    provider = FakeProvider([final('first answer'), final('second answer')])
    monkeypatch.setattr(agent, 'model_provider', provider)
    prompts = iter(['first task', '/history', '/clear', '/history', 'second task', 'exit'])
    experience_snapshot = None
    def input_command(prompt):
        nonlocal experience_snapshot
        command = next(prompts)
        if command == '/clear':
            experience_snapshot = experience.EXPERIENCES_FILE.read_bytes()
        if command == 'second task':
            assert experience.EXPERIENCES_FILE.read_bytes() == experience_snapshot
            assert memory.read_text() == '[{"text":"existing"}]'
        return command
    monkeypatch.setattr('builtins.input', input_command)
    agent.main()
    output = capsys.readouterr().out
    assert 'user: first task' in output and 'assistant: first answer' in output
    assert 'Conversation history is empty.' in output
    assert len(provider.requests) == 2
    assert len(provider.requests[1]) == 2
    assert len(experience.load_experiences()) == 2


def test_display_redaction(monkeypatch):
    monkeypatch.setenv('EXAMPLE_API_KEY', 'test-secret-value')
    session = ConversationSession()
    session.append_turn('test-secret-value', 'x' * 1000)
    assert 'test-secret-value' not in session.display()
    assert '[truncated]' in session.display()


@pytest.mark.parametrize('limit', [0, 1, 3, True, '20'])
def test_invalid_limits(limit):
    with pytest.raises(ValueError):
        ConversationSession(limit)
