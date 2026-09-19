import re
from datetime import datetime
from .results import ToolResult
from json_storage import load_list, write_list

def load_memory():
    import tools
    memory = load_list(tools.MEMORY_FILE)
    return [item for item in memory
            if isinstance(item, dict) and isinstance(item.get("text"), str)]


def save_memory(text: str):
    import tools
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a nonempty string")
    memory = load_memory()

    memory.append({
        "text": text,
        "saved_at": datetime.now().isoformat(timespec="seconds")
    })

    write_list(tools.MEMORY_FILE, memory)

    return ToolResult.ok("Memory saved successfully.")


def search_memory(query: str):
    import tools
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a nonempty string")
    memory = load_memory()
    words = set(re.findall(r"\w+", query.casefold()))

    results = []

    for item in memory:
        text = item.get("text", "")

        if query.casefold() in text.casefold() or words.intersection(
                re.findall(r"\w+", text.casefold())):
            results.append(text)

    if not results:
        return ToolResult.ok("No matching memories found.")

    return ToolResult.ok("\n".join(results))
