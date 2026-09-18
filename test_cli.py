"""Public CLI output and opt-in diagnostics, using offline model responses."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

import agent
import diagnostics
import experience
from conversation import ConversationSession
from test_conversation import FakeProvider, final


@pytest.mark.parametrize('debug', [False, True])
def test_run_diagnostics_preserve_execution(monkeypatch, capsys, debug):
    monkeypatch.setattr(diagnostics, 'DEBUG', debug)
    provider = FakeProvider([
        'invalid response',
        '{"action":"tool","tool":"get_current_time"}',
        final('The time is available.'),
    ])
    session = ConversationSession()
    assert agent.run_agent('current time', provider, session) == 'The time is available.'
    stored, = experience.load_experiences()
    assert stored['steps'] == 3 and stored['parse_failures'] == 1
    assert stored['tools_used'][0]['success']
    assert stored['id'] == session.last_experience_id
    assert len(session.recent_messages()) == 2
    output = capsys.readouterr().out
    if debug:
        for label in ('Relevant experiences: 0', '--- Step 1 ---', 'Model response:',
                      'Normalized response:', 'Tool selected:', 'Arguments:',
                      'Permission:', 'Tool result:', 'Experience saved:'):
            assert label in output
    else:
        assert output == ''


def test_clean_chat_transcript(monkeypatch, capsys):
    provider = FakeProvider([final('A model-generated greeting.')])
    monkeypatch.setattr(agent, 'model_provider', provider)
    inputs = iter(['hello', 'exit'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(inputs))
    agent.main()
    assert capsys.readouterr().out == (
        'Athena v0.11\nReady.\n\n\nAthena > A model-generated greeting.\n\n')
    assert len(provider.requests) == 1


@pytest.mark.parametrize('debug', [False, True])
def test_help_status_are_local(monkeypatch, capsys, debug):
    monkeypatch.setattr(diagnostics, 'DEBUG', debug)
    provider = FakeProvider([])
    provider.provider_name = 'openrouter'
    provider.model_name = 'openrouter/free'
    monkeypatch.setattr(agent, 'model_provider', provider)
    session = ConversationSession()
    monkeypatch.setattr(agent, 'ConversationSession', lambda: session)
    inputs = iter(['/help', '/status', 'exit'])

    def read_input(prompt):
        command = next(inputs)
        if command == '/help':
            assert capsys.readouterr().out == 'Athena v0.11\nReady.\n\n'
        return command

    monkeypatch.setattr('builtins.input', read_input)
    agent.main()
    output = capsys.readouterr().out
    for command in ('/help', '/status', '/history', '/clear', '/good', '/bad', '/neutral', 'exit'):
        assert command in output
    assert ('Athena v0.11\nVersion: 0.11\nProvider: openrouter\nModel: openrouter/free\n'
            f'Debug: {str(debug).lower()}') in output
    assert 'exit - Exit Athena' in output
    assert not provider.requests and not session.recent_messages()
    assert not experience.EXPERIENCES_FILE.exists()


@pytest.mark.parametrize('value,expected', [(None, 'False'), ('false', 'False'),
                                          ('true', 'True'), ('TRUE', 'True')])
def test_debug_environment(value, expected):
    environment = dict(os.environ, PYTHON_DOTENV_DISABLED='1')
    environment.pop('MY_AGENT_DEBUG', None)
    if value is not None:
        environment['MY_AGENT_DEBUG'] = value
    result = subprocess.run(
        [sys.executable, '-c', 'import diagnostics; print(diagnostics.DEBUG)'],
        cwd=Path(agent.__file__).parent, env=environment,
        capture_output=True, text=True, check=True)
    assert result.stdout.strip() == expected


def test_identity_and_internal_protocol():
    prompt = agent.build_system_prompt()
    assert 'You are Athena, an AI work assistant.' in prompt
    assert 'Theocharis is the user/developer, not your name.' in prompt
    assert 'In user-facing answers' in prompt
    assert 'Always respond with one JSON object:' in prompt
