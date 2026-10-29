# Athena runtime architecture (M1)

Athena v0.12 provides two supported entry points:

- `python agent.py` starts the existing CLI, including configuration and setup.
- `agent_runtime.AgentRuntime` runs synchronous tasks from another Python host.

The functional `agent.run_agent()` and `agent_runtime.run_agent()` APIs remain
available. Legacy wrappers preserve existing signatures and patch points. The
functional API has no conversation history unless a session is supplied; an
`AgentRuntime` creates a fresh bounded session by default.

## Execution flow

```text
CLI host: agent.py                  Other Python host
  configuration / commands          provider / callbacks / services
           |                                  |
  compatibility run_agent()          AgentRuntime.run_agent()
           +------------------+---------------+
                              |
                    agent_runtime.run_agent()
                    retrieve experience context
                    assemble prompt and history
                    up to five model calls
                    parse final answer or tool request
                    validate / confirm / dispatch tools
                    append completed conversation turn
                    persist experience in finally
```

The CLI remains a compatibility host; it does not create an `AgentRuntime`
instance. Both entry points use the same orchestration implementation. CLI
commands, prompts, provider defaults, tool schemas and stored formats are
unchanged by M1. No scheduler or background execution is implemented.

## Programmatic API

```python
from agent_runtime import AgentRuntime
from models import OllamaProvider

runtime = AgentRuntime(provider=OllamaProvider())
answer = runtime.run_agent("Explain the difference between RAM and disk storage.")
print(answer)
```

Importing `agent_runtime` and constructing `AgentRuntime` performs no interactive
setup, network requests, configuration loading or filesystem writes. Running a
task can call the provider, execute permitted tools, retrieve stored experiences
and persist an experience record. The host owns configuration and lifecycle.

### Provider injection

Pass any provider implementing `generate(messages)` and returning model text,
`None`, or `ModelFailure`. Passing `OllamaProvider()` explicitly selects local
inference without an API key or cloud fallback. Its default server is localhost;
the host must install/start Ollama and provide an installed model.

With `provider=None`, the runtime constructs a provider lazily on the first task
using existing environment configuration and existing provider defaults. Each
instance retains its own provider cache. Supplying the same provider object to
multiple instances deliberately shares that object.

### Conversation ownership

Each instance creates its own `ConversationSession()` unless the host supplies
`conversation=session`. The default session retains up to twenty user/assistant
messages; completed turns enter history, tool transcripts do not. Feedback target
IDs and task summaries are session metadata. Passing the same session deliberately
shares history. `runtime.conversation.clear()` clears temporary history and its
feedback target, preserving persistent stores.

### Registry snapshots

`registry=...` accepts the existing tool-definition mapping. Omit it to snapshot
the default registry; `{}` deliberately enables no tools. Tool metadata and nested
parameter schemas are copied. Later mutations to the source mapping or another
instance's metadata do not affect this instance. Handler objects retain their
identity, so stateful handlers can still share state.

`runtime.system_prompt` is built from `runtime.registry`, which is also used for
dispatch. Intentional edits to an instance's registry affect its next prompt and
dispatch. Registry configuration belongs to trusted host code, not model input.

### Confirmation callbacks

Pass `confirmation=callback`, where `callback(proposed_action)` returns exactly
`True` to approve one action. The proposed action contains a tool name,
description and redacted, JSON-escaped argument preview. `False`, truthy values
such as `1` or `"yes"`, exceptions and a missing callback do not authorize a write.
There is no persistent approval grant.

Local writes, external writes and sensitive actions require confirmation. Unknown
permission categories fail closed. Argument validation precedes approval. Both
`runtime.run_agent(task)` and `runtime.execute_tool(name, args)` use the existing
dispatcher. Direct Python calls to handlers remain outside this boundary.

### Experience-service injection

Pass `experience_service=service` to supply retrieval and recording behavior.
The existing `experience` module is the default. A compatible service provides:

- `Experience(task)`, returning a per-task recorder.
- `observe(operation, *args)`, preserving best-effort telemetry semantics.
- `find_relevant_experiences(task)`, `build_experience_context(matches)` and
  `safe_text(value, limit)`.

The recorder provides `record`, `provider(...)`, `tool(...)`, `finish(...)` and
`persist()`. Its record retains the existing fields used by orchestration,
including task, status, steps, parse failures, tool events and budget exhaustion.
`persist()` returns the saved ID used as the session feedback target.
`test_agent_runtime.py` contains an in-memory service example using the existing
record format. An adapter that only delegates to module globals does not isolate
storage; a host requiring separate stores must supply a service owning that state.

The runtime creates records through the supplied service, retrieves context from
it and persists in `finally`. The default observer makes telemetry failures
nonfatal while application/programming errors still propagate. CLI feedback
continues using the existing experience module. A custom host must route feedback
to the same service/store as the recorded ID; `AgentRuntime` adds no feedback API.

## Remaining shared state and safety limits

Default tool handlers use global workspace/memory paths in `tools.py`. The default
experience module uses its global experience path. Settings and provider factories
read process environment; web search follows those settings even if the host
injects a different chat provider. Diagnostics and redaction remain process-wide.
The CLI retains its legacy global provider cache and registry behavior.

Runtime instances isolate default conversations, provider caches and copied
registry metadata. They do not establish thread/process safety, separate default
data directories or a Python sandbox. Explicitly shared services, handlers and
other injected objects remain shared.

JSON storage requires one writer/process per data directory. Atomic replacement
prevents partial files but cannot prevent lost concurrent read/modify/write
updates. Reads can create or repair files during recovery. Backups and quarantines
retain private data without automatic expiry. No SQLite migration, locking or
retention policy is introduced by M1.

## Device communication foundation (M3.1)

The optional DeviceGateway is a component owned by the existing SettingsServer;
it does not create another agent runtime or depend on AgentRuntime, a model
provider, memory, or experience storage. It is disabled until an owner starts it.
M3.1 binds its versioned bounded JSON-line protocol to loopback only. The
owner-authenticated settings page lists pending requests and paired identities,
approves or rejects pairing, and revokes devices without exposing credentials.
See [ATHENA_DEVICE_PROTOCOL.md](ATHENA_DEVICE_PROTOCOL.md) for message schemas,
HMAC challenge proofs, bounds, simulator usage and transport limits.

Pairing authenticates a simulated identity on the same host; messages are not
encrypted. No device listener accepts LAN connections. M3.1 does not implement
firmware, audio, Wi-Fi provisioning or physical outputs.

## Validation and packaging

Use Python 3.12+ and the development environment to run:

```sh
python -m pytest -q
python -m pip check
git -c core.whitespace=cr-at-eol diff --check
```

The CRLF-aware check respects existing Windows line endings. The suite uses fake
providers and isolated stores; runtime tests cover import/construction safety,
legacy parity, instance isolation, permissions and experience-service injection.
The existing symlink test may skip without Windows symlink privileges. CI currently
runs Ubuntu on Python 3.12 and 3.14, so Windows validation remains a separate check.

`pyproject.toml` includes `agent_runtime` in its explicit module list.
`Athena.spec` starts at `agent.py`, which imports `agent_runtime` directly; the
one-file console build uses no project data collection. `package_release.py`
enforces its four-file release allowlist and checks archive entries for private
runtime filenames and likely secrets. Its main routine reads private configuration
and creates release output, so source review and synthetic tests do not require
running it. See `BUILD.md` for a separately authorized build and executable smoke
check. Source-level validation does not verify a newly built executable.

Eight Python 3.14 bytecode files under `tool_modules/__pycache__/` are already
tracked despite `.gitignore`. They must be excluded from the M1 source checkpoint;
`git add .` would include their modifications. M1 validation leaves them untouched.
No generated artifacts or private stores belong in the intended source changes.
