"""Explicit-start asyncio WebSocket adapter for the existing paired-device gateway."""
import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
from dataclasses import dataclass, field
import hmac
import ipaddress
import math
import secrets
import ssl
from time import monotonic

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import device_protocol as protocol
from device_gateway import DeviceError, MAX_CONNECTIONS, make_proof


@dataclass
class _Session:
    socket: object
    commands: dict = field(default_factory=dict)
    last_seen: float = field(default_factory=monotonic)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _duration(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("Timeouts must be positive finite numbers.")
    return value


class WebSocketTransport:
    """One event-loop-owned listener; pairing stays in the M3.1 owner flow.

    LAN binding requires both allow_lan=True and an operator TLS server context.
    No listener, task, or event loop is created by construction.
    """

    def __init__(self, gateway, *, host="127.0.0.1", port=8767, allow_lan=False,
                 ssl_context=None, heartbeat_timeout=15, authentication_timeout=5):
        address = ipaddress.ip_address(host)
        if type(allow_lan) is not bool:
            raise ValueError("allow_lan must be a boolean.")
        if not address.is_loopback and not allow_lan:
            raise ValueError("LAN binding requires explicit allow_lan=True.")
        if ssl_context is not None and (
                not isinstance(ssl_context, ssl.SSLContext)
                or ssl_context.protocol != ssl.PROTOCOL_TLS_SERVER):
            raise ValueError("Use a TLS server SSLContext.")
        if not address.is_loopback and ssl_context is None:
            raise ValueError("LAN binding requires a TLS server context.")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Use a port between 0 and 65535.")
        self.gateway = gateway
        self.host, self.port = str(address), port
        self.ssl_context = ssl_context
        self.heartbeat_timeout = _duration(heartbeat_timeout)
        self.authentication_timeout = _duration(authentication_timeout)
        self._server = None
        self._loop = None
        self._lifecycle = asyncio.Lock()
        self._sessions = {}
        self._connections = set()
        self._listener_state = (False, None)
        with gateway._lock:
            if gateway._network_transport is not None:
                raise DeviceError("Only one WebSocket adapter per gateway is supported.")
            gateway._network_transport = self

    @property
    def running(self):
        with self.gateway._lock:
            return self._listener_state[0]

    @property
    def address(self):
        with self.gateway._lock:
            return self._listener_state[1]

    def status(self):
        with self.gateway._lock:
            enabled, address = self._listener_state
            return {"enabled": enabled, "address": address,
                    "tls": self.ssl_context is not None, "connected": sorted(self._sessions)}

    def _check_loop(self, *, bind=False):
        loop = asyncio.get_running_loop()
        with self.gateway._lock:
            if self._loop is not None and self._loop is not loop:
                raise DeviceError("Use the transport's owning event loop.")
            if bind:
                self._loop = loop
        return loop

    async def start(self):
        self._check_loop(bind=True)
        async with self._lifecycle:
            if self.running:
                return self.address
            self.gateway.registry.public_devices()  # Fail closed before binding.
            if self._server is not None:
                await self._server.wait_closed()
                self._server = None
            self._server = await serve(
                self._handle, self.host, self.port, ssl=self.ssl_context,
                origins=[None], compression=None, max_size=protocol.MAX_MESSAGE_BYTES,
                max_queue=4, open_timeout=self.authentication_timeout,
                close_timeout=1, ping_interval=None, server_header=None)
            # No await between acquiring the owned sockets and publishing state.
            address = self._server.sockets[0].getsockname()
            with self.gateway._lock:
                self.port = address[1]
                self._listener_state = (True, address)
            return self.address

    async def stop(self):
        self._check_loop()
        async with self._lifecycle:
            if self._server is not None:
                self._close_listener()
                await self._server.wait_closed()
                self._server = None

    def request_stop(self):
        """Thread-safe nonblocking bridge used by the synchronous gateway stop."""
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._close_listener)

    def _close_listener(self):
        with self.gateway._lock:
            self._listener_state = (False, None)
        if self._server is not None:
            self._server.close()

    def request_disconnect(self, device_id):
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._disconnect, device_id)

    def _disconnect(self, device_id):
        session = self._sessions.get(device_id)
        if session:
            # Closing the underlying connection wakes the handler and its callers.
            session.socket.transport.close()

    async def _read(self, socket):
        raw = await socket.recv()
        if type(raw) is not str:
            raise protocol.ProtocolError("Only WebSocket text messages are supported.")
        return protocol.decode_line(raw.encode("utf-8") + b"\n")

    async def _send(self, socket, device_id, kind, **fields):
        message = {"type": kind, "protocol_version": protocol.PROTOCOL_VERSION,
                   "message_id": protocol.new_id(), "device_id": device_id, **fields}
        await socket.send(protocol.encode_line(message)[:-1].decode("ascii"))

    async def _write(self, session, raw):
        # At most sixteen command writers and one handler acknowledgement wait
        # here. Cancellation during a send retires the socket; it isn't reused
        # after a partially delivered or backpressured command.
        async with session.write_lock:
            try:
                await session.socket.send(raw)
            except asyncio.CancelledError:
                session.socket.transport.abort()
                raise

    def _release_session(self, device_id, session):
        if session is None:
            return
        with self.gateway._lock:
            if self._sessions.get(device_id) is session:
                self._sessions.pop(device_id)
        for future in session.commands.values():
            if not future.done():
                future.set_exception(DeviceError("Device disconnected."))

    async def _handle(self, socket):
        if len(self._connections) >= MAX_CONNECTIONS:
            await socket.close(1013, "Device capacity reached")
            return
        self._connections.add(socket)
        device_id, session = None, None
        try:
            async with asyncio.timeout(self.authentication_timeout):
                hello = await self._read(socket)
                if hello["type"] != "hello":
                    raise protocol.ProtocolError("Hello required.")
                device_id = hello["device_id"]
                record = self.gateway.registry.get(device_id)
                if record is None or record["status"] != "paired":
                    raise DeviceError("Device must be paired through the owner flow.")
                request_id, challenge = protocol.new_id(), secrets.token_urlsafe(32)
                await self._send(socket, device_id, "hello", status="challenge",
                                 request_id=request_id, challenge=challenge)
                proof = await self._read(socket)
                if (proof["type"] != "pair_confirm" or proof["device_id"] != device_id
                        or proof["request_id"] != request_id or proof["challenge"] != challenge
                        or not hmac.compare_digest(proof["proof"], make_proof(
                            bytes.fromhex(record["credential_verifier"]), device_id,
                            request_id, challenge))):
                    raise DeviceError("Authentication failed.")
                self.gateway.registry.record_message(device_id, proof["message_id"])
                with self.gateway._lock:
                    if device_id in self._sessions or device_id in self.gateway._sessions:
                        raise DeviceError("Device is already connected.")
                    session = _Session(socket)
                    self._sessions[device_id] = session
                await self._send(socket, device_id, "hello", status="authenticated")
            await self._receive_loop(device_id, session)
        except (DeviceError, protocol.ProtocolError, ValueError, RecursionError):
            self._release_session(device_id, session)
            await socket.close(1008, "Device authentication or protocol rejected")
        except TimeoutError:
            self._release_session(device_id, session)
            await socket.close(1008, "Device session timed out")
        except (ConnectionClosed, OSError):
            pass
        except asyncio.CancelledError:
            socket.transport.abort()
            raise
        finally:
            self._release_session(device_id, session)
            self._connections.discard(socket)

    async def _receive_loop(self, device_id, session):
        loop = asyncio.get_running_loop()
        # One deadline covers both reads and acknowledgement writes. No detached
        # watchdog or write tasks survive session cleanup.
        async with asyncio.timeout(self.heartbeat_timeout) as watchdog:
            while True:
                message = await self._read(session.socket)
                kind = message["type"]
                if message["device_id"] != device_id or kind not in (
                        "heartbeat", "device_status", "command_result"):
                    raise protocol.ProtocolError("Invalid session message.")
                self.gateway.registry.record_message(device_id, message["message_id"],
                                                     telemetry=message.get("status"))
                if kind == "heartbeat":
                    session.last_seen = monotonic()
                    watchdog.reschedule(loop.time() + self.heartbeat_timeout)
                if kind in ("heartbeat", "device_status"):
                    reply = {"type": kind, "protocol_version": protocol.PROTOCOL_VERSION,
                             "message_id": protocol.new_id(), "device_id": device_id,
                             "accepted": True}
                    await self._write(session, protocol.encode_line(reply)[:-1].decode("ascii"))
                else:
                    future = session.commands.get(message["command_id"])
                    if future is None or future.done():
                        raise protocol.ProtocolError("Unknown or repeated command result.")
                    future.set_result({"ok": message["ok"], "result": message["result"]})

    def send_command_sync(self, device_id, name, arguments, *, timeout, session):
        """Bridge trusted synchronous callers without blocking the owning loop."""
        try:
            caller_loop = asyncio.get_running_loop()
        except RuntimeError:
            caller_loop = None
        if caller_loop is self._loop:
            raise DeviceError("Use await network.send_command() on the transport event loop.")
        if self._loop is None or not self._loop.is_running():
            raise DeviceError("Device transport is not running.")
        deadline = monotonic() + timeout
        coroutine = self.send_command(device_id, name, arguments, timeout=timeout,
                                      _session=session, _deadline=deadline)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        except RuntimeError as exc:
            coroutine.close()
            raise DeviceError("Device transport is not running.") from exc
        try:
            return future.result(timeout=max(0, deadline - monotonic()))
        except (TimeoutError, FutureCancelledError) as exc:
            future.cancel()
            raise DeviceError("Device command timed out or disconnected.") from exc
        except BaseException:
            future.cancel()
            raise

    async def send_command(self, device_id, name, arguments=None, *, timeout=5,
                           _session=None, _deadline=None):
        self._check_loop()
        timeout = _duration(timeout)
        command_id = protocol.new_id()
        message = {"type": "command", "protocol_version": protocol.PROTOCOL_VERSION,
                   "message_id": protocol.new_id(), "device_id": device_id,
                   "command_id": command_id, "name": name,
                   "arguments": {} if arguments is None else arguments}
        raw = protocol.encode_line(message)[:-1].decode("ascii")
        session = self._sessions.get(device_id)
        record = self.gateway.registry.get(device_id)
        if (session is None or (_session is not None and session is not _session)
                or record is None or record["status"] != "paired"
                or monotonic() - session.last_seen >= self.heartbeat_timeout):
            raise DeviceError("Paired device is not connected.")
        if len(session.commands) >= 16:
            raise DeviceError("Device command capacity reached.")
        remaining = timeout if _deadline is None else _deadline - monotonic()
        if remaining <= 0:
            raise DeviceError("Device command timed out before dispatch.")
        future = asyncio.get_running_loop().create_future()
        session.commands[command_id] = future
        try:
            async with asyncio.timeout(remaining):
                await self._write(session, raw)
                return await future
        except (TimeoutError, ConnectionClosed, OSError) as exc:
            raise DeviceError("Device command timed out or disconnected.") from exc
        finally:
            session.commands.pop(command_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Also retrieve failures if socket.send raised first.

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.stop()
