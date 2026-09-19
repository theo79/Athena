import json
import search_provider
from .safety import redact_secrets
from .results import ToolResult

def web_search(query, max_results=5):
    """Search only. Return bounded JSON data, never execute external content."""
    import tools
    if not isinstance(query, str) or not query.strip():
        return ToolResult.fail("Invalid arguments: 'query' must be a nonempty string.", "invalid_arguments")
    if len(query) > tools.MAX_SEARCH_QUERY_LENGTH:
        return ToolResult.fail(f"Invalid arguments: 'query' must be at most {tools.MAX_SEARCH_QUERY_LENGTH} characters.", "invalid_arguments")
    if type(max_results) is not int or not 1 <= max_results <= 10:
        return ToolResult.fail("Invalid arguments: 'max_results' must be an integer from 1 to 10.", "invalid_arguments")
    try:
        results = search_provider.search(query.strip(), max_results)
    except search_provider.SearchError as exc:
        return ToolResult.fail(str(exc), "web_search_unavailable")
    # Redact each field before encoding, preserving valid JSON.
    def sanitize(value):
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        return redact_secrets(value)
    return ToolResult.ok(json.dumps(sanitize(results), ensure_ascii=False))


