# Athena web settings (M3.0)

One lightweight browser interface uses the same ProviderConfigurationService as
first-run setup, CLI configuration and in-chat switching. It adds no database,
frontend framework, Node.js build, discovery framework or application dependency.
The standard-library HTTPServer serves three packaged static assets and JSON
routes. HTML presentation, HTTP validation and provider logic remain separate.

## Install, start and stop

Use the existing Python 3.12+ installation:

```sh
python -m pip install ".[dev]"
python -m web_settings --port 8765
```

The installed equivalent is `athena-settings --port 8765`. Supply a strong owner
token (32–256 printable ASCII characters) through the hidden startup prompt or
ATHENA_SETTINGS_TOKEN in the server's environment. Prefer a randomly generated
secret stored in your existing trusted secret facility. Do not put the token in
arguments, URLs, source files or shared logs. The server never prints it.

Open **http://127.0.0.1:8765** on the same host, then enter that owner token.
Credentials and authentication tokens are not browser-localStorage/sessionStorage
or cookie data. The access token stays in page memory; Lock settings or closing
the page clears it. Owner authentication is required for every settings API call.
The unauthenticated page is a login shell only, containing no active configuration.
Press Ctrl+C in the server terminal to stop. No inference endpoint is contacted
on startup or page load. A missing/unreachable provider does not prevent serving
settings. This is a separate synchronous settings process, not a new agent runtime.

## Name resolution and LAN access

`athena.local` is the intended friendly hostname, but resolution depends on the
host/network. No mDNS daemon or advertisement dependency is added. Do not assume
that http://athena.local works automatically. If you already have appropriate
local name resolution, use the same configured port. A bare hostname URL implies
port 80/443, not the default 8765. No hosts file, firewall or privileged port is
configured automatically.

Loopback HTTP is the default. **Plain HTTP LAN binding is refused.** To enable LAN
access explicitly, supply an existing trusted TLS certificate/private key and an
explicit private IPv4 interface address:

```sh
python -m web_settings --bind 192.168.1.10 --port 8765 --allow-lan --cert server-cert.pem --key server-key.pem
```

Use **https://192.168.1.10:8765**, or **https://athena.local:8765** if name resolution
and the certificate cover that name. Certificate trust/hostname validation must
work in the browser before entering a token. Do not bypass browser certificate
warnings. If you cannot provide trusted HTTPS, keep access on loopback and use
the secure CLI workflow. LAN access is never enabled merely because a LAN exists.

The service refuses public/unspecified listening IPs and supports IPv4 only in
M3.0. It does not expose itself, configure routing or open firewall ports. Never
forward it to the public internet. Certificate issuance/renewal and secure host
administration remain operator responsibilities; no certificate authority or
production identity system is introduced.

`192.168.4.1` is reserved for a future ESP32 Wi-Fi setup/recovery flow. That flow,
Wi-Fi scanning/passwords, AP provisioning, voice and home automation are not part
of M3.0.

## Interface and activation

Overview shows the effective selection for the next agent start, application OS
and version, and settings-service status. It explicitly does not monitor a
separately running agent. Connection status is **Not checked** until an explicit
check of the matching selection in this server session; checks include UTC time
and elapsed milliseconds. A successful test is not a guarantee of future service
availability. A check of an unsaved draft does not label a different saved provider
as connected. No background reachability polling occurs.

AI Provider supports local/cloud mode, OpenAI/Gemini/OpenRouter, explicit model
entry, a local endpoint, environment credential references and installed local
model discovery. **There is no API-key value field.** Configure credentials in the
server's environment or with the existing secure CLI/environment workflow, then
restart the server if necessary. Hidden keys typed in another CLI process do not
magically appear in this process. No provider credential is sent to the browser.

Editing only changes the form draft. Cancel restores the effective selection.
Test Connection explicitly sends a synthetic request using service.test(), with
no save. Save / Switch uses service.switch(), which repeats validation/testing and
atomically commits only after success. Cloud actions require explicit acknowledgement
of task/history/evidence transmission and possible charges. It is never inferred
from the provider name or from opening the page.

**Restart any running Athena agent to activate a web-saved selection.** Existing
agent objects retain their cached provider. The UI does not rebuild a runtime,
clear conversation history or change identity, permissions, memory, experiences,
SQLite data or unrelated settings. CLI setup, `athena config provider` and
`/config provider` continue using the same configuration service.

If a browser request is interrupted, do not assume a save rolled back: the server
may have committed before the response was lost. Reload/unlock settings and check
the effective selection before retrying. Known validation/test/save failures
reported by the shared service preserve the prior configuration.

## Deterministic precedence (M2.6 acceptance)

A successful service-managed switch now saves the nonsecret marker
`provider_selection_source: "service"` in the existing configuration JSON.

- Managed selections: saved provider, model, credential reference and selected
  local endpoint win over stale process/legacy selector fields at restart.
- Legacy/unmanaged configurations: the prior policy is preserved—explicit process
  environment, then saved preferences, then legacy user/adjacent .env settings.
- Credential **values** remain environment-backed, with existing environment over
  legacy .env precedence. Secrets are never written into the JSON selection.
- Unrelated settings, including web-search preference, retain their existing
  precedence. No environment file or old configuration is silently deleted.

ProviderConfigurationService.read() and runtime_paths.load_configuration() agree
on the policy. selection_status() reports the policy and conflicting field names,
never their values. Conflicts detected during startup are retained for the process
and shown by the page and `athena config show`. The active selection is therefore
unambiguous without exposing stale values or secrets. Older files are not forcibly
converted; only an explicitly successful switch creates the managed marker.
Corrupt/unsupported configuration remains inspectable as an unavailable selection;
failed updates do not discard it. The single-writer persistence contract remains.

## Security boundaries and request limits

- A strong owner bearer token is required. The server retains its SHA-256 digest
  and compares authentication using constant-time digest comparison.
- Changing/testing/discovery requests require JSON, a session CSRF header, an
  exact same-origin Origin and an allowed Host/port. Duplicate Host/Origin and
  unrelated hosts/origins are rejected. There is no permissive CORS policy.
- Host aliases are the listening IP, localhost, 127.0.0.1 and athena.local at the
  configured port. Name resolution alone grants no authentication.
- No API keys, raw SDK bodies, stack traces, arbitrary environment listings or
  private filesystem paths are returned. Known credential values are redacted.
  Request/body/authorization logs are deliberately disabled.
- Responses use no-store caching, nosniff, no-referrer and a restrictive CSP with
  same-origin scripts/styles/connect and blocked framing. Dynamic text uses DOM
  textContent/value rather than injecting HTML.
- Browser requests accept only provider/model/endpoint/credential-reference fields
  plus explicit test/cloud acknowledgement flags. Credential-value fields are rejected.
- Web local testing is limited to default localhost Ollama endpoints, an existing
  trusted configured local endpoint, or endpoints enabled at server startup with
  repeatable `--local-endpoint http://127.0.0.1:12345`. Requests cannot supply an
  arbitrary private/public URL. Cloud endpoints remain fixed by the shared adapters.
- Request bodies are capped at 8192 bytes; one Content-Length is required and
  transfer encoding is rejected. Socket/handshake/body reads have a five-second
  timeout. Provider calls retain existing finite limits: cloud approximately 30s,
  local chat up to 120s plus model listing. Browser operations time out after 135s.
- The server is synchronous: one slow authorized provider operation can delay
  other requests. No worker pool or background polling is added. Use on a trusted
  local machine/network; this is not an internet-facing production web platform.

Operator-approved local endpoints still require trust in the configured server
and local network/name resolution. The allowlist does not turn arbitrary Python,
raw SQL, OS access or host administrators into untrusted sandboxed users.

## Network information

Network is read-only: hostname, safely resolved IPv4 interface addresses and the
listening URL. Hostname resolution can return an incomplete or empty address list;
the UI handles both. No Wi-Fi scan, network configuration, internet ping, external
IP lookup or blanket connectivity claim occurs. Provider testing is explicitly
separate from independent network reachability, which remains labelled untested.
System information contains the app version, OS, selection and truthful standalone
runtime status; it does not dump environment values or filesystem paths.

## Explicit live smoke test

M3.0 adds a separate opt-in command using the shared service's synthetic test:

```sh
athena config smoke-test --run-live
# For a cloud selection, also acknowledge transmission and possible charges:
athena config smoke-test --run-live --acknowledge-cloud
# Source-tree equivalent:
python -m provider_cli config smoke-test --run-live --acknowledge-cloud
```

Without --run-live, no inference request occurs. Cloud additionally requires
--acknowledge-cloud and valid configured credentials. Output contains provider,
model, success/failure category and elapsed milliseconds, never key values or
response text. The command does not change configuration or save task telemetry.
Actual live requests are manual, explicitly authorized and may be billed. Mocked
unit tests are not evidence that a live provider is reachable.

## Tests, packaging and CI

```sh
python -m pytest test_provider_acceptance.py test_web_settings.py
python -m pytest test_provider_configuration.py test_settings.py test_agent_runtime.py
python -m pytest test_memory_service.py test_memory_migration.py test_memory_retrieval.py test_memory_lifecycle.py
python -m pytest
python -m pip check
```

Tests use temporary config/data, mocked inference providers and loopback HTTP.
They cover startup/shutdown, owner access, CSRF/Host/Origin, redaction, failed writes,
managed/legacy restart precedence, limits, discovery, local/cloud selection and
unchanged memory. LAN binding/TLS wrapping policy is unit-tested; that alone is
not a real LAN certificate/handshake or multi-device acceptance result.

The existing Ubuntu/Python 3.14 CI job already installs `.[dev]` and runs the full
suite, including acceptance/web/memory tests, plus pip check. No duplicate workflow
is needed. Report observed runs separately from configured YAML; an older commit
or empty remote run listing does not verify uncommitted changes.

Static web assets ship in the Python wheel as package data. No frontend build
step is required. The new entry point is a Python-package service; a refreshed
one-file Windows executable distribution is not built by this milestone.

## Troubleshooting and limits

- Cannot unlock: check the owner token and exact URL/port. No token is recovered
  or echoed by the server. Restart with a new trusted token if necessary.
- Cloud test fails: ensure the credential reference exists in the server process;
  inspect the safe category (authentication, quota/rate, timeout, capability, model).
- Endpoint refused: use the owner-enabled list shown in the UI or explicitly add
  a trusted endpoint at startup. The web page cannot expand its own allowlist.
- Selection reverts in an older installation: complete an explicit successful
  service switch to establish managed precedence; inspect config-show diagnostics.
- athena.local fails: use loopback, or trusted HTTPS LAN IP with a matching trusted
  certificate. No multicast resolution or ESP32 recovery path is silently enabled.
- UI save appears stale in chat: restart the separate agent; no hot runtime
  replacement is attempted from the web process.

M3.1, ESP32/Wi-Fi provisioning, voice, home automation, certificate issuance and
production multi-user identity infrastructure remain outside scope.
