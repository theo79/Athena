"""Single source of tool schemas and execution policy. No model-supplied policy."""
from typing import Callable, TypedDict
from tools import (web_search, get_current_time, save_memory, search_memory,
                   list_files, read_text_file, read_document, MAX_SEARCH_QUERY_LENGTH)

READ_ONLY = "READ_ONLY"
WRITE_LOCAL = "WRITE_LOCAL"
EXTERNAL_READ = "EXTERNAL_READ"
EXTERNAL_WRITE = "EXTERNAL_WRITE"
SENSITIVE_ACTION = "SENSITIVE_ACTION"
NETWORK_READ = EXTERNAL_READ  # v0.10 import compatibility
PERMISSIONS = {READ_ONLY, WRITE_LOCAL, EXTERNAL_READ, EXTERNAL_WRITE, SENSITIVE_ACTION}
WRITE_PERMISSIONS = {WRITE_LOCAL, EXTERNAL_WRITE, SENSITIVE_ACTION}

class ToolDefinition(TypedDict):
    description: str
    parameters: dict
    permission: str
    requires_confirmation: bool
    function: Callable

TOOLS: dict[str, ToolDefinition] = {
    "web_search": {
        "function": web_search,
        "description": "Search current or external information. max_results defaults to 5 (1-10); query up to 500 characters.",
        "parameters": {
            "query": {"type": str, "required": True, "max_length": MAX_SEARCH_QUERY_LENGTH},
            "max_results": {"type": int, "required": False, "min": 1, "max": 10},
        },
        "permission": EXTERNAL_READ,
    },
    "get_current_time": {
        "function": get_current_time,
        "description": "Get the current local date and time.",
        "parameters": {}, "permission": READ_ONLY,
    },
    "save_memory": {
        "function": save_memory,
        "description": "Save information when the user explicitly asks to remember it.",
        "parameters": {"text": {"type": str, "required": True}},
        "permission": WRITE_LOCAL,
    },
    "search_memory": {
        "function": search_memory,
        "description": "Search previously saved information by keywords.",
        "parameters": {"query": {"type": str, "required": True}},
        "permission": READ_ONLY,
    },
    "list_files": {
        "function": list_files,
        "description": "List available files and directories in the workspace; path defaults to '.'.",
        "parameters": {"path": {"type": str, "required": False}},
        "permission": READ_ONLY,
    },
    "read_text_file": {
        "function": read_text_file,
        "description": "Read a specific UTF-8 text file in the workspace, up to 100 KB.",
        "parameters": {"path": {"type": str, "required": True}},
        "permission": READ_ONLY,
    },
}


TOOLS["read_document"] = {
    "function": read_document,
    "description": "Read UTF-8 .txt, .md, .json or .csv documents in bounded character chunks (100 KB file limit). Continue with next_offset when present.",
    "parameters": {
        "path": {"type": str, "required": True},
        "offset": {"type": int, "required": False, "min": 0, "max": 102400},
        "max_chars": {"type": int, "required": False, "min": 1, "max": 12000},
    },
    "permission": READ_ONLY,
}
for spec in TOOLS.values():
    spec["requires_confirmation"] = spec["permission"] in WRITE_PERMISSIONS

def needs_confirmation(spec):
    # Write categories cannot opt out with a false metadata flag.
    return spec["permission"] in WRITE_PERMISSIONS or spec.get("requires_confirmation", False)
