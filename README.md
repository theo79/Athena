# Athena — A Modular, Local-First Embodied AI Assistant

Athena is a local-first AI assistant with configurable cloud or local intelligence,
persistent memory, controlled tools, and a path toward eventual physical embodiment
through a desktop robot. The current software runs on a computer. Hardware
embodiment, voice, and home-device control are future work.

## What works today (through M3.0)

- A synchronous assistant runtime with bounded conversation context and
  permission-checked tools for time, local documents, memory, and optional web
  search.
- Local inference through Ollama, or cloud inference through OpenAI, Gemini, or
  OpenRouter. Provider switching is available after setup and does not reinstall
  Athena or replace persistent data.
- JSON memory and experience files remain the default runtime storage. An
  optional SQLite memory service, explicit JSON migration, retrieval, lifecycle
  operations, and feedback integration are available to hosts that deliberately
  select them. SQLite migration does not switch the runtime storage backend.
- An optional owner-authenticated web settings server, bound to loopback by
  default. Explicit LAN access requires an operator-supplied trusted TLS
  certificate and private IPv4 bind address.

## In progress and planned

The M3.0 web settings page provides provider and local settings on the same
computer. Its LAN mode has explicit TLS configuration; it is not a hosted or
public remote service. Desktop settings and trusted robot-based configuration
are design targets.

A desktop robot, ESP32 firmware, voice input/output, ESP32 Wi-Fi provisioning and
recovery, and integration with home or other LAN devices are planned. They are
not implemented or operational. The address `192.168.4.1` is reserved for the
future ESP32 setup/recovery flow.

`athena.local` is the intended friendly main LAN interface name, but Athena
does not advertise it and its resolution is not universally available. LAN
settings access currently requires an explicitly configured trusted HTTPS
certificate and an address/name that resolves on your network. Loopback access
is the default.

## Install and run

Use Python 3.12 or newer. From the repository directory:

```sh
python -m venv .venv
```

Activate the environment (PowerShell: `.venv\\Scripts\\Activate.ps1`; macOS/Linux:
`source .venv/bin/activate`), then install the project:

```sh
python -m pip install ".[dev]"
athena
```

The installed `athena` command starts Athena and supports the provider
configuration commands below. The source-tree compatibility entry point
`python agent.py` is also available.

## Select or change an AI provider

At first setup, or later from the CLI:

```sh
athena config provider
athena config show
```

From a source checkout without installing the console entry point:

```sh
python -m provider_cli config provider
python -m provider_cli config show
```

Choose **Local AI** to use an Ollama server and a model already installed there.
Athena does not install Ollama, start its server, or download models. The default
endpoint is `http://localhost:11434`.

For cloud inference, select OpenAI, Gemini, or OpenRouter, acknowledge that task
context may be sent to the selected provider and that usage may incur charges,
then configure the matching credential environment variable
(`OPENAI_API_KEY`, `GEMINI_API_KEY`, or `OPENROUTER_API_KEY`). Supply a model
identifier available to your provider account. The CLI accepts a key only through
hidden interactive input; a key entered that way is process-only. For later
launches, configure the selected environment variable using your trusted local
secret facility. Never put a key in a command argument, source file, issue, or
committed configuration. Athena does not expose stored credential values.

Provider changes are tested before the selected configuration is replaced.
Successful switches preserve unrelated settings and persistent data. Existing
legacy `.env` files remain supported and are not rewritten. Consult
[AI_PROVIDER_CONFIGURATION.md](AI_PROVIDER_CONFIGURATION.md) for details,
precedence, provider requirements, and limitations.

## Open the web settings page (optional)

Install the project as above, then start the separate settings server:

```sh
python -m web_settings --port 8765
```

The installed equivalent is `athena-settings --port 8765`. The server asks for a
strong owner token through hidden terminal input unless `ATHENA_SETTINGS_TOKEN`
is set in its environment. Open **http://127.0.0.1:8765** on the same computer
and enter the token. The token stays in page memory and is not saved in browser
storage. Stop the server with Ctrl+C.

The server does not monitor or restart an already running agent. Restart Athena
after a web-saved provider selection to activate it. Loopback HTTP is the default.
Plain HTTP LAN access is refused. To enable LAN access, explicitly provide a
private IPv4 bind address, `--allow-lan`, and an existing trusted TLS certificate
and private key; follow [ATHENA_WEB_SETTINGS.md](ATHENA_WEB_SETTINGS.md). Do not
expose the settings server to the public internet or bypass certificate warnings.

## Memory and privacy

Conversation history is bounded and separate from persistent records. JSON
(`memory.json` and `experiences.json`) remains the default runtime storage.
The optional SQLite service is explicit; migration reads the specified JSON
sources without changing or deleting them. See
[MEMORY_SERVICE.md](MEMORY_SERVICE.md),
[MEMORY_MIGRATION.md](MEMORY_MIGRATION.md),
[MEMORY_RETRIEVAL.md](MEMORY_RETRIEVAL.md), and
[MEMORY_LIFECYCLE.md](MEMORY_LIFECYCLE.md).

Tool permissions constrain model-dispatched actions, but Athena is not a sandbox
for arbitrary Python code or other programs running on your computer. Retrieved
memories, experience evidence, documents, and tool output are untrusted context.
Relevant task context may be sent to a cloud provider when you choose cloud
inference. Optional provider web search also sends requests through the selected
provider and may incur charges. Web search is opt-in. Local inference keeps
inference on the configured local Ollama service, but does not make local files,
the operating system, or the assistant sandboxed.

Preferences and new credential references are stored locally; credentials are
environment-backed. Legacy installations may have plaintext keys in private
`.env` files. Keep those files, runtime memory/experience data, SQLite databases,
backups, and logs private. Run one Athena writer per data directory. See
[ATHENA_WEB_SETTINGS.md](ATHENA_WEB_SETTINGS.md) and
[AI_PROVIDER_CONFIGURATION.md](AI_PROVIDER_CONFIGURATION.md) for additional
security limits.

## Tests and development

```sh
python -m pip install ".[dev]"
python -m pytest -q
python -m pip check
```

The project targets Python 3.12+ on Windows and Linux. The configured CI matrix
covers both operating systems; distribution checks also exercise Debian, Fedora,
and Arch Linux. A configured CI target is not evidence of a run—consult the
repository's actual CI results for current platform verification. Windows
packaging instructions and checks are in [BUILD.md](BUILD.md).

## Documentation and license

- [Architecture](ARCHITECTURE.md)
- [Experience records and feedback](EXPERIENCE.md)
- [Provider configuration](AI_PROVIDER_CONFIGURATION.md)
- [Web settings](ATHENA_WEB_SETTINGS.md)
- [SQLite memory service](MEMORY_SERVICE.md)
- [JSON-to-SQLite migration](MEMORY_MIGRATION.md)
- [Memory retrieval](MEMORY_RETRIEVAL.md)
- [Memory lifecycle](MEMORY_LIFECYCLE.md)
- [Windows packaging](BUILD.md)
- [Apache License 2.0](LICENSE)
