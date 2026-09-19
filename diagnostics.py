"""Optional developer output, configured once when the application starts."""
import os

from runtime_paths import load_configuration
from tools import redact_secrets


try:
    load_configuration()
except (OSError, UnicodeError):
    pass  # Startup reports a safe configuration error instead of a traceback.
DEBUG = os.getenv("MY_AGENT_DEBUG", "false").lower() == "true"


def debug_print(message):
    if DEBUG:
        print(redact_secrets(message))
