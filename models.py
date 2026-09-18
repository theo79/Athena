"""Replaceable model infrastructure; no agent or tool orchestration lives here."""
import os
from dataclasses import dataclass
from typing import Protocol
import httpx
from google import genai
from google.genai import errors, types

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

    def __init__(self, model_name="openrouter/free"):
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

    def __init__(self, model_name="gemini-3.1-flash-lite"):
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


def get_model_provider() -> ModelProvider:
    """Read configuration loaded by the application; construct only one provider."""
    name = os.getenv("MODEL_PROVIDER", "openrouter").strip()
    if name == "openrouter":
        return OpenRouterProvider(model_name=os.getenv("MODEL_NAME", "openrouter/free"))
    if name == "gemini":
        return GeminiProvider(model_name=os.getenv("MODEL_NAME", "gemini-3.1-flash-lite"))
    raise ModelConfigurationError(f"Unsupported model provider: {name}")
