# Athena device protocol and pairing (M3.1)

M3.1 adds a loopback-only software foundation for a future desktop device. The
Core process remains responsible for provider configuration, memory, permissions,
and reasoning. The gateway is a component of the existing settings server, not a
second agent runtime. A Python simulator stands in for an ESP32; no firmware,
microphone, speaker, display hardware, sensors, servos, voice, or Wi-Fi
provisioning is implemented.

## Network boundary

The gateway is **disabled until an owner starts it** in the authenticated Athena
Settings page. It binds only to `127.0.0.1` on the configured device port (default
8766). The page's own optional LAN mode does not change this listener. The
gateway rejects non-loopback peers and has no LAN option in M3.1.

Device frames are not encrypted. Pairing credentials cross the loopback socket.
This is a local simulator foundation, not a claim that pairing is secure on a LAN.
Do not connect physical devices or forward the port. A future network transport
must provide authenticated encryption and peer identity before it can accept LAN
connections. The existing Settings page can itself use operator-configured TLS
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

Transport is one UTF-8 JSON object per LF-terminated TCP frame. Each frame is
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
| `led.set_state` | `{"state":"on\|off"}` | Change in-memory LED state |
| `device.get_status` | `{}` | Return the two simulated state fields |

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

## Heartbeats, limits and recovery

Every authenticated inbound message has a fresh UUID message ID. Core remembers a
bounded recent window of 128 IDs per paired device and rejects duplicates. Commands
also have one-time UUID command IDs; unknown or late results are rejected. The
simulator sends heartbeats every two seconds by default. Socket reads have a
five-second timeout; the owner page marks a connection disconnected when it has
not been seen for 15 seconds. Pair approval requests expire after two minutes.

Core caps the registry at 256 identities, pending pairings at 16 and simultaneous
connections at 16. Device messages are capped at 4096 bytes. These limits bound
storage and work; they are not a general denial-of-service defense for a LAN
service, because there is no LAN listener.

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

## Future ESP32 considerations and limitations

- Firmware must validate the same versioned schema and bounds before acting.
- Use a separately designed audio data channel; do not place microphone or audio
  streams in these JSON command frames.
- Add authenticated encryption and mutual device/server identity before any LAN
  transport. A pairing token or device ID alone does not secure an untrusted
  network.
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
