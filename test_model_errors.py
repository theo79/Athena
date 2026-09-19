"""Model failures are simulated locally; no API requests are made."""
from types import SimpleNamespace
from unittest.mock import Mock

import httpx2
import pytest
from openai import (RateLimitError, AuthenticationError, APIConnectionError,
                    APITimeoutError, APIStatusError)

import agent
import models


SECRET = "sensitive-provider-payload"


def status_error(cls, code):
    request = httpx2.Request("POST", "https://example.test/chat",
                            headers={"Authorization": f"Bearer {SECRET}"})
    response = httpx2.Response(code, request=request,
                               headers={"retry-after": SECRET})
    return cls(SECRET, response=response, body={"error": SECRET})


def fake_client(monkeypatch, effects):
    create = Mock(side_effect=effects)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider = models.OpenRouterProvider()
    provider.client = client
    monkeypatch.setattr(agent, "model_provider", provider)
    return create


@pytest.mark.parametrize("error,expected", [
    (status_error(RateLimitError, 429), "Model provider rate limit reached."),
    (status_error(AuthenticationError, 401), "Model provider authentication failed."),
    (APIConnectionError(message=SECRET, request=httpx2.Request("POST", "https://example.test")),
     "Could not connect to the model provider."),
    (APITimeoutError(request=httpx2.Request("POST", "https://example.test")),
     "Model provider request timed out."),
    (status_error(APIStatusError, 503), "Model provider returned an API error: 503"),
])
def test_model_failure_stops_task(monkeypatch, capsys, error, expected):
    create = fake_client(monkeypatch, [error])
    parse = Mock(side_effect=AssertionError("Model failures must not reach the parser"))
    execute = Mock(side_effect=AssertionError("Model failures must not execute tools"))
    monkeypatch.setattr(agent, "parse_model_response", parse)
    monkeypatch.setattr(agent, "execute_tool", execute)
    result = agent.run_agent("Search the web")
    assert result.startswith(expected)
    create.assert_called_once()
    parse.assert_not_called()
    execute.assert_not_called()
    output = capsys.readouterr().out
    assert "Step 2" not in output
    assert SECRET not in result + output
    assert "Authorization" not in result + output


def successful_response(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def test_interactive_loop_continues(monkeypatch, capsys):
    create = fake_client(monkeypatch, [status_error(RateLimitError, 429),
        successful_response('{"action":"final","answer":"Second task succeeded"}')])
    inputs = iter(["First task", "Second task", "exit"])
    prompts = []
    def user_input(prompt):
        prompts.append(prompt)
        return next(inputs)
    monkeypatch.setattr("builtins.input", user_input)
    agent.main()
    output = capsys.readouterr().out
    assert "Athena > Model provider rate limit reached." in output
    assert "Athena > Second task succeeded" in output
    assert prompts == ["You > "] * 3
    assert create.call_count == 2
    assert SECRET not in output


def test_sdk_retries_disabled(monkeypatch):
    monkeypatch.setattr(agent, "model_provider", models.OpenRouterProvider())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    create = Mock(return_value=successful_response("unchanged text"))
    constructor = Mock(return_value=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(models, "OpenAI", constructor)
    assert agent.call_model([]) == "unchanged text"
    assert constructor.call_args.kwargs["max_retries"] == 0


def test_programming_errors_not_hidden(monkeypatch):
    fake_client(monkeypatch, [TypeError("programming bug")])
    with pytest.raises(TypeError, match="programming bug"):
        agent.run_agent("test")


def test_failure_after_tool_stops_without_repeating_tool(monkeypatch):
    create = fake_client(monkeypatch, [
        successful_response('{"action":"tool","tool":"get_current_time","args":{}}'),
        status_error(RateLimitError, 429)])
    execute = Mock(return_value="2026-09-17 12:00:00")
    monkeypatch.setattr(agent, "execute_tool", execute)
    assert agent.run_agent("What time is it?").startswith("Model provider rate limit reached.")
    execute.assert_called_once_with("get_current_time", {})
    assert create.call_count == 2
