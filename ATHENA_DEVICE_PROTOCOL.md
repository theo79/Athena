# Athena device protocol, pairing, and WebSocket sessions (M3.2)

M3.1 adds a loopback-only software foundation for a future desktop device. The
Core process remains responsible for provider configuration, memory, permissions,
and reasoning. The gateway is a component of the existing settings server, not a
second agent runtime. A Python simulator stands in for an ESP32; no firmware,
microphone, speaker, display hardware, sensors, servos, voice, or Wi-Fi
provisioning is implemented.

## Network boundary

The legacy TCP gateway is **disabled until an owner starts it** in the authenticated Athena
Settings page. It binds only to `127.0.0.1` on the configured device port (default
8766). The page's own optional LAN mode does not change this listener. The
gateway rejects non-loopback peers and has no LAN option in M3.1.

Device frames are not encrypted. Pairing credentials cross the loopback socket.
This is a local simulator foundation, not a claim that pairing is secure on a LAN.
Do not connect physical devices or forward the port. The M3.2 WebSocket adapter described below provides a separate opt-in TLS
transport for previously paired identities. The existing Settings page can itself use operator-configured TLS
for its own remote browser session; that does not wrap the device socket.

The device registry is stored as `devices.json` beside Athena's provider settings.
Athena's existing atomic JSON writer publishes registry updates. Paired entries
contain a SHA-256-derived HMAC verifier, not the raw token; on POSIX the temporary
file is private before replacement, and Windows inherits the settings directory's
ACL. Treat the registry and its directory as sensitive: the verifier is
credential-equivalent for challenge proofs. Device names, status and timestamps
are returned to the owner page; verifiers and raw credentials are never returned
to the browser or written to request logs.

## Protocol v1

Legacy TCP transport is one UTF-8 JSON object per LF-terminated frame.
WebSocket transport is one JSON object per text message, without a trailing LF. Each frame is
limited to 4096 bytes. Duplicate object keys, non-finite JSON numbers, unknown
fields, invalid types, invalid identifiers and unsupported versions are rejected.
Both peers use UUID message identifiers; device IDs are 1-64 ASCII letters,
digits, dots, underscores or hyphens. No request can name an arbitrary URL, file,
shell command, Python code or GPIO operation.

Device-to-Core messages:

| Type | Purpose |
| --- | --- |
| `hello` | Protocol version, device ID and display name |
| `pair_request` | Request owner pairing for an unknown identity |
| `pair_confirm` | Prove possession of the one-time pairing credential |
| `heartbeat` | Report bounded uptime |
| `device_status` | Report simulated expression and LED state |
| `command_result` | Return a result for a correlated Core command |

Core-to-device messages are `hello` (pair-required, challenge, or authenticated
state), `pair_request` (pending request identifier), `pair_confirm` (credential
delivery to the waiting socket), heartbeat/status acknowledgements, `command`,
and safe `error` responses. Secrets are transmitted only to the awaiting device
socket after explicit owner approval.

A command contains a UUID `command_id` in addition to its message ID. Results must
echo the command ID and come from the authenticated device connection. The command
allowlist is:

| Name | Arguments | Simulated effect |
| --- | --- | --- |
| `display.set_expression` | `{"expression":"neutral\|happy\|curious\|thinking\|sleepy"}` | Change in-memory face state |
| `display.set_text` | `{"text":"Hello"}` | Update simulated display text (1–256 characters) |
| `display.set_status` | `{"status":"Ready"}` | Update simulated display status (1–64 characters) |
| `led.set_state` | `{"state":"on\|off"}` | Change in-memory LED state |
| `device.get_status` | `{}` | Return the two simulated state fields |

Text and status reject control characters and extra fields. Both new display
commands acknowledge with `ok: true` and an empty `result` object. Telemetry and
`device.get_status` retain the M3.1 expression/LED schema for compatibility.
All other names, arguments, and result fields are rejected. No command executes a
shell, fetches a URL, accesses files, moves a motor, or operates arbitrary pins.

## Pairing and authentication

1. The owner starts the loopback gateway from Settings. The simulator connects and
   sends `hello`, followed by `pair_request`.
2. Core displays the request's device ID and name in the owner-only device panel.
   An unpaired device cannot send commands or telemetry.
3. The owner approves or rejects that specific pending request. Approval generates
   a 256-bit random URL-safe credential. Core sends it over that request's socket;
   the browser receives only the approval result and device ID.
4. The simulator proves possession using HMAC-SHA256 over the protocol version,
   device ID, random request ID and random challenge. Core stores the derived
   verifier only after proof succeeds. Rejected, expired, disconnected, or
   unconfirmed requests do not create paired records.
5. Reconnect uses a fresh random challenge and the saved credential. Device ID
   alone never authenticates.
6. Revoke removes the verifier, closes the current connection and leaves a
   tombstone. That identity cannot reconnect or request pairing again.

The simulator retains its credential in memory until the process exits. Future
hardware must persist its credential in protected device storage. Core's verifier
is sensitive and is not a public key: local access to the registry can enable
impersonation. No public-key infrastructure, certificate authority or custom
cryptography is introduced.

## Gateway and simulator use

Install Athena's existing dependencies, then run the settings service with its
device port:

```sh
python -m web_settings --port 8765 --device-port 8766
```

Open `http://127.0.0.1:8765`, authenticate with the owner token, and click
**Start loopback gateway**. The default gateway has no listener before that
explicit action. In another terminal run:

```sh
python -m device_simulator --host 127.0.0.1 --port 8766 --device-id desk-sim
```

The simulator prints its pending request ID, not its credential. Approve it in
Settings. The device page then shows connection state and offers status, happy
expression, LED-on, and revoke actions. The simulator reconnects with a fresh
challenge after a transient connection loss while it remains running. Ctrl+C
stops it. The Settings owner can stop the gateway at any time.

The host API is also available to trusted Python callers:

```python
from device_gateway import DeviceGateway

gateway = DeviceGateway("devices.json")  # Does not bind or write at construction.
address = gateway.start()                 # Explicit loopback bind.
summary = gateway.status()                # No credentials in this result.
gateway.approve_pairing(request_id)       # Owner-authorized request ID only.
result = gateway.send_command(
    "desk-sim", "display.set_expression", {"expression": "happy"}
)
gateway.revoke_device("desk-sim")
gateway.stop()
```

The settings page uses the same gateway methods behind its existing owner token,
Origin, Host and CSRF checks. Provider configuration remains separate. Pairing
does not call an AI provider, read memory or experiences, or grant access to
their contents.

## Legacy TCP heartbeats, limits and recovery

Every authenticated inbound message has a fresh UUID message ID. Core remembers a
bounded recent window of 128 IDs per paired device and rejects duplicates. Commands
also have one-time UUID command IDs; unknown or late results are rejected. The
simulator sends heartbeats every two seconds by default. Socket reads have a
five-second timeout on the legacy TCP transport; the owner page marks a TCP connection disconnected when it has
not been seen for 15 seconds. Pair approval requests expire after two minutes.

Core caps the registry at 256 identities and pending TCP pairings at 16. Each
transport caps its simultaneous connections at 16. Device messages are capped
at 4096 bytes. These limits bound storage and work; they are not a general
denial-of-service defense. M3.2 adds a separately enabled WebSocket listener,
whose LAN exposure requires explicit configuration and TLS as described below.

Unsupported versions, malformed frames, spoofed identities, bad proofs, duplicate
IDs, unknown commands, invalid arguments, missing command correlations and revoked
devices are rejected without logging protocol bodies or credentials. The gateway
does not silently pair a failed device, migrate an incompatible registry, or
restore a revoked verifier.

If the registry JSON is malformed or has an unknown schema, gateway startup fails
closed and leaves the file unchanged. Stop writers, keep a copy of the registry,
inspect or restore a known-good copy, then explicitly restart the gateway. A lost
simulator credential cannot be recovered from Core's verifier. The simulator
currently keeps it only in process memory; if the simulator exits, use a new device
identity and explicitly pair again, or retain the running simulator. Revoked
identities remain tombstoned and cannot be reused.

## M3.2 WebSocket transport

`DeviceGateway.network_transport(**configuration)` creates one optional
`WebSocketTransport` using that gateway's existing registry, revocation controls,
and identity proof. Exactly one WebSocket adapter per gateway is supported. Direct
`WebSocketTransport(gateway, ...)` construction also registers that adapter with
the gateway; a second adapter is explicitly rejected. All supported adapters
therefore participate in command routing, exclusive ownership, revocation, and
shutdown. Construction never binds a socket. The existing Settings
**Start loopback gateway** button continues to control only the legacy TCP
listener; it does not enable WebSockets or LAN access.

The adapter defaults to `127.0.0.1:8767`. Start and stop it explicitly on one
asyncio event loop, owned by the embedding application:

```python
network = gateway.network_transport(port=8767)
async with network:  # awaits start(), then stop() even on failure
    # Keep the application's event loop alive while devices connect.
    await application_shutdown_event.wait()
```

Here `gateway` is the existing `DeviceGateway` instance (for an embedding of the
Settings server, `settings_server.device_gateway`). The application supplies the
shutdown event. No second registry writer or agent runtime is needed. Use
`gateway.status()["network"]` or `network.status()` for listener/session state.
Paired-device connection status includes network sessions. Legacy `enabled`,
`listen`, and `transport` fields continue to describe the TCP listener.

For LAN exposure, provide an explicit numeric bind address, `allow_lan=True`,
and an operator-configured `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)` with a
certificate/key loaded using `load_cert_chain`. For example:

```python
network = gateway.network_transport(
    host="0.0.0.0", port=8767, allow_lan=True, ssl_context=server_context)
```

Non-loopback binding without both explicit opt-in and TLS fails before listening.
Clients must trust the server's certificate and verify its hostname/IP. The
simulator accepts a client `ssl_context` for a private CA; otherwise `wss://`
uses system trust. TLS supplies server authentication/encryption; the existing
device HMAC proof supplies device authentication. There is no certificate
provisioning service, mDNS, discovery, or port forwarding. Web browser Origin
headers are rejected; this endpoint is for device clients.

### Pair once, authenticate each connection

Pair through the existing owner-approved M3.1 loopback flow. WebSocket does not
issue credentials or accept pairing requests. Unknown/revoked identities close
with WebSocket policy code 1008. A paired client sends the same v1 `hello` and
receives a fresh `hello` challenge. It sends the same `pair_confirm` request ID,
challenge, and HMAC proof; Core returns `hello` with `status: authenticated`.
The default authentication deadline is five seconds for the complete protocol
handshake. Identity changes, replayed IDs, unknown commands/results, duplicate
results, and unexpected message types close the connection. One active connection
per identity is allowed across the gateway's TCP and WebSocket sessions.

The application heartbeat is a v1 `heartbeat`, acknowledged by Core. The default
deadline is fifteen seconds since authentication or the last heartbeat. Status
traffic and command results do not extend that deadline. WebSocket ping frames
are not substitutes for application heartbeats. Idle sockets close with code
1008 during ordinary idle expiry. The deadline covers acknowledgement writes
as well as reads, so a blocked writer cannot retain ownership indefinitely.
Writers are serialized, with at most sixteen command operations and one handler
acknowledgement per session. Expiry releases ownership and fails pending commands
before closing the connection; a stalled or cancelled in-progress send aborts
its socket. Reconnection always performs a fresh challenge; it does not reuse a session
or automatically re-pair. Revocation closes the network session and prevents
future authentication.

Each WebSocket message passes the same strict v1 validator and 4096-byte bound
(including the internal LF used by the validator). Binary frames, trailing LF,
duplicate JSON keys, non-finite numbers, and malformed JSON are rejected.
Oversized WebSocket messages may close with library code 1009. Compression is
disabled; received-frame queues are bounded to four. At most sixteen established
connections and sixteen outstanding commands per session are allowed.

Send a display update on the event loop with:

```python
result = await network.send_command("desk-sim", "display.set_text", {"text": "Hello"})
result = await network.send_command("desk-sim", "display.set_status", {"status": "Ready"})
```

Existing synchronous callers, including the Settings command endpoint, can use
`gateway.send_command(...)` for either TCP or authenticated WebSocket devices.
The gateway captures the owning session under its lock and releases the lock
before dispatch. WebSocket calls run on the adapter's owning event loop; their
deadline includes time spent waiting to be dispatched, and a queued call cannot
move to a replacement session after reconnect. The synchronous gateway API keeps
its existing maximum five-second command timeout.

Do not call the synchronous command API on the WebSocket adapter's own event
loop: it raises `DeviceError` promptly to prevent deadlock. Use
`await network.send_command(...)` there, or use a worker thread when invoking the
synchronous API from async code. Listener start, stop, and async commands belong
to the same event loop for the adapter's lifetime; cross-loop calls are rejected.

The returned result is correlated to that device/session's command ID. The
default command deadline is five seconds; disconnection fails pending commands
immediately. Timeout/cancellation removes pending command state. A result arriving
after its command expired is rejected and the session closes, rather than being
applied to a later command. No commands are persisted or replayed after reconnect.

`await network.stop()` closes authenticated and unauthenticated connections,
waits for all handlers, and releases the listener. It is idempotent and supports
later restart on the owning event loop. Listener status is published as an
immutable tuple under the gateway lock; cross-thread status reads never inspect
live listener sockets. Synchronous `gateway.stop()` also requests network closure using
the event loop's thread-safe callback; async owners must still await
`network.stop()` before closing the event loop to guarantee completed cleanup.

### Simulator

`WebSocketSimulatedDevice` extends the existing simulator and shares its command
execution logic. Use the M3.1 simulator's in-memory credential after pairing:

```python
device = WebSocketSimulatedDevice("desk-sim", uri="ws://127.0.0.1:8767")
device.credential = paired_tcp_simulator.credential
await device.run_forever()  # receives commands, sends heartbeats, reconnects
```

Disconnect the paired TCP simulator before connecting through WebSocket. The
CLI can perform the same owner-approved pairing, then switch transport:

```sh
python -m device_simulator --device-id desk-sim --websocket-uri ws://127.0.0.1:8767
```

The embedding application must explicitly start the network adapter first.
`run_forever` reconnects with bounded exponential delay (1–10 seconds); cancellation
or `await device.stop()` closes its socket and stops its reconnect loop. Credentials
remain in memory only. No ESP32 firmware, audio, industrial integration, or changes
to reasoning/memory are included. Older v1 peers can use the original command
subset; send the new display commands only to implementations supporting M3.2.

## Future ESP32 considerations and limitations

- Persistent ESP32 identity, protected credential storage, initial provisioning,
  and recovery belong to the future hardware milestone. Firmware must retain its
  identity and credential across reboot and durably store them before confirming
  pairing. A reboot reconnects using the existing credential and fresh challenge;
  it must not silently re-pair or resurrect a revoked identity. No firmware or
  hardware provisioning implementation is added in M3.2.
- Firmware must validate the same versioned schema and bounds before acting.
- Use a separately designed audio data channel; do not place microphone or audio
  streams in these JSON command frames.
- For LAN transport use M3.2 TLS with trusted server identity plus per-device
  challenge proofs. Keep initial credential provisioning on the owner-approved
  loopback flow; a device ID alone never authenticates.
- Provision and rotate per-device credentials through an explicit owner flow.
  Protect device-side credential storage and consider recovery from physical
  reset without allowing a revoked identity to silently return.
- Keep display, LED, sensor, and future movement commands individually bounded.
  Physical firmware must enforce local hardware safety even if Core is compromised.
- `athena.local` remains a planned hostname whose mDNS resolution is not guaranteed.
  `192.168.4.1` remains reserved for future ESP32 setup/recovery. Neither is
  implemented by this gateway.
- No ESP32 firmware, physical device testing, voice, Wi-Fi provisioning, home
  automation, LAN pairing, encrypted transport or motor control is included in
  M3.1.
