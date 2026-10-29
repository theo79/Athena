import experience
import diagnostics
from diagnostics import debug_print
from conversation import ConversationSession

import runtime_paths
import settings
from models import ModelProvider, ModelFailure, ModelConfigurationError, get_model_provider

from tools import (get_current_time, save_memory, search_memory,
                   list_files, read_text_file, redact_secrets, web_search,
                   MAX_SEARCH_QUERY_LENGTH)


try:
    runtime_paths.load_configuration()
except (OSError, UnicodeError):
    pass  # The packaged startup check reports configuration failures safely.

diagnostics.configure()

model_provider: ModelProvider | None = None
VERSION = "0.12"


from tool_modules.registry import (TOOLS, READ_ONLY, WRITE_LOCAL, NETWORK_READ,
    EXTERNAL_READ, EXTERNAL_WRITE, SENSITIVE_ACTION, PERMISSIONS, needs_confirmation)
from tool_modules.results import ToolResult
from tool_modules.confirmation import cli_confirm, proposed_action


import agent_runtime


def build_system_prompt():
    return agent_runtime.build_system_prompt(TOOLS)


SYSTEM_PROMPT = build_system_prompt()


def execute_tool(tool_name, args, *, confirmation=None):
    return agent_runtime.execute_tool(tool_name, args, confirmation=confirmation, registry=TOOLS)


def normalize_response(data):
    return agent_runtime.normalize_response(data)


def strip_code_fence(text):
    return agent_runtime.strip_code_fence(text)


def parse_model_response(response):
    return agent_runtime.parse_model_response(response)


def call_model(messages):
    global model_provider
    if model_provider is None:
        try:
            model_provider = get_model_provider()
        except ModelConfigurationError as exc:
            return ModelFailure(redact_secrets(str(exc)))
    return model_provider.generate(messages)


def run_agent(task, provider: ModelProvider | None = None, conversation=None, *, confirmation=None):
    """Compatibility API; resolve legacy hooks at call time for existing hosts/tests."""
    return agent_runtime.run_agent(
        task, provider, conversation, confirmation=confirmation,
        model_call=call_model, provider_info=lambda: model_provider,
        tool_executor=execute_tool, response_parser=parse_model_response,
        system_prompt=SYSTEM_PROMPT, registry=TOOLS, experience_service=experience)


def main():
    global model_provider
    print(f"Athena v{VERSION}")
    startup_error = runtime_paths.packaged_startup_error()
    if startup_error:
        print(startup_error)
        return
    if model_provider is None:
        if not settings.ensure_configuration():
            return
        try:
            model_provider = get_model_provider()
        except ModelConfigurationError as exc:
            print(redact_secrets(str(exc)))
            return
    print("Ready.\n")

    session = ConversationSession()

    while True:

        try:
            user_input = input("You > ")
        except (EOFError, KeyboardInterrupt):
            break

        if user_input.lower() == "exit":
            break
        feedback_commands = {"/good": "positive", "/bad": "negative", "/neutral": "neutral"}
        command = user_input.strip().lower()
        if command == "/config provider":
            from provider_cli import configure_provider
            selected = configure_provider()
            if selected is not None:
                model_provider = selected
            continue
        if command == "/help":
            print("Commands:\n"
                  "/help - Show available commands\n"
                  "/config provider - Test and change AI provider\n"
                  "/status - Show runtime information\n"
                  "/settings [web on|web off] - Inspect settings or change web access\n"
                  "/tools - Show tools and permissions\n"
                  "/history - Show conversation history\n"
                  "/clear - Clear conversation history\n"
                  "/good - Rate the last task positively\n"
                  "/bad - Rate the last task negatively\n"
                  "/neutral - Rate the last task neutrally\n"
                  "exit - Exit Athena\n")
            continue
        if command == "/tools":
            print("Available tools:")
            for name, spec in TOOLS.items():
                approval = "approval required" if needs_confirmation(spec) else "no confirmation"
                print(f"  {name}: {spec['permission']} ({approval})")
            print()
            continue
        if command.startswith("/settings"):
            if command in ("/settings web on", "/settings web off"):
                enabled = command.endswith(" on")
                try:
                    settings.set_web_enabled(enabled)
                except settings.WebSearchUnavailable as exc:
                    print(str(exc))
                    continue
                except (OSError, UnicodeError):
                    print("Settings could not be saved. Check file permissions and try again.")
                    continue
                if enabled:
                    print(settings.COST_NOTICE)
            elif command != "/settings":
                print("Usage: /settings [web on|web off]")
                continue
        if command == "/status" or command.startswith("/settings"):
            print(redact_secrets(
                f"Athena v{VERSION}\n"
                f"Version: {VERSION}\n"
                f"Provider: {settings.PROVIDER_LABELS.get(getattr(model_provider, 'provider_name', ''), 'custom')}\n"
                f"Model: {getattr(model_provider, 'model_name', 'custom')}\n"
                f"Web search: {settings.web_status()}\n"
                f"{getattr(model_provider, 'status_lines', lambda: '')()}"
                f"Debug: {str(diagnostics.DEBUG).lower()}\n"
                f"Memory: enabled\nExperience: enabled\nTools: {len(TOOLS)}\n"))
            continue
        if command in feedback_commands:
            feedback = feedback_commands[command]
            if session.last_experience_id is None:
                print("No recent task is available for feedback.")
            elif experience.set_experience_feedback(session.last_experience_id, feedback):
                print(f"Feedback saved: {feedback}.")
                print(f"Task: {session.last_experience_task}")
                debug_print(f"Feedback saved for experience: {session.last_experience_id}")
                debug_print(f"Reward: {experience.FEEDBACK_REWARDS[feedback]}")
            else:
                print("Feedback could not be saved: experience not found or storage unavailable.")
            continue
        if user_input.strip().lower() == "/history":
            print(session.display())
            continue
        if user_input.strip().lower() == "/clear":
            session.clear()
            print("Conversation history cleared.")
            continue

        result = run_agent(user_input, conversation=session, confirmation=cli_confirm)

        print(f"\nAthena > {result}\n")


if __name__ == "__main__":
    main()
