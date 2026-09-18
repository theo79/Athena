# my_agent

A local-first AI work assistant built in Python.

my_agent combines LLM reasoning with tools, persistent memory,
experience retrieval, feedback, and permission-controlled actions.

## What it can do

- Answer, summarize, compare, and draft
- Read local documents
- Search the web
- Remember user-approved information
- Learn from past task outcomes and feedback
- Use tools through an explicit permission layer
- Support OpenRouter and Google Gemini

my_agent is evolving into an AI work assistant. This release supports reading,
searching, summarizing, comparing, drafting and decision support with a bounded
five-step agent loop. It adds a Python permission boundary for tool execution.
It does not implement connected work services or autonomous background work.

Use Python 3.10 or newer. Create a virtual environment with `python -m venv .venv`
and activate it (`.venv\Scripts\Activate.ps1` in PowerShell, or
`source .venv/bin/activate` on macOS/Linux). Install dependencies with
`python -m pip install -r requirements.txt`.
Copy `.env.example` to `.env`, fill in your provider key, then run `python agent.py`.
OpenRouter and Gemini configuration is unchanged. Keep `.env` private; local
memory and experience stores are created at runtime and are excluded from Git.

## Commands

- `/tools`: list the seven tools, their permission categories and approval policy.
- `/status`: version, provider, model, debug mode, memory/experience availability
  and tool count. Availability means the feature is enabled, not a storage health check.
- `/help`, `/history`, `/clear`, `/good`, `/bad`, `/neutral`, `exit`: unchanged.

Normal output stays quiet except for answers, commands and required approval
prompts. Set `MY_AGENT_DEBUG=true` for opt-in diagnostics. Approval prompts remain
visible in both modes.

## Tools and permissions

| Category | Use | Confirmation |
| --- | --- | --- |
| `READ_ONLY` | Local reads, time, memory search | Normally none |
| `WRITE_LOCAL` | Local modification, currently `save_memory` | Required |
| `EXTERNAL_READ` | Web search and future connected reads | Normally none |
| `EXTERNAL_WRITE` | Future external modifications | Required |
| `SENSITIVE_ACTION` | Future sensitive operations | Required |

Any read tool may also set `requires_confirmation=True`. A write tool cannot
disable confirmation by setting that field to false. Unknown permission values
fail closed. `NETWORK_READ` remains an import alias for `EXTERNAL_READ`.

The registry in `tool_modules/registry.py` uses a `ToolDefinition` TypedDict and
ordinary dictionaries to preserve the v0.10 interface. The dictionary key is the
tool name; each entry has a description, parameter types/constraints, permission,
confirmation flag and handler. The system prompt and `/tools` use this registry.

`agent.execute_tool` validates arguments, checks policy, requests approval when
needed, and only then invokes the handler. Model JSON cannot supply approval:
extra argument fields are rejected and top-level policy/approval fields are
discarded. Each call requires its own decision. No approval is cached.

`run_agent(..., confirmation=callback)` supports a trusted host UI. The callback
receives an immutable `ProposedAction` containing the tool, description and
sanitized JSON arguments. Only the literal boolean `True` authorizes execution.
Without a callback, writes are denied. The CLI callback accepts `y` or `yes`,
case-insensitively; empty input, other responses, EOF and interruption reject.
Known secrets are redacted and terminal control characters are escaped in the
argument preview. Sanitization is best effort; previews are not truncated.

Permission enforcement applies to model-dispatched calls. Public Python handlers
such as `tools.save_memory` remain callable by trusted application code for
backward compatibility; they are not a sandbox against arbitrary Python code.
Automatic experience recording, feedback and storage recovery retain their
existing behavior and do not prompt as model-selected actions.

## Document reading

`read_document(path, offset=0, max_chars=12000)` supports UTF-8 `.txt`, `.md`,
`.json` and `.csv`, including UTF-8 BOM. JSON and CSV are returned as text, not
interpreted as commands, formulas or structured queries. Extension matching is
case-insensitive. Line endings are preserved.

Files must be in the workspace and at most 100 KiB (102,400 bytes). The shared
reader blocks traversal, paths outside the workspace, filesystem links,
hard-linked files, excluded directories, known credential filenames, private-key
material, invalid UTF-8 and binary control bytes. Errors return useful text and
failure metadata. Secrets are redacted before slicing the text.

Results contain `text`, `offset`, `next_offset` and `total_chars` as JSON.
Offsets count characters in the sanitized text, not source bytes. `next_offset`
is null at the end. Each chunk is at most 12,000 characters; larger files within
the input limit require continuation calls. Files over the input limit are
rejected rather than silently truncated. `read_text_file` keeps its original
interface, broader UTF-8 file support, and 100 KiB limit.

PDF and DOCX are not supported in this release. They require additional parsers
and format-specific resource limits (including decompression and extracted-text
limits). The deliberately smaller text-only foundation keeps installation
unchanged and reuses the existing security checks. No OCR, spreadsheet editing
or unrestricted file writing is provided. Files should remain stable while being
read; the reader does not guarantee protection against concurrent filesystem
replacement by another local process. The five-step budget can prevent reading
every chunk; the assistant is instructed to disclose partial summaries.

## Architecture and compatibility

`tools.py` remains the public compatibility facade and owns configurable memory
and workspace paths. Implementations live in `tool_modules/local_files.py`,
`memory.py`, `web.py`, `safety.py`, `confirmation.py` and `results.py`. This avoids
changing existing imports and storage locations while keeping handler groups
separate. `tools.py` is intentionally not replaced by a same-named package.

`ToolResult` is a string subclass with `success`, `content` and `error_type`.
Existing string comparisons and model messages continue to work. Refactored
readers, memory and web tools supply explicit outcomes. Experience recording uses
those outcomes, so text starting with `Tool failure:` is still successful document
content. A small legacy adapter retains prefix inference for old string-only
handlers and callers.

Conversation history, provider error handling, quality states, feedback,
experience retrieval and storage safety remain in place. Existing `memory.json`
and `experiences.json` need no migration. See [EXPERIENCE.md](EXPERIENCE.md) for
storage recovery, historical evidence and feedback behavior.

For a future integration, add a handler module and registry entry with an explicit
schema and permission. Use `EXTERNAL_READ` for retrieval and `EXTERNAL_WRITE` for
modifications. Return `ToolResult` with an explicit outcome. Keep drafting and
execution as separate operations; preparing an email or event must not send or
create it. Gmail, Drive, Calendar, OAuth and real sending are intentionally absent.

## Examples

1. **Read-only:** “List the workspace files.” The model selects `list_files`;
   Python validates its arguments and executes without approval.
2. **Write:** “Remember that I prefer short reports.” Before `save_memory` runs:

   ```text
   Proposed action: save_memory
   Save information when the user explicitly asks to remember it.
   Arguments: {"text": "I prefer short reports"}
   Approve? [y/N]
   ```

   `y` saves the memory; `n` or Enter leaves memory unchanged.
3. **Summary:** “Read report.md and summarize the risks.” The model selects
   `read_document`, follows continuation offsets if needed and uses the returned
   text to summarize. The prompt requires facts and analysis to be distinguished.

## Tests

The suite is offline and uses isolated stores and fake providers. Install the
development dependencies and run the full suite from the project root:

```sh
python -m pip install ".[dev]"
python -m pytest -q
```

`test_work_assistant.py` covers permission classification, fail-closed execution,
approval/rejection, model bypass attempts, safe previews, CLI introspection,
document formats/chunks/limits and summary/approval workflows. Existing tests
continue to cover providers, quiet/debug output, persistent memory, experience,
feedback, retrieval quality and storage recovery. These tests do not measure live
LLM answer quality or make live provider calls.
## Current release

v0.11 — Work Assistant Foundations
