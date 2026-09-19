import re
import json
from pathlib import Path
from .safety import redact_secrets
from .results import ToolResult


DOCUMENT_EXTENSIONS = {".txt", ".md", ".json", ".csv"}


def read_document(path, offset=0, max_chars=12000):
    """Return a chunk of sanitized text and an explicit continuation offset."""
    import tools
    if type(offset) is not int or not 0 <= offset <= tools.MAX_TEXT_BYTES:
        return ToolResult.fail("Invalid arguments: invalid character offset.", "invalid_arguments")
    if type(max_chars) is not int or not 1 <= max_chars <= 12000:
        return ToolResult.fail("Invalid arguments: max_chars must be from 1 to 12000.", "invalid_arguments")
    try:
        file = workspace_path(path)
    except (ValueError, OSError):
        return ToolResult.fail("Access denied: invalid document path.", "invalid_path")
    if file.suffix.casefold() not in DOCUMENT_EXTENSIONS:
        return ToolResult.fail(
            "File error: unsupported document format. Supported: .txt, .md, .json, .csv (UTF-8).",
            "unsupported_format")
    result = read_text_file(path)
    if not result.success:
        return result
    if offset > len(result):
        return ToolResult.fail("Invalid arguments: offset exceeds document length.", "invalid_arguments")
    end = min(offset + max_chars, len(result))
    return ToolResult.ok(json.dumps({
        "text": result[offset:end], "offset": offset,
        "next_offset": end if end < len(result) else None,
        "total_chars": len(result),
    }, ensure_ascii=False))

def workspace_path(path):
    """Reject traversal and links before resolving and checking containment."""
    import tools
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Invalid path: expected a nonempty string.")
    # Check both separators, including Windows paths when tests run elsewhere.
    if ".." in path.replace("\\", "/").split("/"):
        raise ValueError("Access denied: parent traversal is not allowed.")
    if "\x00" in path or ":" in path[2:]:
        raise ValueError("Access denied: invalid path or alternate data stream.")
    root = tools.WORKSPACE_ROOT.resolve()
    candidate = Path(path)
    candidate = candidate if candidate.is_absolute() else root / candidate
    if not candidate.is_relative_to(root):
        raise ValueError("Access denied: path is outside the workspace.")
    current = root
    for part in candidate.relative_to(root).parts:
        # Windows silently removes trailing dots/spaces from many filenames.
        if part != part.rstrip(" ."):
            raise ValueError("Access denied: ambiguous Windows filename.")
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ValueError("Access denied: filesystem links are not allowed.")
        if part.casefold() in tools.IGNORED_DIRECTORIES:
            raise ValueError("Access denied: excluded workspace directory.")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Access denied: path is outside the workspace.")
    return resolved


def list_files(path="."):
    """List one directory without following links or entering excluded folders."""
    import tools
    try:
        directory = workspace_path(path)
        if not directory.exists():
            return ToolResult.fail("File error: directory not found.", "file_error")
        if not directory.is_dir():
            return ToolResult.fail("File error: expected a directory.", "file_error")
        entries = []
        for item in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if item.name.casefold() in tools.IGNORED_DIRECTORIES:
                continue
            if item.is_symlink() or item.is_junction():
                continue
            entries.append(item.name + ("/" if item.is_dir() else ""))
        return ToolResult.ok(redact_secrets("\n".join(entries)) or "Directory is empty.")
    except ValueError as exc:
        return ToolResult.fail(str(exc), "invalid_path")
    except OSError:
        return ToolResult.fail("File error: unable to list directory.", "file_error")


def read_text_file(path):
    """Read bounded UTF-8 text; protected files never reach the model."""
    import tools
    try:
        file = workspace_path(path)
        parts = [part.casefold() for part in file.relative_to(tools.WORKSPACE_ROOT.resolve()).parts]
        if any(part == ".env" or part.startswith(".env.") for part in parts):
            return ToolResult.fail("Access denied: .env cannot be read by the agent.", "Access denied")
        if file.suffix.casefold() in {".pem", ".key", ".p12", ".pfx"} or file.name.casefold() in {
                "id_rsa", "id_ed25519", "credentials.json", "secrets.json"}:
            return ToolResult.fail("Access denied: credential files cannot be read by the agent.", "Access denied")
        if not file.exists():
            return ToolResult.fail("File error: file not found.", "file_error")
        if not file.is_file():
            return ToolResult.fail("File error: expected a regular text file, not a directory.", "file_error")
        if file.stat().st_nlink > 1:
            return ToolResult.fail("Access denied: hard-linked files cannot be read by the agent.", "Access denied")
        if file.stat().st_size > tools.MAX_TEXT_BYTES:
            return ToolResult.fail("File error: text file exceeds the 100 KB limit.", "file_error")
        with file.open("rb") as stream:
            content = stream.read(tools.MAX_TEXT_BYTES + 1)
        if len(content) > tools.MAX_TEXT_BYTES:
            return ToolResult.fail("File error: text file exceeds the 100 KB limit.", "file_error")
        if any(byte < 32 and byte not in (9, 10, 13) for byte in content):
            return ToolResult.fail("File error: binary files are not supported.", "file_error")
        text = content.decode("utf-8-sig")
        if re.search(r"(?m)^-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----\s*$", text):
            return ToolResult.fail("Access denied: file contains private key material.", "Access denied")
        return ToolResult.ok(redact_secrets(text))
    except UnicodeDecodeError:
        return ToolResult.fail("File error: expected UTF-8 text; binary files are not supported.", "file_error")
    except ValueError as exc:
        return ToolResult.fail(str(exc), "invalid_path")
    except OSError:
        return ToolResult.fail("File error: unable to read file.", "file_error")


