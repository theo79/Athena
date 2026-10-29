# AI provider configuration and switching (M2.6)

Athena is the application; the model is an interchangeable inference engine.
This milestone reuses ModelProvider.generate(), the existing runtime/action parser,
permission checks, memory backends and native adapters. One
ProviderConfigurationService supplies configuration logic to first-run setup,
CLI commands and future trusted interfaces. No new runtime is introduced.

## Provider matrix

| Selection | Existing adapter/interface | Credentials | Model selection |
| --- | --- | --- | --- |
| Local AI (`local`, legacy `ollama`) | Ollama /api/tags and /api/chat | None | Explicit installed model ID |
| OpenAI (`openai`) | Official OpenAI SDK Responses API | OPENAI_API_KEY or selected environment reference | Required user-supplied ID; no OpenAI model is hardcoded |
| Google Gemini (`gemini`) | google-genai Client, native generate_content and Content roles | GEMINI_API_KEY or selected reference | Explicit model ID |
| OpenRouter (`openrouter`) | Existing OpenAI-compatible chat adapter at https://openrouter.ai/api/v1 | OPENROUTER_API_KEY or selected reference | Explicit provider/model ID |

Legacy Gemini/OpenRouter/Ollama defaults remain readable for existing installations.
First-run setup always asks for a model. No model/provider fallback is added.
No new application dependency is required.

The OpenAI adapter uses responses.create(model=..., input=..., store=False),
output_text, a fixed official base URL, finite timeout and zero SDK retries.
The native Gemini adapter retains its system/role conversion and disabled automatic
function calling. OpenRouter retains its documented compatible endpoint; models
are not assumed to support native tool calling or strict structured outputs.

## First run and later changes

With an existing valid configuration, startup remains compatible and skips setup.
Missing credentials/configuration starts the new workflow:

1. Choose **Local AI** or **Cloud API**.
2. For local, explicitly confirm an endpoint (default displayed as
   http://localhost:11434). Optionally query that one endpoint for installed
   models, or manually enter a model identifier. No scanning, model installation,
   downloads or server launching occurs.
3. For cloud, choose OpenAI, Gemini or OpenRouter, acknowledge cloud transmission
   and possible charges, select the credential environment reference and model,
   then optionally supply a key using hidden terminal input for this process.
4. Explicitly approve a connection/action-protocol test. It sends only synthetic
   text, never a conversation, memory record or user task. Cloud tests may be billed.
5. Only a successful test permits atomic configuration replacement. Failure or
   cancellation preserves the prior selection and unrelated settings.

After installing the existing project using `python -m pip install ".[dev]"`:

```sh
athena
athena config provider
athena config show
```

Equivalent source-tree commands (no reinstall needed):

```sh
python agent.py
python -m provider_cli config provider
python -m provider_cli config show
```

Inside an existing Athena chat, use `/config provider`. After a successful change,
the CLI replaces only its selected provider object; conversation history, identity,
permissions and data stores remain intact. A failed change leaves the cached old
provider in use. `/help` lists the command. No key is accepted as a CLI argument.

Example OpenAI setup: choose Cloud API, OpenAI, acknowledge transmission/charges,
accept OPENAI_API_KEY (or select your environment reference), and enter a model ID
available to your API project. Leave hidden key input blank to use the environment.
Create keys through [OpenAI API keys](https://platform.openai.com/api-keys).
A ChatGPT subscription does not provide API credits or replace an API credential.
Model availability and account billing must be checked in the user's account.

Gemini credentials come from [Google AI Studio](https://aistudio.google.com/apikey).
OpenRouter credentials come from [OpenRouter key settings](https://openrouter.ai/settings/keys).
Never paste keys into a task, conversation, model field, endpoint URL or issue.
No live credential is included in examples or automated tests.

## Shared Python API

```python
from provider_configuration import ProviderConfig, ProviderConfigurationService

service = ProviderConfigurationService()  # Existing settings path; no I/O on construction.
current = service.read()                   # Safe configuration, never a stored key value.
proposed = service.validate(ProviderConfig('openai', 'YOUR_OPENAI_MODEL_ID'))

# Use an externally configured credential environment variable. No real key in code.
result = service.switch(proposed, allow_network=True, acknowledge_cloud=True)
if result.ok:
    runtime.provider = result.provider     # Explicit host activation, existing AgentRuntime.
```

Local switch:

```python
proposed = ProviderConfig('local', 'YOUR_INSTALLED_MODEL_ID', 'http://localhost:11434')
result = service.switch(proposed, allow_network=True)
```

`test(config, credential=None, allow_network=False, acknowledge_cloud=False)`
validates/probes without saving or changing process selection. `switch(...)`
performs that same test before committing. An optional credential parameter is
process-only; it is not a ProviderConfig field and never enters JSON storage.
`create_provider(...)` constructs the existing adapter without testing or committing;
it is a trusted host building block, not a replacement switch path.
`list_local_models(endpoint, allow_network=True)` queries only that selected endpoint.

ProviderTestResult contains ok/code/message and observed capabilities. The live
provider is excluded from its repr. Results never expose raw SDK errors, headers,
response bodies or credentials. Each future interface should call these same APIs,
not copy the CLI's switching logic.

M3.0 implements the lightweight browser **AI Provider** settings interface.
Desktop settings and robot configuration with trusted confirmation remain design targets. They must supply authenticated
host intent, display cloud implications, collect explicit acknowledgements, call
this service and activate a successful returned provider. The browser implementation is documented in ATHENA_WEB_SETTINGS.md.

## Storage, precedence and atomicity

Nonsecret selection is merged into the existing config.json at
settings.preferences_path(), using portable pathlib operations. Provider fields are
model_provider, model_name, credential_env, provider_config_version=1 and the local
ollama_base_url where applicable. Unknown/unrelated preferences, web search choice,
identity/permission settings and historical local endpoint settings are retained.
Memory, experience, SQLite databases and conversation storage are never opened by
the configuration service. Existing legacy .env files are read, never rewritten.

The service probes first, detects changes to the configuration file during the
probe, writes/flushed/fsyncs a sibling temporary file and publishes via os.replace.
Process selection and any process-only key are changed only after replacement
succeeds. A failed save removes the temporary file and keeps old process state.
This retains the project's single-writer contract; it is not multi-process locking
or an atomic transaction across separate processes. A process crash after disk
replacement can leave the old process provider cached until restart.

M3.0 acceptance establishes managed precedence: after a successful shared-service
switch, saved provider/model/credential-reference/local-endpoint selection wins at
restart. Legacy unmanaged files retain environment > saved preferences > legacy
.env precedence. Credential values and unrelated settings keep their existing
precedence. Conflicts are exposed by field name, never by value, in config show
and web settings. See [ATHENA_WEB_SETTINGS.md](ATHENA_WEB_SETTINGS.md).
Unknown future configuration versions and corrupt files are not overwritten by
the switching service. Repair a separate copy or restore a known good config.

The existing platform settings path facility is retained. Hosts/tests may supply
an explicit portable path to ProviderConfigurationService; no OS-specific path is
hardcoded into the new service.

## Credentials and cloud acknowledgement

No OS keychain integration is currently available in Athena. New setup therefore
uses environment-backed credential references, rather than silently persisting
plaintext credentials. A key typed with getpass is held only in the current process;
before a later launch, set the selected environment variable through your trusted
OS/shell/secret-management facility. Do not put it in shell command arguments or
commit it. If hidden terminal input is unavailable, setup fails closed.

Legacy installations with .env credentials continue to work. Their existing
plaintext files remain the user's responsibility; M2.6 neither copies nor deletes
them. Configuration display contains references only and passes through existing
redaction. Active custom credential references are included in runtime redaction,
even when their variable name does not contain KEY/TOKEN/SECRET. Memory records,
telemetry and exceptions must not carry configuration credentials.

Every cloud test/switch requires acknowledge_cloud=True, including local-to-cloud
changes. CLI acknowledgement explicitly covers subsequent tasks: the selected cloud
model can receive conversation history, retrieved memory/experience evidence and
tool output already provided to Athena's runtime. Existing tool access boundaries
still apply. A failed local request never selects or contacts a cloud provider.
No automatic provider routing, fallback or cloud key generation is added.

New local endpoint validation accepts HTTP/HTTPS with localhost, loopback/private
IP addresses or explicit .local names. Credentials, query strings, fragments,
unspecified/public IPs and public DNS names are rejected. No service is exposed or
started. Ollama requests retain disabled environment proxies and redirects. Hosts
must trust their explicitly configured server; DNS rebinding/authentication is not
newly solved. Legacy endpoint behavior remains available through the old adapter.

## Capabilities and errors

Athena requires text generation that follows its existing JSON action protocol.
The synthetic test verifies a final-action JSON response; a model response asking
to execute a tool is rejected during testing and is never executed. Runtime tools
continue through the existing parser, registry validation and host confirmation.
A successful probe is evidence for this one test, not a guarantee of future model
behavior, native function calling or schema-constrained generation.

ProviderCapabilities records observed text_generation/action_protocol. Native tools,
structured outputs and context limits remain unknown; streaming is not used. No
support is inferred solely from provider name. OpenAI web search is safely marked
unavailable in this milestone; local web search remains unavailable. Existing
Gemini/OpenRouter search adapters and the saved user preference are retained.

Safe error codes distinguish invalid_configuration, missing_credentials,
invalid_credentials, unreachable, model_unavailable, unsupported_capability,
rate_limit, timeout, provider_error, cloud_ack_required, test_required,
storage_error and configuration_changed. Provider responses are not echoed.
Authentication/project access restrictions may share an authentication category;
unknown SDK failures are generic provider_error. No failure activates a fallback.

## Tests and CI

```sh
python -m pytest test_provider_configuration.py test_settings.py test_models.py test_gemini.py test_ollama.py
python -m pytest test_agent_runtime.py test_memory_service.py test_memory_migration.py test_memory_retrieval.py test_memory_lifecycle.py
python -m pytest
python -m pip check
```

Tests use temporary configuration/data files and mock providers/HTTP transports;
ordinary unit tests never require a live endpoint or paid API request. Optional
live smoke testing is manual and disabled by default: run `athena config provider`
with a trusted local server or your own cloud credentials and explicitly approve
the disclosed test. It performs a real request and may incur charges.

The existing Windows/Ubuntu Python 3.12–3.14 CI workflow installs `.[dev]`, runs
all tests (including provider and memory suites), then pip check. Remote CI results
for an older commit do not verify uncommitted local milestone changes. Report
actual local and observed remote results separately. No new CI duplication.

## Official interface references

- [OpenAI Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
- [Gemini generateContent](https://ai.google.dev/api/generate-content)
- [OpenRouter API](https://openrouter.ai/docs/api_reference/overview)
- [Ollama model listing](https://docs.ollama.com/api/tags)

No web dashboard, desktop framework, robot interface, voice/ESP32/home automation,
autonomous provider selection or M2.7 work is included.
