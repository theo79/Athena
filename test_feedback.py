import json
from unittest.mock import Mock
import pytest
import agent
import experience as exp
from conversation import ConversationSession
from test_conversation import FakeProvider, final
from test_retrieval import record, seed


def test_defaults_and_old_records():
    new = exp.Experience('task').record
    assert new['feedback'] is None and new['reward'] == 0
    seed([record('old', 'weather')])
    old, = exp.load_experiences()
    assert old.get('feedback') is None and old.get('reward', 0) == 0
    assert exp.find_relevant_experiences('weather')[0]['reward'] == 0


@pytest.mark.parametrize('feedback,reward', [('positive', 1), ('negative', -1), ('neutral', 0)])
def test_exact_id_and_reload(feedback, reward):
    other = record('other', 'same task')
    seed([other, record('target', 'same task')])
    assert exp.set_experience_feedback('target', feedback)
    records = json.loads(exp.EXPERIENCES_FILE.read_text())
    assert records[0] == other
    assert records[1]['feedback'] == feedback and records[1]['reward'] == reward
    assert records[1]['quality'] == exp.FEEDBACK_QUALITY[feedback]


@pytest.mark.parametrize('feedback', ['bad', '', None, [], 1])
def test_invalid_feedback(feedback):
    seed([record('one', 'task')])
    before = exp.EXPERIENCES_FILE.read_bytes()
    assert not exp.set_experience_feedback('one', feedback)
    assert exp.EXPERIENCES_FILE.read_bytes() == before


def test_missing_duplicate_and_storage_failure(monkeypatch):
    assert not exp.set_experience_feedback('absent', 'positive')
    assert not exp.EXPERIENCES_FILE.exists()
    seed([record('one', 'task'), record('one', 'task')])
    assert not exp.set_experience_feedback('one', 'positive')
    seed([record('one', 'task')])
    before = exp.EXPERIENCES_FILE.read_bytes()
    monkeypatch.setattr(exp, '_write_experiences', Mock(side_effect=PermissionError('private')))
    assert not exp.set_experience_feedback('one', 'positive')
    assert exp.EXPERIENCES_FILE.read_bytes() == before


def test_ranking():
    seed([dict(record('negative', 'weather sensors'), feedback='negative', reward=-1),
          record('neutral', 'weather sensors'),
          dict(record('positive', 'weather sensors'), feedback='positive', reward=1)])
    matches = exp.find_relevant_experiences('weather sensors')
    assert [m['id'] for m in matches] == ['positive']
    context = exp.build_experience_context(matches)
    assert 'positive_example' in context and 'user_validated' in context
    assert '"score"' not in context
    seed([dict(record('unrelated', 'baking bread'), feedback='positive', reward=1),
          dict(record('less', 'weather'), feedback='positive', reward=1),
          dict(record('more', 'weather sensors'), feedback='negative', reward=-1)])
    assert [m['id'] for m in exp.find_relevant_experiences('weather sensors')] == ['more', 'less']


@pytest.mark.parametrize('command,feedback', [('/good', 'positive'), ('/bad', 'negative'), ('/neutral', 'neutral')])
def test_cli_no_model_no_history_no_extra_experience(monkeypatch, capsys, command, feedback):
    session = ConversationSession()
    monkeypatch.setattr(agent, 'ConversationSession', lambda: session)
    provider = FakeProvider([final('done')])
    monkeypatch.setattr(agent, 'model_provider', provider)
    prompts = iter(['task', command, '/history', 'exit'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(prompts))
    agent.main()
    stored, = exp.load_experiences()
    assert stored['feedback'] == feedback
    assert stored['id'] == session.last_experience_id
    assert len(provider.requests) == 1 and len(session.recent_messages()) == 2
    assert command not in json.dumps(session.recent_messages())
    output = capsys.readouterr().out
    assert f'Feedback saved: {feedback}.' in output
    assert 'Task: task' in output
    assert stored['id'] not in output and 'Reward:' not in output


def test_fresh_session_no_target(monkeypatch, capsys):
    seed([record('old', 'past task')])
    provider = FakeProvider([])
    monkeypatch.setattr(agent, 'model_provider', provider)
    prompts = iter(['/good', '/bad', 'exit'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(prompts))
    agent.main()
    assert 'No recent task is available' in capsys.readouterr().out
    assert len(exp.load_experiences()) == 1 and not provider.requests


def test_failed_save_clears_previous_target(monkeypatch):
    session = ConversationSession()
    agent.run_agent('one', FakeProvider([final('done')]), session)
    assert session.last_experience_id
    monkeypatch.setattr(exp, 'save_experience', Mock(side_effect=OSError()))
    agent.run_agent('two', FakeProvider([final('done')]), session)
    assert session.last_experience_id is None
    assert session.last_experience_task is None


def test_rating_replaces_not_accumulates():
    seed([record('one', 'task')])
    for feedback in ['positive', 'positive', 'negative', 'neutral']:
        assert exp.set_experience_feedback('one', feedback)
        assert exp.load_experiences()[0]['reward'] == exp.FEEDBACK_REWARDS[feedback]
        assert exp.load_experiences()[0]['quality'] == exp.FEEDBACK_QUALITY[feedback]


def test_clear_removes_feedback_target(monkeypatch, capsys):
    provider = FakeProvider([final('done')])
    monkeypatch.setattr(agent, 'model_provider', provider)
    prompts = iter(['Read notes.txt', '/clear', '/good', 'exit'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(prompts))
    agent.main()
    stored, = exp.load_experiences()
    assert stored['feedback'] is None and stored['quality'] == 'unverified'
    assert 'No recent task is available' in capsys.readouterr().out
    assert len(provider.requests) == 1


def test_feedback_task_confirmation_redacted_and_bounded(monkeypatch, capsys):
    monkeypatch.setenv('TEST_API_KEY', 'private-known-key')
    provider = FakeProvider([final('done')])
    monkeypatch.setattr(agent, 'model_provider', provider)
    prompts = iter(['Read private-known-key\n' + 'notes ' * 100, '/good', 'exit'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(prompts))
    agent.main()
    output = capsys.readouterr().out
    task_line, = [line for line in output.splitlines() if line.startswith('Task: ')]
    assert len(task_line) <= 126 and 'private-known-key' not in output
