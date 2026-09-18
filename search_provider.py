"""Tavily adapter. The agent only sees search(query, max_results) and normalized data."""
import json
import os
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 512 * 1024
FIELD_LIMITS = {"title": 200, "url": 1000, "snippet": 600}


class SearchError(Exception):
    """A safe, user-facing provider error (never raw response bodies or headers)."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def search(query, max_results):
    """Make one basic search request; do not fetch result URLs or retry requests."""
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        raise SearchError("Web search unavailable: TAVILY_API_KEY is not configured.")
    request = Request(
        "https://api.tavily.com/search",
        data=json.dumps({"query": query, "max_results": max_results,
                         "search_depth": "basic", "auto_parameters": False,
                         "include_answer": False, "include_raw_content": False,
                         "include_images": False}).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(NoRedirects()).open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        status = exc.code
        exc.close()
        if status == 429:
            raise SearchError("Web search unavailable: provider rate limit reached.") from None
        if status in (401, 403):
            raise SearchError("Web search unavailable: provider rejected authentication.") from None
        raise SearchError(f"Web search unavailable: provider HTTP error {status}.") from None
    except TimeoutError:
        raise SearchError("Web search unavailable: request timed out.") from None
    except URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise SearchError("Web search unavailable: request timed out.") from None
        raise SearchError("Web search unavailable: could not connect to provider.") from None
    except (OSError, HTTPException):
        raise SearchError("Web search unavailable: network response failed.") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise SearchError("Web search unavailable: provider response is too large.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SearchError("Web search unavailable: invalid provider response.") from None
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise SearchError("Web search unavailable: invalid provider response.")
    results = []
    for item in data["results"][:max_results]:
        if not isinstance(item, dict) or any(
                not isinstance(item.get(field), str) for field in ("title", "url", "content")):
            raise SearchError("Web search unavailable: invalid provider result.")
        results.append({
            "title": item["title"][:FIELD_LIMITS["title"]],
            "url": item["url"][:FIELD_LIMITS["url"]],
            "snippet": item["content"][:FIELD_LIMITS["snippet"]],
        })
    return results
