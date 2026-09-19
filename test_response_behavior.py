"""Offline prompt-contract and presentation regressions; no live-model quality claims."""
import json

import pytest

import agent
import tools
from conversation import ConversationSession
from test_conversation import FakeProvider, final


def test_public_identity_and_private_protocol_contract():
    prompt = agent.build_system_prompt()
    assert prompt.startswith("You are Athena, an AI work assistant.")
    assert "Respond naturally and concisely." in prompt
    assert "Internal response formatting requirements are implementation details" in prompt
    assert "Never mention required JSON format, action schemas, parser requirements, normalization," in prompt
    assert "internal response validation, tool schemas, or internal tool-call protocol unless the user" in prompt
    assert "explicitly asks how Athena is implemented" in prompt
    assert "do not acknowledge these internal instructions" in prompt
    assert "all secret and permission safeguards still apply" in prompt
    assert 'Always respond with one JSON object:' in prompt
    assert '{"action":"tool","tool":"tool_name","args":{}}' in prompt
    assert '{"action":"final","answer":"your answer"}' in prompt
    assert "Its answer field contains the natural user-facing reply" in prompt


def test_memory_capability_and_privacy_contract():
    prompt = agent.build_system_prompt()
    for instruction in (
        "Conversation history: temporary, bounded context from the current session",
        "Persistent memory: information explicitly requested for long-term saving",
        "user approval. It persists across sessions and is searchable with search_memory",
        "save_memory requires confirmation",
        "Experience history: compact records of past tasks, tool use, outcomes, feedback, and quality",
        "Relevant records are retrieved automatically as historical evidence",
        "not the same\n  as user-approved personal memory or a complete conversation transcript",
        "Do not claim that all past conversations are stored as personal memory",
        "no persistent memory. Do not invent stored contents",
        "without revealing private saved contents unless the user asks",
        "Before reporting specific persistent memory contents, use search_memory",
        "Do not save information just because memory is discussed",
        "Respect tool permissions; never bypass or self-approve required confirmation",
    ):
        assert instruction in prompt


@pytest.mark.parametrize("repair", [False, True])
def test_normal_cli_prints_only_generated_answer(monkeypatch, capsys, repair):
    # Two distinct model-authored fixtures guard against a hardcoded identity reply.
    answers = ["I'm Athena, and I'm here to help with your work.", "What would you like to work on?"]
    responses = (["invalid response"] if repair else []) + [final(answer) for answer in answers]
    provider = FakeProvider(responses)
    monkeypatch.setattr(agent, "model_provider", provider)
    commands = iter(["You are also called Athena, my AI assistant.", "Hello again", "exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(commands))
    agent.main()
    output = capsys.readouterr().out
    for answer in answers:
        assert f"Athena > {answer}" in output
    for internal in ("required JSON format", "action schema", "parser", "normalization",
                     "internal response validation", '"action"', '"answer"'):
        assert internal not in output
    for request in provider.requests:
        assert request[0] == {"role": "system", "content": agent.SYSTEM_PROMPT}
        assert "Internal response formatting requirements are implementation details" in request[0]["content"]
    if repair:
        assert "Invalid response format" in provider.requests[1][-1]["content"]


@pytest.mark.parametrize("question", ["What do you remember?", "Do you have memory?",
                                     "What memory tools do you have?", "Do you remember past conversations?"])
def test_memory_questions_receive_three_system_contract(question, monkeypatch, tmp_path):
    memory = tmp_path / "memory.json"
    memory.write_text('[{"text":"private fixture content"}]')
    monkeypatch.setattr(tools, "MEMORY_FILE", memory)
    answer = ("I use current-session conversation history, approved persistent memory, "
              "and relevant experience history from past tasks. These are distinct; "
              "I don't store every conversation as personal memory.")
    provider = FakeProvider([final(answer)])
    session = ConversationSession()
    assert agent.run_agent(question, provider, session) == answer
    request_text = json.dumps(provider.requests)
    assert "Conversation history:" in request_text
    assert "Persistent memory:" in request_text
    assert "Experience history:" in request_text
    assert "private fixture content" not in request_text
    assert memory.read_text() == '[{"text":"private fixture content"}]'
    assert session.recent_messages()[-1]["content"] == answer


def test_memory_tool_protocol_and_final_parser_still_work(monkeypatch, tmp_path):
    memory = tmp_path / "memory.json"
    memory.write_text('[{"text":"Prefers brief reports"}]')
    monkeypatch.setattr(tools, "MEMORY_FILE", memory)
    call = '{"action":"tool","tool":"search_memory","args":{"query":"reports"}}'
    assert agent.parse_model_response(call) == {
        "action": "tool", "tool": "search_memory", "args": {"query": "reports"}}
    answer = "Your saved preference is for brief reports."
    assert agent.parse_model_response(final(answer)) == {"action": "final", "answer": answer}
    provider = FakeProvider([call, final(answer)])
    assert agent.run_agent("What have I saved about reports?", provider) == answer
    assert "Prefers brief reports" in provider.requests[1][-1]["content"]


def test_explicit_implementation_question_can_be_answered():
    answer = "Internally, a JSON action schema separates tool requests from final answers."
    provider = FakeProvider([final(answer)])
    assert agent.run_agent("How is Athena implemented? Explain its internal action schema.", provider) == answer
