# Athena
**Current release: v0.12**

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

## Download

### Windows

[**Download Athena v0.12 for Windows**](https://github.com/theo79/Athena/releases/tag/v0.12)

Download `Athena-v0.12-windows.zip`, extract it, and run `Athena.exe`.

Python is not required for the Windows executable.

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

## Memory, experiences and permissions

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

```sh
python -m pip install ".[dev]"
python -m pytest -q
```

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
