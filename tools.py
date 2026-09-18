"""Stable public tool API and workspace configuration."""
from pathlib import Path
from datetime import datetime
import search_provider
from runtime_paths import memory_path, application_dir

MEMORY_FILE = memory_path()
WORKSPACE_ROOT = application_dir()
MAX_TEXT_BYTES = 100 * 1024
IGNORED_DIRECTORIES = {".venv", "__pycache__", ".git", ".pytest_cache"}
MAX_SEARCH_QUERY_LENGTH = 500


from tool_modules.safety import redact_secrets
from tool_modules.local_files import workspace_path, list_files
from tool_modules.local_files import read_text_file as _read_text_file
from tool_modules.memory import load_memory, save_memory, search_memory
from tool_modules.web import web_search

def read_text_file(path):
    """Compatibility entry point for bounded UTF-8 reading."""
    return _read_text_file(path)

def get_current_time():
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")


from tool_modules.local_files import read_document
