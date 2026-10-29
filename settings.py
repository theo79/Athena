"""Small, secret-free preferences and interactive setup."""
import getpass
import json
import re
import os

import runtime_paths

DEFAULT_MODELS = {"openai": "", "local": "llama3.2", "openrouter": "openrouter/free", "gemini": "gemini-3.1-flash-lite", "ollama": "llama3.2"}
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
KEY_NAMES = {"openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY", "gemini": "GEMINI_API_KEY"}
PROVIDER_LABELS = {"openai": "OpenAI", "local": "Local AI (Ollama)", "openrouter": "OpenRouter", "gemini": "Gemini", "ollama": "Ollama"}
COST_NOTICE = "Web searches use your selected provider and may count toward its API usage or billing."


def preferences_path():
    return runtime_paths.user_config_dir() / "config.json"


def load_preferences():
    try:
        data = json.loads(preferences_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, UnicodeError):
        return {}
    if not isinstance(data, dict) or data.get('provider_config_version', 1) != 1:
        return {}
    result = {}
    if isinstance(data.get("model_provider"), str) and data["model_provider"] in DEFAULT_MODELS:
        result["MODEL_PROVIDER"] = data["model_provider"]
        if isinstance(data.get("model_name"), str) and data["model_name"].strip():
            result["MODEL_NAME"] = data["model_name"].strip()
    if type(data.get("web_search_enabled")) is bool:
        result["WEB_SEARCH_ENABLED"] = str(data["web_search_enabled"]).lower()
    if isinstance(data.get("ollama_base_url"), str) and data["ollama_base_url"].strip():
        result["OLLAMA_BASE_URL"] = data["ollama_base_url"].strip()
    reference = data.get('credential_env')
    if isinstance(reference, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', reference):
        result['MODEL_CREDENTIAL_ENV'] = reference
    return result


def credential_env(provider=None):
    return os.getenv('MODEL_CREDENTIAL_ENV') or KEY_NAMES.get(provider or provider_name(), '')


def web_enabled():
    return not web_unavailable_reason() and os.getenv("WEB_SEARCH_ENABLED", "false").strip().lower() == "true"


class WebSearchUnavailable(ValueError):
    """A provider capability restriction, safe to display."""


def web_unavailable_reason():
    if provider_name() in ('ollama', 'local'):
        return "Web search is not available with Ollama yet."
    if provider_name() == 'openai':
        return "Web search is not available with OpenAI yet."
    return None


def web_status():
    return "unavailable" if web_unavailable_reason() else ("enabled" if web_enabled() else "disabled")


def ollama_base_url():
    return os.getenv("OLLAMA_BASE_URL", "").strip() or DEFAULT_OLLAMA_BASE_URL


def provider_name():
    return os.getenv("MODEL_PROVIDER", "openrouter").strip()


def model_name(provider=None):
    return os.getenv("MODEL_NAME", "").strip() or DEFAULT_MODELS.get(provider or provider_name(), "")


def valid_configuration():
    if provider_name() in ("ollama", "local"):
        return True  # Reachability is checked by the provider, not by key presence.
    key = credential_env()
    return bool(key and model_name() and os.getenv(key, "").strip())


def save_preferences(provider, model, enabled):
    from provider_configuration import atomic_preferences
    changes = {"model_provider": provider, "model_name": model,
               "web_search_enabled": enabled if provider not in ('local', 'ollama') else False}
    if provider in ('local', 'ollama'):
        changes['ollama_base_url'] = ollama_base_url()
    atomic_preferences(preferences_path(), changes)


def set_web_enabled(enabled):
    if enabled and web_unavailable_reason():
        raise WebSearchUnavailable(web_unavailable_reason())
    save_preferences(provider_name(), model_name(), enabled)
    os.environ["WEB_SEARCH_ENABLED"] = str(enabled).lower()


def ensure_configuration():
    if valid_configuration():
        return True  # Preserve valid legacy installations without destructive setup.
    print("Welcome to Athena.\nLet's set up your AI provider.")
    from provider_cli import configure_provider
    return configure_provider() is not None
