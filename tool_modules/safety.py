import os
import re

def redact_secrets(value):
    """Best-effort protection for known environment secrets and common key formats."""
    if not isinstance(value, str):
        return value
    for name, secret in os.environ.items():
        if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", name, re.I) and len(secret) >= 8:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,})",
                   "[REDACTED]", value)
    value = re.sub(
        r"(?im)^(\s*[\"']?[\w.-]*(?:api[_-]?key|token|secret|password|credential)"
        r"[\w.-]*[\"']?\s*[:=]\s*).+$", r"\1[REDACTED]", value)
    return value


