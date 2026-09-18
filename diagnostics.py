"""Optional developer output, configured once when the application starts."""
import os

from dotenv import load_dotenv
from tools import redact_secrets


load_dotenv()
DEBUG = os.getenv("MY_AGENT_DEBUG", "false").lower() == "true"


def debug_print(message):
    if DEBUG:
        print(redact_secrets(message))
