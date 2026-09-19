"""Small, secret-free preferences and interactive setup."""
import getpass
import json
import os
import warnings
from tempfile import NamedTemporaryFile

from dotenv import set_key
import runtime_paths

DEFAULT_MODELS = {"openrouter": "openrouter/free", "gemini": "gemini-3.1-flash-lite", "ollama": "llama3.2"}
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
KEY_NAMES = {"openrouter": "OPENROUTER_API_KEY", "gemini": "GEMINI_API_KEY"}
PROVIDER_LABELS = {"openrouter": "OpenRouter", "gemini": "Gemini", "ollama": "Ollama"}
COST_NOTICE = "Web searches use your selected provider and may count toward its API usage or billing."


def preferences_path():
    return runtime_paths.user_config_dir() / "config.json"


def load_preferences():
    try:
        data = json.loads(preferences_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, UnicodeError):
        return {}
    if not isinstance(data, dict):
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
    return result


def web_enabled():
    return not web_unavailable_reason() and os.getenv("WEB_SEARCH_ENABLED", "false").strip().lower() == "true"


class WebSearchUnavailable(ValueError):
    """A provider capability restriction, safe to display."""


def web_unavailable_reason():
    return "Web search is not available with Ollama yet." if provider_name() == "ollama" else None


def web_status():
    return "unavailable" if web_unavailable_reason() else ("enabled" if web_enabled() else "disabled")


def ollama_base_url():
    return os.getenv("OLLAMA_BASE_URL", "").strip() or DEFAULT_OLLAMA_BASE_URL


def provider_name():
    return os.getenv("MODEL_PROVIDER", "openrouter").strip()


def model_name(provider=None):
    return os.getenv("MODEL_NAME", "").strip() or DEFAULT_MODELS.get(provider or provider_name(), "")


def valid_configuration():
    if provider_name() == "ollama":
        return True  # Reachability is checked by the provider, not by key presence.
    key = KEY_NAMES.get(provider_name())
    return bool(key and os.getenv(key, "").strip())


def save_preferences(provider, model, enabled):
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=".settings-", delete=False) as output:
            temporary = output.name
            data = {"model_provider": provider, "model_name": model,
                    "web_search_enabled": enabled if provider != "ollama" else False}
            if provider == "ollama":
                data["ollama_base_url"] = ollama_base_url()
            json.dump(data, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def set_web_enabled(enabled):
    if enabled and web_unavailable_reason():
        raise WebSearchUnavailable(web_unavailable_reason())
    save_preferences(provider_name(), model_name(), enabled)
    os.environ["WEB_SEARCH_ENABLED"] = str(enabled).lower()


def _choice(prompt, choices=("1", "2")):
    while True:
        choice = input(prompt).strip()
        if choice in choices:
            return choice
        print("Please choose " + " or ".join(choices) + ".")


def _setup_ollama():
    from models import OllamaProvider, ModelFailure
    print("Checking Ollama...")
    provider = OllamaProvider(base_url=ollama_base_url())
    available = provider.list_models()
    if isinstance(available, ModelFailure):
        print(available.message)
        return False
    print("Ollama is running.")
    if not available:
        print("No Ollama models are installed.\nInstall one, then start Athena again:\nollama pull llama3.2")
        return False
    print("Available models:")
    for index, name in enumerate(available, 1):
        print(f"{index}. {name}")
    selected = _choice("Choose model: ", tuple(str(i) for i in range(1, len(available) + 1)))
    model = available[int(selected) - 1]
    print("Web search is not available in local Ollama mode yet.")
    save_preferences("ollama", model, False)
    os.environ.update(MODEL_PROVIDER="ollama", MODEL_NAME=model, WEB_SEARCH_ENABLED="false")
    print("Setup complete.")
    return True


def ensure_configuration():
    if valid_configuration():
        return True
    print("Welcome to Athena.\nLet's set up your AI provider.")
    try:
        provider = {"1": "openrouter", "2": "gemini", "3": "ollama"}[_choice(
            "Choose your AI provider:\n1. OpenRouter\n2. Gemini\n3. Ollama\nChoice: ", ("1", "2", "3"))]
        if provider == "ollama":
            return _setup_ollama()
        model = model_name(provider) if provider == provider_name() else DEFAULT_MODELS[provider]
        print(f"Model: {model}")
        # Fail closed if the terminal cannot hide credentials; getpass otherwise echoes.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass(f"Enter your {PROVIDER_LABELS[provider]} API key: ").strip()
        if not key or any(ord(char) < 32 for char in key):
            print("Setup was not saved. Enter a valid, nonempty API key.")
            return False
        print(COST_NOTICE)
        enabled = _choice("Allow Athena to access the web when needed?\n1. Yes\n2. No\nChoice: ") == "1"
        key_path = runtime_paths.user_config_dir() / ".env"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        set_key(str(key_path), KEY_NAMES[provider], key)
        save_preferences(provider, model, enabled)
        os.environ.update({"MODEL_PROVIDER": provider, "MODEL_NAME": model,
                           "WEB_SEARCH_ENABLED": str(enabled).lower(), KEY_NAMES[provider]: key})
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled. Start Athena again when you are ready.")
        return False
    except getpass.GetPassWarning:
        print("Hidden key input is unavailable. Run Athena in an interactive terminal or configure .env manually.")
        return False
    except (OSError, UnicodeError):
        print("Settings could not be saved. Check file permissions and try again.")
        return False
    print("Setup complete.")
    return True
