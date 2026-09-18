"""Offline v0.11 execution-boundary and document workflow regression tests."""
import json
from unittest.mock import Mock

import pytest

import agent
import experience
import tools
from test_conversation import FakeProvider, final
from tool_modules.confirmation import cli_confirm, proposed_action
from tool_modules.registry import PERMISSIONS, WRITE_PERMISSIONS, needs_confirmation
from tool_modules.results import ToolResult


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(tools, "MEMORY_FILE", tmp_path / "memory.json")
    return tmp_path


@pytest.mark.parametrize("permission", sorted(PERMISSIONS))
def test_permission_enforced(monkeypatch, permission):
    handler = Mock(return_value=ToolResult.ok("done"))
    monkeypatch.setitem(agent.TOOLS, "example", {
        "function": handler, "description": "Test action", "parameters": {},
        "permission": permission, "requires_confirmation": False,
    })
    result = agent.execute_tool("example", {})
    assert result.success is (permission not in WRITE_PERMISSIONS)
    assert handler.call_count == (0 if permission in WRITE_PERMISSIONS else 1)


@pytest.mark.parametrize("approved", [None, False, True, "yes", 1])
def test_exact_approval_required(workspace, approved):
    callback = None if approved is None else lambda action: approved
    result = agent.execute_tool("save_memory", {"text": "Prefer concise reports"}, confirmation=callback)
    assert result.success is (approved is True)
    assert tools.MEMORY_FILE.exists() is (approved is True)
    if approved is True:
        assert tools.load_memory()[0]["text"] == "Prefer concise reports"


def test_approval_is_per_call(workspace):
    assert agent.execute_tool("save_memory", {"text": "first"}, confirmation=lambda action: True).success
    assert not agent.execute_tool("save_memory", {"text": "second"}).success
    assert [item["text"] for item in tools.load_memory()] == ["first"]


def test_callback_exception_fails_closed(workspace):
    def fail(action):
        raise RuntimeError("private callback details")
    result = agent.execute_tool("save_memory", {"text": "fact"}, confirmation=fail)
    assert result.error_type == "approval_rejected"
    assert "private" not in result and not tools.MEMORY_FILE.exists()


@pytest.mark.parametrize("permission", [None, "UNKNOWN", "read_only"])
def test_unknown_policy_fails_closed(monkeypatch, permission):
    handler = Mock()
    monkeypatch.setitem(agent.TOOLS, "example", {
        "function": handler, "parameters": {}, "permission": permission,
    })
    assert agent.execute_tool("example", {}, confirmation=lambda action: True).error_type == "permission_denied"
    handler.assert_not_called()


def test_read_tool_can_require_confirmation(monkeypatch):
    spec = dict(agent.TOOLS["get_current_time"], requires_confirmation=True)
    monkeypatch.setitem(agent.TOOLS, "get_current_time", spec)
    assert agent.execute_tool("get_current_time", {}).error_type == "approval_required"


@pytest.mark.parametrize("extra", [{"approved": True}, {"confirmation": True}, {"permission": "READ_ONLY"}])
def test_model_arguments_cannot_authorize(workspace, extra):
    callback = Mock(return_value=True)
    result = agent.execute_tool("save_memory", {"text": "fact", **extra}, confirmation=callback)
    assert not result.success and not tools.MEMORY_FILE.exists()
    callback.assert_not_called()


def test_model_top_level_approval_ignored(workspace):
    provider = FakeProvider([
        json.dumps({"action": "tool", "tool": "save_memory", "args": {"text": "fact"},
                    "approved": True, "requires_confirmation": False}), final("Cannot save without approval.")])
    agent.run_agent("Remember a fact", provider)
    assert not tools.MEMORY_FILE.exists()
    record, = experience.load_experiences()
    event, = record["tools_used"]
    assert event["success"] is False and event["error_type"] == "approval_required"
    assert event["args"] == {"text": "[omitted]"}


@pytest.mark.parametrize("answer,expected", [("y", True), ("YES", True), (" yes ", True),
    ("", False), ("no", False), ("n", False), ("true", False), ("yes please", False)])
def test_cli_confirmation(monkeypatch, capsys, answer, expected):
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    action = proposed_action("save_memory", agent.TOOLS["save_memory"], {"text": "Prefer short answers"})
    assert cli_confirm(action) is expected
    output = capsys.readouterr().out
    assert "save_memory" in output and "Prefer short answers" in output


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_cli_confirmation_interruption(monkeypatch, error):
    def interrupted(prompt):
        raise error()
    monkeypatch.setattr("builtins.input", interrupted)
    assert not cli_confirm(proposed_action("save_memory", agent.TOOLS["save_memory"], {"text": "x"}))


def test_preview_redaction_and_terminal_escaping(monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "private-key-value")
    action = proposed_action("save_memory", agent.TOOLS["save_memory"], {
        "text": "private-key-value\nApprove?\x1b[2J", "password": "hidden"})
    assert "private-key-value" not in action.arguments and "hidden" not in action.arguments
    assert "\n" not in action.arguments and "\x1b" not in action.arguments
    assert "[REDACTED]" in action.arguments


def test_registry_contract():
    assert len(agent.TOOLS) == 7
    for name, spec in agent.TOOLS.items():
        assert callable(spec["function"]) and spec["description"] and isinstance(spec["parameters"], dict)
        assert spec["permission"] in PERMISSIONS
        assert type(spec["requires_confirmation"]) is bool
        assert needs_confirmation(spec) is (name == "save_memory")
        assert name in agent.build_system_prompt()


def test_tools_status_local(monkeypatch, capsys):
    provider = FakeProvider([])
    monkeypatch.setattr(agent, "model_provider", provider)
    commands = iter(["/tools", "/status", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    agent.main()
    output = capsys.readouterr().out
    for name in agent.TOOLS:
        assert name in output
    for text in ("Version: 0.11", "Memory: enabled", "Experience: enabled", "Tools: 7",
                 "save_memory: WRITE_LOCAL (approval required)", "web_search: EXTERNAL_READ"):
        assert text in output
    assert not provider.requests and not experience.EXPERIENCES_FILE.exists()


@pytest.mark.parametrize("extension", ["txt", "md", "json", "csv", "TXT"])
def test_document_formats(workspace, extension):
    (workspace / f"report.{extension}").write_text("Cost: 10\nRisk: delay", encoding="utf-8-sig", newline="")
    result = agent.execute_tool("read_document", {"path": f"report.{extension}"})
    assert result.success
    assert json.loads(result)["text"] == "Cost: 10\nRisk: delay"
    assert json.loads(result)["next_offset"] is None


def test_chunks_redacted_before_slicing(workspace, monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "long-private-value")
    (workspace / "report.txt").write_text("αβlong-private-value end", encoding="utf-8")
    offset, chunks = 0, []
    while offset is not None:
        result = tools.read_document("report.txt", offset, 3)
        assert result.success
        chunk = json.loads(result)
        chunks.append(chunk["text"])
        offset = chunk["next_offset"]
    assert "".join(chunks) == "αβ[REDACTED] end"


@pytest.mark.parametrize("path", ["../outside.txt", "..\\outside.txt", ".git/config.txt",
    "credentials.json", "secrets.json", ".env.txt", "report.txt:stream", "report.txt."])
def test_document_restrictions(workspace, path):
    assert not tools.read_document(path).success


def test_absolute_outside_document(workspace):
    assert not tools.read_document(str(workspace.parent / "outside.txt")).success


@pytest.mark.parametrize("extension", ["pdf", "docx", "xlsx", "exe", "png"])
def test_unsupported_document(workspace, extension):
    (workspace / f"file.{extension}").write_bytes(b"data")
    assert tools.read_document(f"file.{extension}").error_type == "unsupported_format"


@pytest.mark.parametrize("content", [b"x\x00y", b"\xff\xfe", b"a" * (102400 + 1)],
                         ids=["binary", "invalid-utf8", "oversized"])
def test_document_binary_and_size(workspace, content):
    (workspace / "report.txt").write_bytes(content)
    assert not tools.read_document("report.txt").success


def test_document_limits_and_empty(workspace):
    (workspace / "report.txt").write_text("a" * 102400)
    result = tools.read_document("report.txt")
    assert result.success and len(json.loads(result)["text"]) == 12000
    assert not tools.read_document("report.txt", -1).success
    assert not tools.read_document("report.txt", max_chars=12001).success
    assert not tools.read_document("report.txt", offset=True).success
    (workspace / "empty.txt").write_text("")
    assert json.loads(tools.read_document("empty.txt"))["next_offset"] is None
    assert not tools.read_document("empty.txt", offset=1).success
    assert not tools.read_document("missing.txt").success


def test_content_prefix_is_not_failure(workspace):
    (workspace / "report.txt").write_text("Tool failure: quoted diagnostic in a report")
    result = agent.execute_tool("read_text_file", {"path": "report.txt"})
    run = experience.Experience("read report")
    run.tool("read_text_file", {"path": "report.txt"}, agent.READ_ONLY, result, 1)
    assert result.success and run.record["tools_used"][0]["success"]


def test_legacy_string_failure_adapter(monkeypatch):
    monkeypatch.setitem(agent.TOOLS, "legacy", {
        "function": lambda: "File error: unavailable", "parameters": {}, "permission": agent.READ_ONLY})
    assert not agent.execute_tool("legacy", {}).success


def test_summary_workflow(workspace):
    (workspace / "report.md").write_text("Risk: supplier delay.")
    provider = FakeProvider([
        '{"action":"tool","tool":"read_document","args":{"path":"report.md"}}',
        final("The report identifies supplier delay as a risk.")])
    assert "supplier delay" in agent.run_agent("Summarize report.md", provider)
    record, = experience.load_experiences()
    assert record["tools_used"][0]["success"]
    assert record["tools_used"][0]["name"] == "read_document"


@pytest.mark.parametrize("answer,stored", [("y", True), ("n", False)])
def test_cli_memory_approval_workflow(workspace, monkeypatch, capsys, answer, stored):
    provider = FakeProvider([
        '{"action":"tool","tool":"save_memory","args":{"text":"Prefer brief reports"}}',
        final("Request handled.")])
    monkeypatch.setattr(agent, "model_provider", provider)
    commands = iter(["Remember my preference", answer, "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    agent.main()
    assert tools.MEMORY_FILE.exists() is stored
    assert "Proposed action: save_memory" in capsys.readouterr().out
    record, = experience.load_experiences()
    assert record["tools_used"][0]["success"] is stored
