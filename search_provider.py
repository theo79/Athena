"""Provider-native search adapters returning bounded, provider-independent data."""
import os
from urllib.parse import urlsplit

import httpx
from google import genai
from google.genai import errors, types
from openai import OpenAI, APIError
import settings


class SearchError(Exception):
    """Safe public error; never contains a provider response or credential."""


AUTH_ERROR = "Your provider API key appears to be invalid or missing."
API_ERROR = "Web search could not be completed. Please try again."
UNSUPPORTED = "Web search is not available with the current model."


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _normalize(answer, sources, provider, max_results):
    if not isinstance(answer, str) or not answer.strip():
        raise SearchError(API_ERROR)
    normalized = []
    seen = set()
    for source in sources:
        url = _field(source, "url") or _field(source, "uri")
        if not isinstance(url, str) or len(url) > 2000:
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ("https", "http") or not parsed.netloc:
                continue
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        title = _field(source, "title")
        normalized.append({"title": title[:200] if isinstance(title, str) else url, "url": url})
        if len(normalized) >= max_results:
            break
    return {"answer": answer[:12000], "sources": normalized, "provider": provider, "success": True}


def _openrouter(query, max_results, key, model):
    with OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key,
                max_retries=0, timeout=30.0) as client:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Search the web and give a concise, sourced answer to: " + query}],
            extra_body={"plugins": [{"id": "web", "max_results": max_results}]})
    if not response.choices:
        raise SearchError(API_ERROR)
    message = response.choices[0].message
    citations = [_field(item, "url_citation", {}) for item in (_field(message, "annotations", []) or [])
                 if _field(item, "type") == "url_citation"]
    return _normalize(message.content, citations, "openrouter", max_results)


def _gemini(query, max_results, key, model):
    with genai.Client(api_key=key, vertexai=False, http_options=types.HttpOptions(
            timeout=30000, retry_options=types.HttpRetryOptions(attempts=1))) as client:
        response = client.models.generate_content(
            model=model, contents="Search the web and give a concise, sourced answer to: " + query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
    sources = []
    for candidate in response.candidates or []:
        metadata = _field(candidate, "grounding_metadata")
        for chunk in _field(metadata, "grounding_chunks", []) or []:
            web = _field(chunk, "web")
            if web:
                sources.append(web)
    return _normalize(response.text, sources, "gemini", max_results)


def search(query, max_results):
    if settings.web_unavailable_reason():
        raise SearchError(settings.web_unavailable_reason())
    if not settings.web_enabled():
        raise SearchError("Web search is disabled in Athena settings.")
    provider = settings.provider_name()
    key = os.getenv(settings.KEY_NAMES.get(provider, ""), "").strip()
    if not key:
        raise SearchError(AUTH_ERROR)
    try:
        adapter = {"openrouter": _openrouter, "gemini": _gemini}.get(provider)
        if adapter is None:
            raise SearchError(UNSUPPORTED)
        return adapter(query, max_results, key, settings.model_name(provider))
    except (APIError, errors.APIError) as exc:
        code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if code in (401, 403):
            raise SearchError(AUTH_ERROR) from None
        if code in (400, 404, 422):
            # Provider payload is inspected only to classify; never returned or logged.
            detail = str(exc).lower()
            if any(word in detail for word in ("api key", "api_key", "unauthenticated")):
                raise SearchError(AUTH_ERROR) from None
            if any(word in detail for word in ("not supported", "unsupported", "not available", "not found")):
                raise SearchError(UNSUPPORTED) from None
        raise SearchError(API_ERROR) from None
    except (httpx.TransportError, OSError):
        raise SearchError(API_ERROR) from None
