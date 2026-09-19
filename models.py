"""Replaceable model infrastructure; no agent or tool orchestration lives here."""
import os
import re
from dataclasses import dataclass
from typing import Protocol
import httpx
from google import genai
from google.genai import errors, types
from settings import DEFAULT_MODELS, model_name, ollama_base_url

from openai import (OpenAI, RateLimitError, AuthenticationError,
                    APIConnectionError, APITimeoutError, APIStatusError)


@dataclass(frozen=True)
class ModelFailure:
    """Safe message kept separate from model text and parser failures."""
    message: str


class ModelProvider(Protocol):
    def generate(self, messages: list[dict]) -> str | None | ModelFailure:
        ...


class ModelConfigurationError(ValueError):
    """Invalid provider selection; never silently fall back."""


class OpenRouterProvider:
    provider_name = "openrouter"

    def __init__(self, model_name=DEFAULT_MODELS["openrouter"]):
        self.model_name = model_name
        self.client = None

    def generate(self, messages):
        if self.client is None:
            api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
            if not api_key:
                return ModelFailure("Model provider authentication failed. OPENROUTER_API_KEY is not configured.")
            self.client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key,
                                 max_retries=0)
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages
            )
        except RateLimitError:
            return ModelFailure("Model provider rate limit reached. Try again after the "
                                "provider reset or configure another model/provider.")
        except AuthenticationError:
            return ModelFailure("Model provider authentication failed. Check the configured API key.")
        except APITimeoutError:  # A subclass of APIConnectionError: handle it first.
            return ModelFailure("Model provider request timed out.")
        except APIConnectionError:
            return ModelFailure("Could not connect to the model provider.")
        except APIStatusError as exc:
            return ModelFailure(f"Model provider returned an API error: {exc.status_code}")

        return response.choices[0].message.content


class GeminiProvider:
    provider_name = "gemini"

    def __init__(self, model_name=DEFAULT_MODELS["gemini"]):
        self.model_name = model_name
        self.client = None

    def generate(self, messages):
        if self.client is None:
            api_key = os.getenv("GEMINI_API_KEY", "").strip()
            if not api_key:
                return ModelFailure("Model provider authentication failed. GEMINI_API_KEY is not configured.")
            self.client = genai.Client(
                api_key=api_key, vertexai=False,
                http_options=types.HttpOptions(
                    timeout=30000, retry_options=types.HttpRetryOptions(attempts=1)))

        # Keep our conversation format independent of the SDK. Tool results
        # already arrive as user messages in the existing custom agent loop.
        instructions = []
        contents = []
        for message in messages:
            role, text = message["role"], message["content"]
            if role == "system":
                instructions.append(text)
            elif role in ("user", "assistant"):
                contents.append(types.Content(
                    role="model" if role == "assistant" else "user",
                    parts=[types.Part.from_text(text=text)]))
            else:
                raise ValueError("Unsupported internal message role")
        try:
            response = self.client.models.generate_content(
                model=self.model_name, contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction="\n\n".join(instructions) or None,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
        except errors.APIError as exc:
            if exc.code == 429:
                return ModelFailure("Model provider rate limit reached. Try again after the "
                                    "provider reset or configure another model/provider.")
            if exc.code in (401, 403):
                return ModelFailure("Model provider authentication failed. Check the configured API key.")
            if exc.code in (408, 504):
                return ModelFailure("Model provider request timed out.")
            return ModelFailure(f"Model provider returned an API error: {exc.code}")
        except httpx.TimeoutException:
            return ModelFailure("Model provider request timed out.")
        except httpx.TransportError:
            return ModelFailure("Could not connect to the model provider.")
        if not response.text:
            return ModelFailure("Model provider returned no text (possibly blocked or empty response).")
        return response.text


class OllamaProvider:
    """Native HTTP chat; Athena retains tool execution and JSON action parsing."""
    provider_name = "ollama"
    CHAT_TIMEOUT = httpx.Timeout(120.0, connect=5.0)
    DISCOVERY_TIMEOUT = httpx.Timeout(3.0)

    def __init__(self, model_name=DEFAULT_MODELS["ollama"], base_url=None):
        self.model_name = model_name
        self.base_url = (base_url if base_url is not None else ollama_base_url()).rstrip("/")

    def _valid_url(self):
        try:
            url = httpx.URL(self.base_url)
            return (url.scheme in ("http", "https") and bool(url.host)
                    and not url.userinfo and not url.query and not url.fragment)
        except (httpx.InvalidURL, ValueError):
            return False

    @staticmethod
    def _valid_model(name):
        return isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", name) is not None

    def _request(self, endpoint, payload=None):
        if not self._valid_url():
            return ModelFailure("Ollama base URL is invalid. Set OLLAMA_BASE_URL to an HTTP or HTTPS address without credentials or query parameters.")
        timeout = self.CHAT_TIMEOUT if payload is not None else self.DISCOVERY_TIMEOUT
        try:
            # Local requests must not inherit a cloud proxy or follow redirects.
            with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
                response = (client.post(self.base_url + endpoint, json=payload) if payload is not None
                            else client.get(self.base_url + endpoint))
            if response.status_code == 404 and payload is not None:
                return ModelFailure(f"Ollama model '{self.model_name}' is not installed.\nInstall it with:\nollama pull {self.model_name}")
            if not response.is_success:
                return ModelFailure("Ollama could not complete the request. Check that the server and model are available, then try again.")
            data = response.json()
        except httpx.TimeoutException:
            return ModelFailure("Ollama request timed out. The model may still be loading; please try again.")
        except httpx.TransportError:
            return ModelFailure(f"Ollama is not reachable at {self.base_url}.\nInstall or start Ollama, then try again. You can start the server with: ollama serve")
        except (ValueError, UnicodeError):
            return ModelFailure("Ollama returned an invalid response. Please try again.")
        if not isinstance(data, dict) or "error" in data:
            return ModelFailure("Ollama returned an invalid response. Please try again.")
        return data

    def generate(self, messages):
        if not self._valid_model(self.model_name):
            return ModelFailure("Ollama model name is invalid. Set MODEL_NAME to an installed model name.")
        # Athena already uses Ollama-compatible system/user/assistant roles.
        converted = [{"role": item["role"], "content": item["content"]} for item in messages]
        data = self._request("/api/chat", {"model": self.model_name, "messages": converted,
                                           "stream": False, "format": "json"})
        if isinstance(data, ModelFailure):
            return data
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if (not isinstance(content, str) or not content.strip()
                or message.get("role") != "assistant" or data.get("done") is not True):
            return ModelFailure("Ollama returned an invalid or empty response. Please try again.")
        return content

    def list_models(self):
        data = self._request("/api/tags")
        if isinstance(data, ModelFailure):
            return data
        items = data.get("models")
        if not isinstance(items, list) or any(not isinstance(item, dict)
                or not self._valid_model(item.get("name")) for item in items):
            return ModelFailure("Ollama returned an invalid model list. Please try again.")
        return list(dict.fromkeys(item["name"] for item in items))

    def status_lines(self):
        available = self.list_models()
        state = "unavailable" if isinstance(available, ModelFailure) else "connected"
        # Never render credentials from a malformed URL.
        url = self.base_url if self._valid_url() else "invalid (check OLLAMA_BASE_URL)"
        lines = f"Ollama: {state}\nBase URL: {url}\n"
        if isinstance(available, ModelFailure):
            lines += available.message + "\n"
        return lines


def get_model_provider() -> ModelProvider:
    """Read configuration loaded by the application; construct only one provider."""
    name = os.getenv("MODEL_PROVIDER", "openrouter").strip()
    if name == "openrouter":
        return OpenRouterProvider(model_name=model_name(name))
    if name == "gemini":
        return GeminiProvider(model_name=model_name(name))
    if name == "ollama":
        return OllamaProvider(model_name=model_name(name), base_url=ollama_base_url())
    raise ModelConfigurationError(f"Unsupported model provider: {name}")
