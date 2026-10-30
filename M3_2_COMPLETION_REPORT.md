# M3.2 — Network transport and device sessions

Branch: `feature/embodied-agent`. Changes are intentionally uncommitted and unpushed.

## Implemented

- Explicit-start asyncio WebSocket transport attached to `DeviceGateway` through
  `network_transport()`. Construction leaves networking disabled. Default bind:
  `127.0.0.1:8767`. Non-loopback binding requires `allow_lan=True` and a TLS server
  context; clients verify server certificates.
- Reuses the M3.1 registry, credential verifier, HMAC proof, replay tracking, and
  revocation. Credential provisioning remains the existing owner-approved
  loopback pairing flow. No second pairing system or registry was introduced.
- Fresh challenge authentication on every connection; five-second authentication
  deadline, fifteen-second application heartbeat deadline, explicit disconnect,
  fresh-session reconnect, cross-transport duplicate-session rejection, and
  immediate failure of commands when their session disconnects.
- Strict protocol validation, identity isolation, bounded text frames and queues,
  established-connection and command limits, and rejection of browser Origins.
- `display.set_text` and `display.set_status` commands with bounded exact schemas;
  existing expression, LED, status, and TCP behavior retained.
- Async simulator with the same simulated command execution path, status telemetry,
  heartbeat loop, bounded reconnect delay, cancellation cleanup, explicit stop,
  and CLI `--websocket-uri` option after existing owner-approved TCP pairing.
- Gateway status includes WebSocket sessions. Revocation and synchronous stop
  request network closure; async owners await transport stop to join all handlers.
- Added one runtime dependency: `websockets>=16.0,<17`; packaged the new module.
  No reasoning, memory, provider, operating-system, firmware, audio, or industrial
  integration changes.

## Modified and added files

| File | Change |
| --- | --- |
| `device_transport.py` | New async transport and session lifecycle |
| `device_gateway.py` | Transport configuration, status, stop/revoke bridges, duplicate-session exclusion |
| `device_protocol.py` | Display schemas; malformed type and deeply nested JSON rejection |
| `device_simulator.py` | Shared command handler; async WebSocket simulator and CLI switch |
| `test_device_transport.py` | Real TCP pairing, WebSocket, TLS, isolation, timeout and cleanup tests |
| `test_fixtures/localhost-cert.pem` | Public localhost test certificate |
| `test_fixtures/localhost-key.pem` | Public test-only private key; never use for deployment |
| `requirements.txt` | WebSocket dependency |
| `pyproject.toml` | Include transport module in distributions |
| `ATHENA_DEVICE_PROTOCOL.md` | Framing, lifecycle, pairing reuse, opt-in APIs, TLS, simulator and limitations |
| `M3_2_COMPLETION_REPORT.md` | This report |

## Review corrections

1. **Gateway routing:** the synchronous API looked only in TCP sessions, even
   though status included WebSocket sessions. It now captures the owning session
   under the gateway lock, releases the lock, and dispatches WebSocket work with
   `asyncio.run_coroutine_threadsafe`. The original maximum five-second gateway
   timeout includes dispatch queue time. The dispatch checks both the captured
   session identity and remaining deadline, preventing delivery to a replacement
   session or delivery after queue expiry. Same-owning-loop synchronous calls
   fail promptly with guidance to use the async API. Disconnect, timeout, and
   cancellation remove pending command state. TCP command routing is preserved.
2. **Heartbeat backpressure:** the old deadline enclosed only `recv`, leaving
   acknowledgement sends outside its scope. A single reschedulable asyncio timeout
   now encloses the complete authenticated receive/write loop. Per-session writes
   are serialized, bounded by sixteen command operations plus one handler
   acknowledgement. Expiry releases ownership and fails pending commands before
   closing; cancellation during an in-progress send aborts the socket. No detached
   watchdog/write tasks are created. Tests reproduce the real WebSocket
   flow-control pause state and verify cleanup and successful reconnection.
3. **Exclusive ownership:** directly constructed adapters were invisible to the
   gateway. Every successfully constructed adapter now registers under the gateway
   lock; a second adapter is explicitly rejected. The simplest supported topology
   is one TCP listener and one optional WebSocket adapter per gateway. Both use
   the same lock to claim identities. TCP sends its authenticated acknowledgement
   only after winning ownership, so simultaneous TCP/WebSocket authentication
   cannot report two successful connections. Direct adapters participate in
   routing, status, revocation, and shutdown. Cross-loop listener operations are
   explicitly rejected; construction still creates no listener or task.
4. **Listener status concurrency:** status previously dereferenced a live socket
   tuple after checking listener state. The owning event loop now publishes an
   immutable `(enabled, address)` tuple under the gateway lock. Shutdown publishes
   the disabled state before closing. Foreign threads read that tuple and never
   access listener sockets. Socket-address discovery occurs only during startup
   on the owning loop, without an intervening await. Status field names are
   preserved; the gateway also snapshots network identity membership under lock.
5. **Documentation:** legacy five-second reads are explicitly TCP-specific;
   optional WebSocket activation, explicit LAN/TLS requirements, the unchanged
   TCP-only Settings start button, synchronous routing constraints, and future
   ESP32 persistence/provisioning scope are now documented consistently.

Files changed for these corrections: `device_gateway.py`, `device_transport.py`,
`test_device_transport.py`, `ATHENA_DEVICE_PROTOCOL.md`, and this report. The
complete uncommitted M3.2 file list remains above. No reasoning, memory, provider,
firmware, M3.3 functionality, or additional dependencies were added.

### Regression tests added

- Synchronous gateway and real owner-authenticated Settings command endpoint
  delivery to WebSocket display sessions.
- Same-event-loop fail-fast behavior; routed command disconnect/shutdown/timeout
  cleanup; stale-session and expired dispatch-queue rejection.
- Heartbeat expiry during real write-flow-control pause; ownership and handler
  cleanup, pending command failure, and legitimate reconnection.
- Backpressured send timeout/cancellation cleanup; sixteen-writer capacity bound
  and graceful shutdown of all pending writers.
- Direct adapter registration and duplicate adapter rejection; direct adapter
  revocation/shutdown; simultaneous same-identity WebSocket/WebSocket and
  TCP/WebSocket authentication with exactly one winner.
- Concurrent status reads during sixty listener restarts, plus a sentinel proving
  status never reads an invalidated socket tuple; cross-loop operation rejection.
- The existing TLS test now exercises the supported registered single-adapter
  configuration with wildcard binding, certificate verification, and untrusted
  certificate rejection. Existing M3.1/M3.2 tests remain intact.

## Verification

- Focused M3.1/M3.2 suite: **100 passed, 1 warning in 36.87 seconds**.
  Command: `python -m pytest -vv test_device_transport.py test_device_gateway.py`
  with isolated workspace basetemp/cache and a diagnostic stack-dump timeout.
  There are nineteen additional regression cases relative to the initial 81-case
  focused suite; the existing TLS test uses the corrected single-adapter topology.
- Complete suite: **969 passed, 1 skipped, 1 warning in 90.89 seconds**.
  Command: `python -m pytest -q -rs` with isolated workspace basetemp/cache and
  a diagnostic stack-dump timeout. No test failures or stack-dump timeouts occurred.
- Existing skip: `test_agent.py:206`, because symlink creation requires privileges
  on this Windows installation. No platform skip or assertion was weakened.
- Existing warning: google-genai's `_UnionGenericAlias` deprecation on Python 3.14.
- `python -m pip check`: **No broken requirements found**.
- `python -m compileall -q` for all device source modules and the transport tests:
  **passed**. Additional in-memory compilation and source whitespace checks passed.
- `git -c core.whitespace=cr-at-eol diff --check`: **passed**. New untracked source
  files also passed explicit trailing-whitespace checks.
- Isolated wheel build with `python -m pip wheel --no-deps`: **passed**. Runtime
  dependencies were not changed by the corrections.
- Packaging verification: all four device modules in the wheel exactly match
  final checkout bytes; `websockets<17,>=16.0` is declared; tests, TLS certificates,
  and the public test-only private key are excluded.
- Final wheel SHA-256:
  `44c0de743315873ca5879d68cdf618b11f3bec1421fe198df6a0a7f21661a16d`.
- Branch confirmed as `feature/embodied-agent`. No commit or push performed.

## Operational limits and remaining risks

- Actually exercised locally on Windows with Python 3.14.7 and websockets 16.1.1.
  Linux, Python 3.12/3.13, and remote CI have not run for these uncommitted changes.
- TLS wildcard binding was exercised through localhost with verified certificates,
  including untrusted-certificate rejection. No physical device, separate LAN
  machine, firewall/router, or production certificate deployment was tested.
- LAN certificate/key issuance, renewal, private-CA distribution, and access policy
  remain operator responsibilities. The repository TLS fixture key is intentionally
  public and strictly for tests. Test fixtures and tests are excluded from the wheel.
- Initial credential provisioning remains loopback-only. Simulator credentials
  remain in memory and cannot be recovered from the verifier after process exit.
  This milestone does not add device credential storage, recovery, or rotation.
- WebSocket activation is an explicit embedding API; the existing Settings button
  still starts only M3.1 TCP. Async owners must await `network.stop()` before event
  loop termination; synchronous `gateway.stop()` requests closure without waiting
  for the async event loop.
- The existing atomic JSON registry remains intended for one gateway process per
  data directory. Writes are synchronous and bounded; this is a small local device
  transport, not a high-throughput or Internet-facing service. Replay history
  retains the existing bounded M3.1 message-ID window.
- New display commands are additive to v1; old devices should receive the original
  command subset. There is no capability negotiation or durable command replay.
  A late response to an expired command closes its session by policy.

## Commit readiness

**Ready for commit and cross-platform CI**, based on the completed local checks.
All four confirmed review defects have regression coverage and passed the focused
and complete suites. Windows/Python 3.14 verification does not establish Linux or
other Python-version compatibility; those CI jobs remain required before merge.
The changes remain uncommitted and unpushed, as requested.
