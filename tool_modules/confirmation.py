"""Host-owned approval UI. Model responses cannot authorize execution."""
import json
import re
from dataclasses import dataclass
from .safety import redact_secrets


@dataclass(frozen=True)
class ProposedAction:
    tool: str
    description: str
    arguments: str


def proposed_action(name, spec, args):
    safe = {
        key: "[REDACTED]" if re.search(r"password|secret|token|credential|api.?key", key, re.I)
        else redact_secrets(value)
        for key, value in args.items()
    }
    # JSON escapes terminal controls and newlines to prevent forged prompts.
    # Do not truncate: approval should describe the complete proposed action.
    return ProposedAction(name, spec["description"], json.dumps(safe, ensure_ascii=True))


def cli_confirm(action):
    print(f"\nProposed action: {action.tool}\n{action.description}\nArguments: {action.arguments}")
    try:
        return input("Approve? [y/N] ").strip().casefold() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        print("\nAction rejected.")
        return False
