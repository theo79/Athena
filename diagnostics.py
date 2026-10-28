"""Optional developer output, configured once when the application starts."""
import os

from tools import redact_secrets


DEBUG = os.getenv("MY_AGENT_DEBUG", "false").lower() == "true"


def configure():
    """Refresh diagnostics after the host explicitly loads configuration."""
    global DEBUG
    DEBUG = os.getenv("MY_AGENT_DEBUG", "false").lower() == "true"


def debug_print(message):
    if DEBUG:
        print(redact_secrets(message))
