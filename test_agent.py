import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import agent
import tools


@pytest.mark.parametrize('raw, expected', [
    ('{"action":"tool","tool":"save_memory","args":{"text":"Orion"}}',
     {'action': 'tool', 'tool': 'save_memory', 'args': {'text': 'Orion'}}),
    ('{"action":"final","answer":"Done"}', {'action': 'final', 'answer': 'Done'}),
    ('```json\n{"action":"final","answer":"Done"}\n```', {'action': 'final', 'answer': 'Done'}),
    ('<tool_call>save_memory<arg_key>text</arg_key><arg_value>Orion</arg_value></tool_call>',
     {'action': 'tool', 'tool': 'save_memory', 'args': {'text': 'Orion'}}),
    ('<tool_call>search_memory {"query":"project name"}</tool_call>',
     {'action': 'tool', 'tool': 'search_memory', 'args': {'query': 'project name'}}),
    ('<tool_call>{"action":"tool","tool":"get_current_time"}</tool_call>',
     {'action': 'tool', 'tool': 'get_current_time', 'args': {}}),
    ('<tool_call>get_current_time</tool_call>',
     {'action': 'tool', 'tool': 'get_current_time', 'args': {}}),
    ('<tool_call>search_memory {"query":"Orion"}',
     {'action': 'tool', 'tool': 'search_memory', 'args': {'query': 'Orion'}}),
])
def test_parsing(raw, expected):
    assert agent.parse_model_response(raw) == expected


@pytest.mark.parametrize('raw', [None, '', 'not JSON', '[]', 'null', '42',
    '{"tool":"save_memory"}', '{"action":"other"}', '{"action":"final"}',
    '{"action":"final","answer":5}', '{"action":"tool"}',
    '{"action":"tool","tool":[]}', '{"action":"tool","tool":" "}',
    '{"action":"tool","tool":"save_memory","args":[]}',
    '<tool_call>save_memory<arg_key>text</arg_key></tool_call>',
    '<tool_call>save_memory {broken}</tool_call>',
    '<tool_call>save_memory {"text":"x"} trailing</tool_call>'])
def test_invalid_parsing(raw):
    assert agent.parse_model_response(raw) is None


def scripted_model(monkeypatch, responses):
    responses = iter(responses)
    observed = []
    def model(messages):
        observed.append([dict(message) for message in messages])
        return next(responses)
    monkeypatch.setattr(agent, 'call_model', model)
    return observed


def test_unknown_tool(monkeypatch):
    observed = scripted_model(monkeypatch, [
        '{"action":"tool","tool":"unknown"}', '{"action":"final","answer":"Done"}'])
    assert agent.run_agent('test') == 'Done'
    assert observed[1][-1]['content'] == 'Tool result: Unknown tool: unknown'


def test_tool_failure(monkeypatch):
    def fail():
        raise RuntimeError('private failure details')
    monkeypatch.setitem(agent.TOOLS, 'fail', {
        'function': fail, 'parameters': {}, 'description': 'Failure fixture',
        'permission': agent.READ_ONLY})
    observed = scripted_model(monkeypatch, [
        '{"action":"tool","tool":"fail"}', '{"action":"final","answer":"Failed"}'])
    assert agent.run_agent('test') == 'Failed'
    assert observed[1][-1]['content'] == 'Tool result: Tool failure: fail (RuntimeError)'


def test_none_handled_before_access(monkeypatch):
    scripted_model(monkeypatch, [None, '{"action":"final","answer":"Recovered"}'])
    assert agent.run_agent('test') == 'Recovered'


def test_invalid_responses_are_bounded(monkeypatch):
    observed = scripted_model(monkeypatch, ['plain prose'] * 5)
    assert agent.run_agent('test') == 'Maximum number of steps reached.'
    assert len(observed) == 5


def test_max_steps(monkeypatch):
    observed = scripted_model(monkeypatch, ['{"action":"tool","tool":"unknown"}'] * 5)
    assert agent.run_agent('test') == 'Maximum number of steps reached.'
    assert len(observed) == 5


@pytest.fixture
def memory_file(tmp_path, monkeypatch):
    path = tmp_path / 'memory.json'
    monkeypatch.setattr(tools, 'MEMORY_FILE', path)
    return path


@pytest.mark.parametrize('content', ['', '{bad', '{}', 'null', '[null, 1, {"text":42}]'])
def test_invalid_memory(memory_file, content):
    memory_file.write_text(content, encoding='utf-8')
    assert tools.load_memory() == []
    assert tools.search_memory('project') == 'No matching memories found.'


def test_missing_memory(memory_file):
    assert tools.load_memory() == []
    assert json.loads(memory_file.read_text()) == []


def test_save_search_and_restart(memory_file):
    assert tools.save_memory('My project is called Orion.') == 'Memory saved successfully.'
    assert json.loads(memory_file.read_text())[0]['text'] == 'My project is called Orion.'
    assert tools.search_memory('PROJECT NAME') == 'My project is called Orion.'
    assert tools.search_memory('unrelated') == 'No matching memories found.'
    script = 'import tools; from pathlib import Path; tools.MEMORY_FILE = Path(__import__("sys").argv[1]); print(tools.search_memory("project"))'
    output = subprocess.check_output([sys.executable, '-c', script, str(memory_file)],
                                     cwd=Path(tools.__file__).parent, text=True)
    assert 'Orion' in output


def test_time():
    assert datetime.strptime(tools.get_current_time(), '%Y-%m-%d %H:%M:%S').date() == datetime.now().date()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    root.mkdir()
    monkeypatch.setattr(tools, 'WORKSPACE_ROOT', root)
    (root / 'agent.py').write_text('print("hello")', encoding='utf-8')
    (root / '.env').write_text('API_KEY=never-show-this', encoding='utf-8')
    return root


def test_list_files(workspace):
    (workspace / 'docs').mkdir()
    for name in tools.IGNORED_DIRECTORIES:
        (workspace / name).mkdir()
    result = tools.list_files()
    assert 'agent.py' in result and '.env' in result and 'docs/' in result
    for name in tools.IGNORED_DIRECTORIES:
        assert name not in result
    assert 'never-show-this' not in result


def test_read_text(workspace):
    assert tools.read_text_file('agent.py') == 'print("hello")'
    assert tools.read_text_file(str(workspace / 'agent.py')) == 'print("hello")'


@pytest.mark.parametrize('path', ['.env', '.env.local', '../somefile.txt', '..\\somefile.txt',
    '.git/config', 'agent.py:stream', '.env ', '.env.', '.git /config'])
def test_denied_paths(workspace, path):
    assert tools.read_text_file(path).startswith('Access denied:')


def test_outside_absolute(workspace):
    outside = workspace.parent / 'outside.txt'
    outside.write_text('outside content', encoding='utf-8')
    assert tools.read_text_file(str(outside)).startswith('Access denied:')
    assert tools.list_files(str(workspace.parent)).startswith('Access denied:')


def test_directory_and_missing(workspace):
    assert 'directory' in tools.read_text_file('.')
    assert 'not found' in tools.read_text_file('missing.txt')
    assert 'not found' in tools.list_files('missing')
    assert 'expected a directory' in tools.list_files('agent.py')


@pytest.mark.parametrize('content', [b'abc\x00def', b'\xff\xfe', b'abc\x01def'])
def test_binary_file(workspace, content):
    (workspace / 'binary.dat').write_bytes(content)
    assert 'binary' in tools.read_text_file('binary.dat')


def test_file_size(workspace):
    (workspace / 'large.txt').write_bytes(b'a' * (tools.MAX_TEXT_BYTES + 1))
    assert '100 KB' in tools.read_text_file('large.txt')


def test_secret_redaction(workspace, monkeypatch):
    monkeypatch.setenv('EXAMPLE_API_KEY', 'known-secret-value')
    (workspace / 'notes.txt').write_text('known-secret-value\npassword=another-secret\n', encoding='utf-8')
    result = tools.read_text_file('notes.txt')
    assert 'known-secret-value' not in result and 'another-secret' not in result
    assert '[REDACTED]' in result


def test_private_key_block_and_source_read(workspace):
    (workspace / 'private.txt').write_text(
        '-----BEGIN PRIVATE KEY-----\nprivate material\n-----END PRIVATE KEY-----', encoding='utf-8')
    assert tools.read_text_file('private.txt').startswith('Access denied:')
    source = Path(tools.__file__).read_text(encoding='utf-8')
    (workspace / 'tools.py').write_text(source, encoding='utf-8')
    assert 'def read_text_file' in tools.read_text_file('tools.py')


def test_link_escape(workspace):
    outside = workspace.parent / 'outside.txt'
    outside.write_text('outside', encoding='utf-8')
    link = workspace / 'link.txt'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('Creating symlinks requires privileges on this Windows installation')
    assert tools.read_text_file('link.txt').startswith('Access denied:')
    assert 'link.txt' not in tools.list_files()


def test_hardlink_to_env(workspace):
    (workspace / 'alias.txt').hardlink_to(workspace / '.env')
    assert tools.read_text_file('alias.txt').startswith('Access denied:')


@pytest.mark.parametrize('args', [None, [], {'unexpected': 'x'}, {}, {'path': 7}, {'path': ''}])
def test_invalid_tool_arguments(args):
    assert agent.execute_tool('read_text_file', args).startswith('Invalid arguments:')


def test_malformed_arguments_in_loop(monkeypatch):
    observed = scripted_model(monkeypatch, [
        '{"action":"tool","tool":"read_text_file","args":{"path":7}}',
        '{"action":"final","answer":"Corrected"}'])
    assert agent.run_agent('test') == 'Corrected'
    assert 'Invalid arguments:' in observed[1][-1]['content']


def test_registry_metadata():
    for name, spec in agent.TOOLS.items():
        assert callable(spec['function'])
        assert spec['description'] and isinstance(spec['parameters'], dict)
        expected = {'save_memory': agent.WRITE_LOCAL, 'web_search': agent.NETWORK_READ}
        assert spec['permission'] == expected.get(name, agent.READ_ONLY)
        assert name in agent.SYSTEM_PROMPT


def test_debug_redacts_secrets(monkeypatch, capsys):
    import diagnostics
    monkeypatch.setattr(diagnostics, 'DEBUG', True)
    monkeypatch.setenv('EXAMPLE_API_KEY', 'known-secret-value')
    scripted_model(monkeypatch, ['{"action":"final","answer":"known-secret-value"}'])
    assert agent.run_agent('test') == '[REDACTED]'
    assert 'known-secret-value' not in capsys.readouterr().out
