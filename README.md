<<<<<<< HEAD
# Athena

![Athena](athena.png)

**Athena is a local-first AI work assistant built in Python.**

Athena combines LLM reasoning with tools, persistent memory, experience retrieval, feedback, and permission-controlled actions.

The goal is simple:

> Help users complete useful work, not just answer questions.

## What Athena can do

- Answer questions, summarize, compare, and draft
- Read local documents
- Search the web
- Remember user-approved information
- Reuse relevant past experiences
- Adapt retrieval using user feedback
- Use tools through an explicit permission layer
- Support OpenRouter and Google Gemini
- Provide decision support while keeping the user in control

## Current release

**v0.11 — Work Assistant Foundations**

This release focuses on the foundations needed for a safe, extensible work assistant.

Athena currently supports reading, searching, summarizing, comparing, drafting, and decision support through a bounded five-step agent loop.

It also adds a Python-enforced permission boundary for tool execution.

Connected services such as Gmail, Google Drive, and Calendar are not implemented yet, and Athena does not run autonomous background tasks.

---

## Quick start

Athena requires **Python 3.10 or newer**.

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it.

### Windows PowerShell

```powershell
.venv\Scripts\Activate.ps1
```

### macOS / Linux

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Copy the example environment file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Add your provider API key to `.env`.

Then run:

```bash
python agent.py
```

Example:

```text
Athena v0.11
Ready.

You > summarize the main points in report.md

Athena > ...
```

OpenRouter and Gemini are currently supported.

Keep `.env` private. Runtime memory and experience files are created locally and are excluded from Git.

---

## Configuration

Example `.env`:

```env
MODEL_PROVIDER=openrouter
MODEL_NAME=openrouter/free
MY_AGENT_DEBUG=false

OPENROUTER_API_KEY=
GEMINI_API_KEY=
TAVILY_API_KEY=
```

For Gemini:

```env
MODEL_PROVIDER=gemini
MODEL_NAME=gemini-3.1-flash-lite
```

`MY_AGENT_DEBUG=true` enables development diagnostics.

The `MY_AGENT_DEBUG` name is retained for backward compatibility.

---

## Commands

Athena includes a small CLI command set:

| Command | Purpose |
| --- | --- |
| `/tools` | Show available tools, permissions, and approval requirements |
| `/status` | Show version, provider, model, debug status, and enabled systems |
| `/history` | Show recent conversation history |
| `/clear` | Clear the current conversation and feedback target |
| `/good` | Mark the latest eligible experience as user-validated |
| `/bad` | Mark it as user-rejected |
| `/neutral` | Return it to unverified quality |
| `/help` | Show available commands |
| `exit` | Exit Athena |

Normal output stays quiet except for answers, commands, and required approval prompts.

---
=======
# Athena v0.12

Athena is a local-first AI work assistant for reading local documents, remembering
approved preferences, searching the web when enabled, and helping summarize,
compare, analyze and draft. Settings and records stay on your machine. Choosing a
cloud provider sends the task and relevant context to that provider.

## Choose one provider

| Provider | Runs | API key | Optional Athena web search |
| --- | --- | --- | --- |
| OpenRouter | Cloud | One OpenRouter key | OpenRouter web plugin |
| Gemini | Cloud | One Gemini key | Google Search grounding |
| Ollama | Local server | None | Unavailable |

You do not need both cloud providers or multiple API keys. No separate search
service account is needed. Web search defaults to disabled; cloud searches reuse
your chosen provider's key and may count toward its API usage or billing.

## Start Athena

### From source

Use Python **3.12 or newer**. The current code uses Python 3.12 filesystem APIs.
From the project directory:

```sh
python -m venv .venv
```

Activate with `.venv\Scripts\Activate.ps1` in PowerShell or
`source .venv/bin/activate` on macOS/Linux, then run:

```sh
python -m pip install -r requirements.txt
python agent.py
```

### Windows executable

Extract the complete Windows ZIP, open a terminal in its folder, and run
`.\Athena.exe`. Python is not required. See [WINDOWS_README.md](WINDOWS_README.md)
for storage and configuration details; [BUILD.md](BUILD.md) explains packaging.

### First run

1. Choose OpenRouter, Gemini or Ollama.
2. For a cloud provider, enter its API key using hidden input.
3. Separately choose whether cloud web search is allowed.
4. For Ollama, Athena checks the server and asks you to select an installed model;
   no API key is requested, and web search stays unavailable.
5. Start working. Use `/help` for commands or `exit` to quit.

Existing valid configuration skips setup. Cloud setup checks key presence, not
live authentication. Hidden input requires an interactive terminal; Ctrl+C
cancels setup. Manual configuration is also supported.

For Ollama, install and start [Ollama](https://ollama.com) yourself, then install a
model, for example `ollama pull llama3.2`. If necessary start the server with
`ollama serve`. Athena reports an unreachable server or an empty model list and
never installs Ollama or downloads models automatically.

## Commands

| Command | Purpose |
| --- | --- |
| `/help` | Show available commands |
| `/status` | Show provider, model, web access, debug and storage-feature availability; check Ollama connectivity |
| `/settings` | Inspect current settings |
| `/settings web on` | Enable cloud web search; rejected for Ollama |
| `/settings web off` | Disable web search |
| `/tools` | List tools and permission requirements |
| `/history` | Show bounded conversation history |
| `/clear` | Clear conversation context and the feedback target, not persistent records |
| `/good`, `/bad`, `/neutral` | Rate the last recorded task |
| `exit` | Exit Athena |

Provider switching remains manual. Status availability is not a storage health
check. Normal output stays concise; `MY_AGENT_DEBUG=true` enables diagnostics.
The legacy debug variable name is retained for compatibility.
>>>>>>> 7a14887 (Release Athena v0.12 with Ollama and native web search)

## Memory, experiences and permissions

<<<<<<< HEAD
Athena separates **model decisions** from **execution authority**.

The LLM may request a tool, but Python validates permissions before the tool executes.

| Category | Use | Confirmation |
| --- | --- | --- |
| `READ_ONLY` | Local reads, time, memory search | Normally none |
| `WRITE_LOCAL` | Local modifications such as saving memory | Required |
| `EXTERNAL_READ` | Web search and future connected reads | Normally none |
| `EXTERNAL_WRITE` | Future external modifications | Required |
| `SENSITIVE_ACTION` | Future sensitive operations | Required |

Write-capable actions cannot disable confirmation.

Unknown permission values fail closed.

A read tool may also explicitly require confirmation if needed.

---

## Confirmation model

Actions that modify state require explicit approval.

For example:

```text
You > Remember that I prefer short reports

Proposed action: save_memory
Save information when the user explicitly asks to remember it.
Arguments: {"text": "I prefer short reports"}
Approve? [y/N]
```

Only an explicit `y` or `yes` authorizes the action in the CLI.

Pressing Enter, answering no, reaching EOF, or interrupting the prompt rejects the action.

The model cannot approve its own action.

Model-provided approval fields are ignored or rejected.

Each action requires its own approval decision.

---

## Tool architecture

Athena uses a typed registry for tool metadata.

Each tool can define:

- name
- description
- argument schema
- permission category
- confirmation requirement
- handler

The registry is used by both the model-facing system prompt and the `/tools` command.

The tool implementation is split into modules under:

```text
tool_modules/
├── confirmation.py
├── local_files.py
├── memory.py
├── registry.py
├── results.py
├── safety.py
└── web.py
```

`tools.py` remains the compatibility facade so existing imports continue to work.

---

## Document reading

Athena currently supports:

- `.txt`
- `.md`
- `.json`
- `.csv`

through:

```python
read_document(path, offset=0, max_chars=12000)
```

Supported files must:

- remain inside the configured workspace
- be no larger than 100 KiB
- contain valid UTF-8 text
- pass path and credential safety checks

The reader blocks:

- path traversal
- files outside the workspace
- symbolic links
- hard-linked files
- excluded directories
- known credential filenames
- private-key material
- invalid UTF-8
- binary control bytes

Secrets are redacted before text is returned to the model.

Large supported files are read in bounded chunks using continuation offsets.

Athena does **not** currently support:

- PDF
- DOCX
- OCR
- spreadsheet editing
- unrestricted file writing

These are intentionally deferred until format-specific limits and parsers are added safely.

---

## Memory

Athena has persistent local memory.

Memory is stored in `memory.json` and survives restarts.

The assistant is instructed to save information only when the user explicitly asks it to remember something.

Model-selected memory writes require confirmation.

Example:

```text
You > Remember that I prefer concise technical reports.
```

Athena proposes the memory write and asks for approval before storing it.

---

## Experience system

Athena automatically records compact metadata about completed tasks.

Experience records can include:

- task
- provider and model
- steps used
- tools used
- outcome
- feedback
- quality state
- error category
- lesson placeholders for future versions

The current system distinguishes:

```text
completed but unverified
user validated
user rejected
execution failed
```

A completed response is **not automatically considered correct**.

Past experiences are treated as evidence, not truth.

---

## Experience retrieval

Before a new task, Athena may retrieve up to three relevant past experiences.

Retrieval currently uses deterministic lexical similarity with:

- weighted token overlap
- relevance thresholds
- duplicate suppression
- quality adjustments
- feedback adjustments
- execution outcome
- recency as a tie-breaker

Rejected or failed experiences may still be retrieved as cautionary evidence.

Historical final answers and raw tool outputs are not automatically replayed into the model context.

See [EXPERIENCE.md](EXPERIENCE.md) for the detailed experience and storage design.

---

## Feedback

After a task, the user can rate the latest eligible experience:

```text
/good
/bad
/neutral
```

Feedback replaces the previous rating rather than accumulating.

Feedback affects future experience ranking and quality state.

This is **not model training or reinforcement learning**.

Athena adapts through external memory and experience retrieval around the base LLM.

---

## Storage safety

Local JSON stores use safer persistence behavior, including:

- atomic writes
- previous-generation backups
- corruption preservation/quarantine
- recovery from valid backups when available

Runtime files are excluded from Git.

Concurrent writers are not currently supported.

---

## Web search

Athena supports web search through Tavily.

A Tavily API key is optional unless web search is needed.

Web search is classified as:

```text
EXTERNAL_READ
```

and normally does not require confirmation.

---

## Providers

Athena currently supports:

- OpenRouter
- Google Gemini

The provider is selected through `.env`.

The project is intentionally provider-agnostic so additional providers, including local models, can be added later.

---

## Decision support

Athena is designed to help with decisions, not silently make them on the user's behalf.

It should:

- distinguish facts from analysis
- explain important tradeoffs
- show the basis for recommendations
- avoid presenting subjective judgments as objective facts
- keep final authority with the user

Example:

```text
You > Which proposal is better?

Athena >
Proposal A has the lower cost.
Proposal B has stronger warranty coverage.

If minimizing upfront cost is the priority, A aligns better.
If long-term support is the priority, B aligns better.
```

---

## Drafting versus execution

Athena treats these as separate concepts:

```text
drafting something
≠
executing or sending it
```

For future connected services:

```text
draft email
```

must remain separate from:

```text
send email
```

Likewise:

```text
prepare calendar event
```

must remain separate from:

```text
create calendar event
```

Actions that affect external systems will require explicit permission.

---

## Planned direction

Athena is evolving toward a practical AI work assistant.

Planned areas include:

- Gmail read/search
- Google Drive read/search
- Google Calendar integration
- PDF and DOCX reading
- local LLM support
- semantic experience retrieval
- reusable lessons
- richer document workflows
- permission-controlled external actions

The goal is to let users give Athena **work**, not just questions.

---

## Example workflows

### Read-only

```text
You > List the workspace files.
```

Athena selects `list_files`, Python validates the request, and the tool executes without approval.

### Local document summary

```text
You > Read report.md and summarize the risks.
```

Athena uses `read_document`, reads the document in bounded chunks if needed, and summarizes the result.

### Memory write

```text
You > Remember that I prefer short reports.
```

Athena proposes `save_memory`, shows the sanitized action, and executes only after approval.

---

## Tests

The test suite is offline and uses isolated stores and fake providers.
=======
Conversation history is temporary and bounded. Persistent memory is separate:
ask Athena to remember something, then approve the `save_memory` confirmation.
Keyword search retrieves previously saved memory. There is no vector database.

Experiences are compact, automatically recorded task histories. Deterministic
lexical retrieval selects relevant past records; feedback records your assessment
without training the model. A completed task is not automatically considered
correct. See [EXPERIENCE.md](EXPERIENCE.md) for scoring, record fields and recovery.

Local reads and web search normally need no per-call confirmation. Local writes
(`save_memory`) require approval. External writes and sensitive actions are
reserved permission categories that also require approval. Unknown permissions
fail closed. Model text cannot approve an action. Automatic experience recording
and storage recovery retain their existing behavior without approval prompts.

A request such as “Remember that I prefer brief reports” produces an action
preview. Enter `y` or `yes` to save; Enter or another response rejects it. Approval
is per action. These controls apply to model-dispatched tools; they are not a
sandbox for arbitrary Python code or other programs on your machine.

## Documents and web search

The seven tools cover local time, file listing, text/document reading, memory
save/search and web search. Local files must be within the source directory (or
the executable's directory in packaged mode), at most 100 KiB, and valid UTF-8.
`read_document` supports `.txt`, `.md`, `.json` and `.csv` with chunks of up to
12,000 characters. Longer documents require continuation reads. Filesystem links,
traversal, known credential files and binary content are blocked.

For example, ask “Read report.md and summarize the risks.” A bounded five-step
loop may prevent complete reading; answers should disclose partial coverage.
PDF, DOCX, OCR and spreadsheet editing are not supported.

When enabled, `web_search` returns bounded text and source titles/URLs where
available. Search is activated only on search requests; normal chat requests do
not add search tools. Results are untrusted external content. Provider model
support, billing and source metadata vary; failures return friendly messages.
Gemini decides whether grounding requires an actual search. Athena does not fetch
source URLs itself. Ollama never falls back to another provider for web search.

## Configuration and private data

First-run preferences are saved to `%LOCALAPPDATA%\Athena\config.json`.
Cloud keys use `%LOCALAPPDATA%\Athena\.env` in plaintext; keep that folder private.
Ollama needs no `.env`. If LOCALAPPDATA is unavailable, Athena uses
`AppData\Local\Athena` under your home directory.

| Data | Packaged executable | Source execution |
| --- | --- | --- |
| Preferences and setup keys | User Athena directory | Same user Athena directory |
| Memory, experiences, backups and recovery files | User Athena directory | Beside source files (legacy behavior) |
| Document workspace | Executable directory | Source directory |

Source-mode stores are private and Git-ignored. Do not upload them. This audit
preserves that behavior; moving source storage to the user directory requires a
separate migration. Run only one Athena process per data directory. Backups and
quarantines may contain private information and are not automatically deleted.
Relevant memory, experience summaries and document excerpts may be included in
model context, including when using a cloud provider.

### Manual configuration

Copy `.env.example` to `.env` beside the source or executable if preferred. Set
`MODEL_PROVIDER` to `openrouter`, `gemini` or `ollama`. Fill only the selected cloud
provider's key. Defaults for `MODEL_NAME` are `openrouter/free`,
`gemini-3.1-flash-lite` and `llama3.2`, respectively. Remove an old `MODEL_NAME`
when switching providers to use the new provider's default.

`WEB_SEARCH_ENABLED=true` opts in for cloud providers; the default is `false`.
`OLLAMA_BASE_URL` defaults to `http://localhost:11434`. Ollama uses `/api/chat`
and `/api/tags`, bypasses environment proxies, and does not follow redirects.
Chat allows 120 seconds for generation and 5 seconds to connect; discovery/status
checks allow 3 seconds. Use a URL pointing to your intended Ollama server.

JSON preferences use `model_provider`, `model_name`, `web_search_enabled`, and
`ollama_base_url` for Ollama. Never put keys in this file. Configuration precedence
is explicit process environment, saved JSON preferences, user-folder `.env`, then
adjacent `.env`. Web-setting commands apply immediately and save preferences;
explicit environment variables take precedence again after restart.

## Development and tests
>>>>>>> 7a14887 (Release Athena v0.12 with Ollama and native web search)

Install development dependencies:

```bash
python -m pip install ".[dev]"
```

Run the full suite:

```bash
python -m pytest -q
```

<<<<<<< HEAD
Tests cover:

- providers
- model errors
- CLI behavior
- quiet/debug output
- persistent memory
- conversation history
- experience recording
- experience retrieval
- feedback
- JSON storage recovery
- web search behavior
- tool permissions
- approval and rejection
- model bypass attempts
- document reading
- safe previews
- assistant workflows

The tests verify deterministic implementation behavior.

They do not independently measure live-model answer quality.

---

## License

Athena is licensed under the **Apache License 2.0**.

See [LICENSE](LICENSE).

---

## Project status

Athena is under active development.

**Current release: v0.11 — Work Assistant Foundations**

The current focus is building a safe and useful foundation before adding connected workplace services and external actions.
=======
Tests use fake providers and isolated stores; no live Ollama or cloud account is
required. They cover all providers, search, setup/settings, the agent loop,
permissions, memory, experience retrieval, recovery and release safety. The CI
matrix targets Python 3.12 and 3.14. Windows executable build instructions and
artifact checks are in [BUILD.md](BUILD.md).

## Current limits and license

Athena has no Gmail, Drive or Calendar integration, GUI, unrestricted computer
control, autonomous background work, semantic/vector memory or Ollama web search.
Local models vary in speed and ability to follow the action protocol. Keys use a
private plaintext fallback rather than Windows Credential Manager. The Windows
executable is unsigned and has no installer or automatic updater.

Licensed under [Apache License 2.0](LICENSE).
>>>>>>> 7a14887 (Release Athena v0.12 with Ollama and native web search)
