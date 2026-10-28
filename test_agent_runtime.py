"""Offline contracts for the extracted synchronous runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

import agent
import agent_runtime
import experience
import tools
from conversation import ConversationSession
from models import ModelFailure
from test_conversation import FakeProvider, final


@pytest.mark.parametrize('construct', [False, True])
def test_fresh_import_has_no_host_side_effects(tmp_path, construct):
    script = r'''
import builtins
import os
from pathlib import Path
import sys

def forbidden(*args, **kwargs):
    raise AssertionError("Host operation during runtime import")

def audit(event, args):
    if event == "open":
        path, mode, flags = args
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            forbidden()
        if isinstance(path, (str, bytes)):
            name = os.fsdecode(path).replace("\\", "/").rsplit("/", 1)[-1].lower()
            if name.startswith((".env", "memory.json", "experiences.json")) or name == "config.json":
                forbidden()
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "builtins.input",
                 "os.mkdir", "os.remove", "os.rename", "subprocess.Popen"}:
        forbidden()

sys.dont_write_bytecode = True
sys.addaudithook(audit)
builtins.input = forbidden
import runtime_paths
import settings
runtime_paths.load_configuration = forbidden
settings.ensure_configuration = forbidden
before = dict(os.environ)
import agent_runtime
assert dict(os.environ) == before
assert "agent" not in sys.modules
print("safe")
'''
    if construct:
        script = script.replace('assert dict(os.environ) == before',
                                'agent_runtime.get_model_provider = forbidden\n'
                                'runtime = agent_runtime.AgentRuntime()\n'
                                'assert runtime.provider is None\n'
                                'assert dict(os.environ) == before')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', LOCALAPPDATA=str(tmp_path))
    result = subprocess.run([sys.executable, '-c', script],
                            cwd=Path(agent_runtime.__file__).parent,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'safe'
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('responses', [
    [final('done')],
    ['invalid', final('recovered')],
    ['<tool_call>get_current_time</tool_call>', final('done')],
    ['invalid'] * 5,
    [ModelFailure('unavailable')],
])
def test_headless_matches_legacy(responses, tmp_path, monkeypatch):
    observed = []
    for index, entry in enumerate((agent.run_agent, agent_runtime.run_agent)):
        monkeypatch.setattr(experience, 'EXPERIENCES_FILE', tmp_path / f'{index}.json')
        provider = FakeProvider(responses)
        session = ConversationSession()
        answer = entry('task', provider, session)
        record, = experience.load_experiences()
        observed.append((answer, provider.requests, session.recent_messages(),
                         record['status'], record['steps'], record['parse_failures'],
                         bool(session.last_experience_id)))
    assert observed[0] == observed[1]


@pytest.fixture(autouse=True)
def fixed_time_tool(monkeypatch):
    spec = dict(agent.TOOLS['get_current_time'], function=lambda: 'fixed time')
    monkeypatch.setitem(agent.TOOLS, 'get_current_time', spec)


@pytest.mark.parametrize('approved', [None, False, True])
def test_headless_memory_permission_and_persistence(tmp_path, monkeypatch, approved):
    monkeypatch.setattr(tools, 'MEMORY_FILE', tmp_path / 'memory.json')
    callback = None if approved is None else Mock(return_value=approved)
    provider = FakeProvider([
        json.dumps({'action': 'tool', 'tool': 'save_memory', 'args': {'text': 'synthetic fact'}}),
        final('handled'),
    ])
    assert agent_runtime.run_agent('Remember a fact', provider, confirmation=callback) == 'handled'
    assert tools.MEMORY_FILE.exists() is (approved is True)
    record, = experience.load_experiences()
    assert record['tools_used'][0]['success'] is (approved is True)
    if callback is not None:
        callback.assert_called_once()


def test_headless_provider_factory_is_lazy_and_per_task(monkeypatch):
    provider = FakeProvider(['invalid', final('done')])
    provider.provider_name, provider.model_name = 'fake', 'offline'
    factory = Mock(return_value=provider)
    monkeypatch.setattr(agent_runtime, 'get_model_provider', factory)
    assert agent_runtime.run_agent('task') == 'done'
    factory.assert_called_once_with()
    record, = experience.load_experiences()
    assert record['provider'] == 'fake' and record['model'] == 'offline'


def test_headless_exception_still_persists_and_propagates():
    provider = Mock()
    provider.generate.side_effect = TypeError('synthetic programming error')
    with pytest.raises(TypeError, match='synthetic programming error'):
        agent_runtime.run_agent('task', provider)
    record, = experience.load_experiences()
    assert record['status'] == 'internal_error'


def test_prompt_and_cli_entry_are_preserved():
    assert agent.SYSTEM_PROMPT == agent_runtime.SYSTEM_PROMPT
    assert agent.build_system_prompt() == agent_runtime.build_system_prompt()
    assert callable(agent.main)


def test_legacy_hooks_resolved_per_call(monkeypatch):
    model = Mock(return_value=final('done'))
    parser = Mock(wraps=agent_runtime.parse_model_response)
    monkeypatch.setattr(agent, 'call_model', model)
    monkeypatch.setattr(agent, 'parse_model_response', parser)
    assert agent.run_agent('task') == 'done'
    model.assert_called_once()
    parser.assert_called_once()


@pytest.fixture
def experience_service():
    """Use real record/format logic with persistence confined to a Python list."""
    from copy import deepcopy
    from types import SimpleNamespace

    def make_service():
        records = []

        class Record(experience.Experience):
            def persist(self):
                records.append(deepcopy(self.record))
                return self.record['id']

        return SimpleNamespace(
            Experience=Record, observe=experience.observe, safe_text=experience.safe_text,
            find_relevant_experiences=Mock(return_value=[]),
            build_experience_context=experience.build_experience_context, records=records)

    return make_service


def test_instances_have_independent_providers_and_histories(experience_service):
    left_provider = FakeProvider([final('left one'), final('left two')])
    right_provider = FakeProvider([final('right one')])
    left_service, right_service = experience_service(), experience_service()
    left = agent_runtime.AgentRuntime(left_provider, experience_service=left_service)
    right = agent_runtime.AgentRuntime(right_provider, experience_service=right_service)
    assert left.run_agent('left first') == 'left one'
    assert right.run_agent('right first') == 'right one'
    assert left.run_agent('left second') == 'left two'
    assert left.conversation is not right.conversation
    assert len(left.conversation.recent_messages()) == 4
    assert len(right.conversation.recent_messages()) == 2
    assert left_provider.requests[1][1:3] == [
        {'role': 'user', 'content': 'left first'},
        {'role': 'assistant', 'content': 'left one'}]
    assert 'left first' not in json.dumps(right_provider.requests)
    assert len(left_service.records) == 2 and len(right_service.records) == 1
    assert left.conversation.last_experience_id == left_service.records[-1]['id']
    assert right.conversation.last_experience_id == right_service.records[0]['id']


def test_instance_provider_caches_are_lazy_and_independent(monkeypatch, experience_service):
    first = FakeProvider([final('one'), final('two')])
    second = FakeProvider([final('other')])
    factory = Mock(side_effect=[first, second])
    monkeypatch.setattr(agent_runtime, 'get_model_provider', factory)
    left = agent_runtime.AgentRuntime(experience_service=experience_service())
    right = agent_runtime.AgentRuntime(experience_service=experience_service())
    factory.assert_not_called()
    assert left.run_agent('first') == 'one'
    assert left.run_agent('again') == 'two'
    assert right.run_agent('other') == 'other'
    assert factory.call_count == 2
    assert left.provider is first and right.provider is second


def test_registry_snapshot_is_deep_but_handlers_keep_identity(experience_service):
    from tool_modules.results import ToolResult

    handler = Mock(return_value=ToolResult.ok('original'))
    registry = {'example': {
        'description': 'Original description', 'function': handler,
        'permission': 'READ_ONLY', 'requires_confirmation': False,
        'parameters': {'text': {'type': str, 'required': True, 'max_length': 10}},
    }}
    left = agent_runtime.AgentRuntime(registry=registry, experience_service=experience_service())
    right = agent_runtime.AgentRuntime(registry=registry, experience_service=experience_service())
    registry['example']['parameters']['text']['max_length'] = 1
    registry['example']['description'] = 'Changed source description'
    registry['example']['permission'] = 'SENSITIVE_ACTION'
    registry['example']['function'] = Mock(side_effect=AssertionError('source handler'))
    registry['new'] = registry['example']
    assert set(left.registry) == {'example'}
    assert left.registry['example']['function'] is handler
    assert left.execute_tool('example', {'text': 'hello'}).success
    assert 'Original description' in left.system_prompt
    assert 'Changed source description' not in left.system_prompt
    left.registry['example']['parameters']['text']['max_length'] = 2
    left.registry['example']['description'] = 'Instance description'
    assert not left.execute_tool('example', {'text': 'hello'}).success
    assert right.execute_tool('example', {'text': 'hello'}).success
    assert right.registry['example']['parameters']['text']['max_length'] == 10
    assert 'Instance description' not in right.system_prompt


def test_instance_prompt_and_dispatch_use_same_snapshot(monkeypatch, experience_service):
    provider = FakeProvider(['<tool_call>get_current_time</tool_call>', final('done')])
    runtime = agent_runtime.AgentRuntime(provider, experience_service=experience_service())
    monkeypatch.delitem(agent.TOOLS, 'get_current_time')
    assert runtime.run_agent('time?') == 'done'
    assert provider.requests[0][0]['content'] == runtime.system_prompt
    assert '- get_current_time:' in runtime.system_prompt
    assert provider.requests[1][-1]['content'] == 'Tool result: fixed time'
    runtime.registry['get_current_time']['description'] = 'Updated instance description'
    assert 'Updated instance description' in runtime.system_prompt


@pytest.mark.parametrize('permission', ['READ_ONLY', 'EXTERNAL_READ', 'WRITE_LOCAL',
                                      'EXTERNAL_WRITE', 'SENSITIVE_ACTION', 'UNKNOWN'])
@pytest.mark.parametrize('approval', [None, False, True, 1, 'yes'])
def test_instance_dispatch_permission_matrix(permission, approval, experience_service):
    from tool_modules.results import ToolResult

    handler = Mock(return_value=ToolResult.ok('executed'))
    callback = None if approval is None else Mock(return_value=approval)
    registry = {'example': {
        'description': 'Synthetic action', 'parameters': {}, 'function': handler,
        'permission': permission, 'requires_confirmation': False}}
    service = experience_service()
    runtime = agent_runtime.AgentRuntime(
        FakeProvider(['<tool_call>example</tool_call>', final('done')]),
        registry=registry, confirmation=callback, experience_service=service)
    assert runtime.run_agent('task') == 'done'
    allowed = (permission in {'READ_ONLY', 'EXTERNAL_READ'} or
               (permission in {'WRITE_LOCAL', 'EXTERNAL_WRITE', 'SENSITIVE_ACTION'}
                and approval is True))
    assert handler.call_count == int(allowed)
    assert service.records[0]['tools_used'][0]['success'] is allowed


@pytest.mark.parametrize('args', [{}, {'text': 1}, {'text': ' '}, {'text': 'too long'},
                                 {'text': 'ok', 'approved': True}])
def test_instance_argument_validation_precedes_approval(args, experience_service):
    handler, callback = Mock(), Mock(return_value=True)
    runtime = agent_runtime.AgentRuntime(registry={'example': {
        'description': 'Synthetic write', 'permission': 'WRITE_LOCAL', 'function': handler,
        'parameters': {'text': {'type': str, 'required': True, 'max_length': 3}},
    }}, confirmation=callback, experience_service=experience_service())
    assert runtime.execute_tool('example', args).error_type == 'invalid_call'
    handler.assert_not_called()
    callback.assert_not_called()


def test_instance_confirmation_is_per_instance_and_call(experience_service):
    from tool_modules.results import ToolResult

    handler = Mock(return_value=ToolResult.ok('executed'))
    registry = {'example': {'description': 'Synthetic write', 'permission': 'WRITE_LOCAL',
                            'parameters': {}, 'function': handler}}
    callback = Mock(side_effect=[True, False])
    left = agent_runtime.AgentRuntime(registry=registry, confirmation=callback,
                                     experience_service=experience_service())
    right = agent_runtime.AgentRuntime(registry=registry, experience_service=experience_service())
    assert left.execute_tool('example', {}).success
    assert not left.execute_tool('example', {}).success
    assert right.execute_tool('example', {}).error_type == 'approval_required'
    handler.assert_called_once()


def test_injected_experience_service_owns_retrieval_and_persistence(monkeypatch, experience_service):
    service = experience_service()
    service.find_relevant_experiences.return_value = [{'task': 'synthetic history'}]
    service.build_experience_context = Mock(return_value='synthetic context')
    monkeypatch.setattr(experience, 'find_relevant_experiences',
                        Mock(side_effect=AssertionError('global retrieval')))
    monkeypatch.setattr(experience, 'save_experience',
                        Mock(side_effect=AssertionError('global persistence')))
    provider = FakeProvider([final('done')])
    session = ConversationSession()
    runtime = agent_runtime.AgentRuntime(provider, session, experience_service=service)
    assert runtime.conversation is session
    assert runtime.run_agent('task') == 'done'
    service.find_relevant_experiences.assert_called_once_with('task')
    service.build_experience_context.assert_called_once_with([{'task': 'synthetic history'}])
    assert provider.requests[0][1] == {'role': 'user', 'content': 'synthetic context'}
    assert service.records[0]['status'] == 'completed'
    experience.find_relevant_experiences.assert_not_called()
    experience.save_experience.assert_not_called()
    assert not experience.EXPERIENCES_FILE.exists()


def test_injected_persistence_failure_remains_best_effort(experience_service):
    service = experience_service()
    record_class = service.Experience
    record = record_class('task')
    record.persist = Mock(side_effect=OSError('synthetic failure'))
    service.Experience = lambda task: record
    runtime = agent_runtime.AgentRuntime(FakeProvider([final('done')]), experience_service=service)
    assert runtime.run_agent('task') == 'done'
    assert runtime.conversation.last_experience_id is None
    assert len(runtime.conversation.recent_messages()) == 2


def test_explicit_empty_registry_is_not_replaced(experience_service):
    runtime = agent_runtime.AgentRuntime(registry={}, experience_service=experience_service())
    assert runtime.registry == {}
    assert '- get_current_time:' not in runtime.system_prompt
    assert runtime.execute_tool('get_current_time', {}).error_type == 'invalid_call'
