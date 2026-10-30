"""Loopback-only paired-device gateway. No device listener starts on construction."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import socketserver
from json_storage import _atomic_bytes
from threading import BoundedSemaphore, Event, RLock, Thread
from time import monotonic

import device_protocol as protocol
from device_protocol import ProtocolError

REGISTRY_VERSION = 1
MAX_DEVICES = 256
MAX_PENDING = 16
MAX_CONNECTIONS = 16
MAX_MESSAGE_IDS = 128
SOCKET_TIMEOUT = 5
PAIR_TIMEOUT = 120
HEARTBEAT_TIMEOUT = 15
COMMAND_TIMEOUT = 5


class DeviceError(RuntimeError):
    """Safe device gateway operation error."""


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate registry fields.")
        value[key] = item
    return value


class DeviceRegistry:
    """Atomic, local JSON registry. Stores verifier keys, never pairing tokens."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = RLock()

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        except FileNotFoundError:
            return []
        except (ValueError, UnicodeError) as exc:
            raise DeviceError("The device registry is invalid; it was left unchanged.") from exc
        if type(value) is not dict or set(value) != {"schema_version", "devices"}:
            raise DeviceError("The device registry schema is incompatible; it was left unchanged.")
        if type(value["schema_version"]) is not int or value["schema_version"] != REGISTRY_VERSION:
            raise DeviceError("The device registry schema is incompatible; it was left unchanged.")
        devices = value["devices"]
        if type(devices) is not list or len(devices) > MAX_DEVICES:
            raise DeviceError("The device registry is invalid; it was left unchanged.")
        seen = set()
        fields = {"device_id", "name", "status", "credential_verifier", "paired_at",
                  "revoked_at", "last_seen", "message_ids", "telemetry"}
        for record in devices:
            if type(record) is not dict or set(record) != fields:
                raise DeviceError("The device registry is invalid; it was left unchanged.")
            device_id = record["device_id"]
            if type(device_id) is not str or not protocol.DEVICE_PATTERN.fullmatch(device_id) or device_id in seen:
                raise DeviceError("The device registry is invalid; it was left unchanged.")
            seen.add(device_id)
            if (type(record["name"]) is not str or not 1 <= len(record["name"]) <= 64
                    or any(ord(char) < 32 or ord(char) == 127 for char in record["name"])):
                raise DeviceError("The device registry is invalid; it was left unchanged.")
            if record["status"] not in ("paired", "revoked"):
                raise DeviceError("The device registry is invalid; it was left unchanged.")
            verifier = record["credential_verifier"]
            if record["status"] == "paired":
                if type(verifier) is not str or len(verifier) != 64 or any(c not in "0123456789abcdef" for c in verifier):
                    raise DeviceError("The device registry is invalid; it was left unchanged.")
                if record["revoked_at"] is not None:
                    raise DeviceError("The device registry is invalid; it was left unchanged.")
            elif verifier is not None or type(record["revoked_at"]) is not str:
                raise DeviceError("The device registry is invalid; it was left unchanged.")
            if type(record["paired_at"]) is not str or (record["last_seen"] is not None and type(record["last_seen"]) is not str):
                raise DeviceError("The device registry is invalid; it was left unchanged.")
            telemetry = record["telemetry"]
            if telemetry is not None and (
                    type(telemetry) is not dict or set(telemetry) != {"expression", "led_state"}
                    or type(telemetry["expression"]) is not str
                    or telemetry["expression"] not in protocol.EXPRESSIONS
                    or type(telemetry["led_state"]) is not str
                    or telemetry["led_state"] not in protocol.LED_STATES):
                raise DeviceError("The device registry is invalid; it was left unchanged.")
            ids = record["message_ids"]
            if type(ids) is not list or len(ids) > MAX_MESSAGE_IDS or any(
                    type(item) is not str or not protocol.ID_PATTERN.fullmatch(item) for item in ids):
                raise DeviceError("The device registry is invalid; it was left unchanged.")
        return devices

    def _write(self, devices):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps({"schema_version": REGISTRY_VERSION, "devices": devices},
                         ensure_ascii=True, allow_nan=False, indent=2).encode("utf-8")
        _atomic_bytes(self.path, raw)

    def get(self, device_id):
        with self._lock:
            record = next((item for item in self._read() if item["device_id"] == device_id), None)
            return deepcopy(record)

    def public_devices(self):
        with self._lock:
            return [{key: value for key, value in item.items()
                     if key not in ("credential_verifier", "message_ids")}
                    for item in self._read()]

    def add(self, device_id, name, verifier, first_message_id):
        with self._lock:
            devices = self._read()
            if any(item["device_id"] == device_id for item in devices):
                raise DeviceError("This device identity is already registered or revoked.")
            if len(devices) >= MAX_DEVICES:
                raise DeviceError("Device registry is full.")
            now = _utc_now()
            devices.append({"device_id": device_id, "name": name, "status": "paired",
                            "credential_verifier": verifier.hex(), "paired_at": now,
                            "revoked_at": None, "last_seen": now, "message_ids": [first_message_id],
                            "telemetry": None})
            self._write(devices)

    def record_message(self, device_id, message_id, *, telemetry=None):
        with self._lock:
            devices = self._read()
            record = next((item for item in devices if item["device_id"] == device_id), None)
            if record is None or record["status"] != "paired":
                raise DeviceError("Device is not paired.")
            if message_id in record["message_ids"]:
                raise ProtocolError("Replayed message identifier.")
            record["message_ids"] = (record["message_ids"] + [message_id])[-MAX_MESSAGE_IDS:]
            record["last_seen"] = _utc_now()
            if telemetry is not None:
                record["telemetry"] = dict(telemetry)
            self._write(devices)

    def revoke(self, device_id):
        with self._lock:
            devices = self._read()
            record = next((item for item in devices if item["device_id"] == device_id), None)
            if record is None or record["status"] != "paired":
                raise DeviceError("Paired device was not found.")
            record["status"] = "revoked"
            record["credential_verifier"] = None
            record["revoked_at"] = _utc_now()
            record["last_seen"] = _utc_now()
            record["message_ids"] = []
            record["telemetry"] = None
            self._write(devices)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _key(credential):
    return hashlib.sha256(credential.encode("ascii")).digest()


def make_proof(verifier, device_id, request_id, challenge):
    message = f"athena-device-v{protocol.PROTOCOL_VERSION}\n{device_id}\n{request_id}\n{challenge}".encode("ascii")
    return hmac.new(verifier, message, hashlib.sha256).hexdigest()


class _Pending:
    def __init__(self, request_id, device_id, name, sock):
        self.request_id = request_id
        self.device_id = device_id
        self.name = name
        self.sock = sock
        self.created = monotonic()
        self.send_lock = RLock()
        self.approved = False
        self.verifier = None
        self.challenge = None


class _Session:
    def __init__(self, device_id, sock):
        self.device_id = device_id
        self.sock = sock
        self.send_lock = RLock()
        self.commands = {}
        self.last_seen = monotonic()


class DeviceGateway:
    """One explicit-start loopback protocol listener and bounded paired-device registry."""

    def __init__(self, registry_path, *, host="127.0.0.1", port=8766):
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("Device gateway requires a loopback IPv4 address.") from exc
        if address.version != 4 or not address.is_loopback:
            raise ValueError("Device gateway is loopback-only in M3.1.")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Use a port between 0 and 65535.")
        self.registry = DeviceRegistry(registry_path)
        self.host = str(address)
        self.port = port
        self._lock = RLock()
        self._pending = {}
        self._sessions = {}
        self._slots = BoundedSemaphore(MAX_CONNECTIONS)
        self._server = None
        self._thread = None
        self._network_transport = None

    @property
    def running(self):
        with self._lock:
            return self._server is not None

    @property
    def address(self):
        with self._lock:
            return None if self._server is None else self._server.server_address

    def start(self):
        with self._lock:
            if self._server is not None:
                return self.address
            self.registry.public_devices()
            gateway = self

            class Handler(socketserver.BaseRequestHandler):
                def handle(self):
                    gateway._handle(self.request, self.client_address)

            class Server(socketserver.ThreadingTCPServer):
                allow_reuse_address = True
                daemon_threads = True
                block_on_close = False
                request_queue_size = MAX_CONNECTIONS

                def verify_request(self, request, client_address):
                    try:
                        return ipaddress.ip_address(client_address[0]).is_loopback
                    except ValueError:
                        return False

                def process_request(self, request, client_address):
                    if not gateway._slots.acquire(blocking=False):
                        self.shutdown_request(request)
                        return
                    try:
                        super().process_request(request, client_address)
                    except Exception:
                        gateway._slots.release()
                        raise

                def process_request_thread(self, request, client_address):
                    try:
                        super().process_request_thread(request, client_address)
                    finally:
                        gateway._slots.release()

                def handle_error(self, request, client_address):
                    pass

            server = Server((self.host, self.port), Handler)
            self._server = server
            self.port = server.server_address[1]
            self._thread = Thread(target=server.serve_forever, name="athena-device-gateway", daemon=True)
            self._thread.start()
            return server.server_address

    def network_transport(self, **configuration):
        """Configure an opt-in WebSocket adapter; await its start/stop methods."""
        from device_transport import WebSocketTransport
        with self._lock:
            if self._network_transport is None:
                self._network_transport = WebSocketTransport(self, **configuration)
            elif configuration:
                raise DeviceError("Network transport is already configured.")
            return self._network_transport

    def stop(self):
        if self._network_transport is not None:
            self._network_transport.request_stop()
        with self._lock:
            server, thread = self._server, self._thread
            if server is None:
                return
            self._server = None
            self._thread = None
            sockets = [session.sock for session in self._sessions.values()]
            sockets += [pending.sock for pending in self._pending.values()]
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=3)

    def status(self):
        now = monotonic()
        with self._lock:
            sessions = dict(self._sessions)
            network = self._network_transport
            network_ids = frozenset(network._sessions) if network is not None else frozenset()
            if network is not None:
                sessions.update(network._sessions)
            pending = list(self._pending.values())
            running = self._server is not None
            address = None if self._server is None else self._server.server_address
        paired = []
        for record in self.registry.public_devices():
            session = sessions.get(record["device_id"])
            timeout = (network.heartbeat_timeout if network is not None
                       and record["device_id"] in network_ids else HEARTBEAT_TIMEOUT)
            connected = bool(session and now - session.last_seen <= timeout)
            paired.append({"device_id": record["device_id"], "name": record["name"],
                           "status": record["status"],
                           "connection": "connected" if connected and record["status"] == "paired" else "disconnected",
                           "last_seen": record["last_seen"], "paired_at": record["paired_at"],
                           "revoked_at": record["revoked_at"], "telemetry": record["telemetry"]})
        waiting = [{"request_id": item.request_id, "device_id": item.device_id,
                    "name": item.name, "state": "awaiting_confirmation" if item.approved else "pending"}
                   for item in pending if monotonic() - item.created <= PAIR_TIMEOUT]
        return {"enabled": running, "listen": f"{address[0]}:{address[1]}" if address else None,
                "transport": "loopback_unencrypted", "paired": paired, "pending": waiting,
                "network": network.status() if network else {"enabled": False}}

    def approve_pairing(self, request_id):
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None or monotonic() - pending.created > PAIR_TIMEOUT:
                raise DeviceError("Pairing request expired or was not found.")
            if pending.approved:
                raise DeviceError("Pairing request was already approved.")
            credential = secrets.token_urlsafe(32)
            verifier = _key(credential)
            challenge = secrets.token_urlsafe(32)
            pending.verifier = verifier
            pending.challenge = challenge
            pending.approved = True
            message = {"type": "pair_confirm", "protocol_version": protocol.PROTOCOL_VERSION,
                       "message_id": protocol.new_id(), "device_id": pending.device_id,
                       "request_id": pending.request_id, "challenge": challenge,
                       "device_credential": credential}
        try:
            _send(pending.sock, message, pending.send_lock)
        except OSError as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise DeviceError("Pairing connection was lost; no device was registered.") from exc
        return {"approved": True, "device_id": pending.device_id}

    def reject_pairing(self, request_id):
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            raise DeviceError("Pairing request expired or was not found.")
        try:
            _send_error(pending.sock, pending.device_id, "pairing_rejected", "Owner rejected pairing.")
            pending.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        return {"rejected": True, "device_id": pending.device_id}

    def revoke_device(self, device_id):
        self.registry.revoke(device_id)
        if self._network_transport is not None:
            self._network_transport.request_disconnect(device_id)
        with self._lock:
            session = self._sessions.pop(device_id, None)
            pending = [item for item in self._pending.values() if item.device_id == device_id]
            for item in pending:
                self._pending.pop(item.request_id, None)
        for item in pending:
            try:
                _send_error(item.sock, device_id, "revoked", "Device authorization was revoked.")
                item.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if session is not None:
            try:
                _send_error(session.sock, device_id, "revoked", "Device authorization was revoked.")
            except OSError:
                pass
            try:
                session.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return {"revoked": True, "device_id": device_id}

    def send_command(self, device_id, name, arguments=None, *, timeout=COMMAND_TIMEOUT):
        arguments = {} if arguments is None else arguments
        message = {"type": "command", "protocol_version": protocol.PROTOCOL_VERSION,
                   "message_id": protocol.new_id(), "device_id": device_id,
                   "command_id": protocol.new_id(), "name": name, "arguments": arguments}
        protocol.validate_message(message, direction="core")
        if type(timeout) not in (int, float) or not 0 < timeout <= COMMAND_TIMEOUT:
            raise ValueError("Command timeout must be between 0 and 5 seconds.")
        with self._lock:
            session = self._sessions.get(device_id)
            network = self._network_transport
            network_session = network._sessions.get(device_id) if network is not None else None
        if session is None and network_session is not None:
            return network.send_command_sync(device_id, name, arguments, timeout=timeout,
                                             session=network_session)
        if session is None or monotonic() - session.last_seen > HEARTBEAT_TIMEOUT:
            raise DeviceError("Paired device is not connected.")
        event = Event()
        with self._lock:
            session.commands[message["command_id"]] = {"event": event, "result": None}
        try:
            _send(session.sock, message, session.send_lock)
            if not event.wait(timeout):
                raise DeviceError("Device command timed out.")
            return session.commands[message["command_id"]]["result"]
        except OSError as exc:
            raise DeviceError("Device connection was lost.") from exc
        finally:
            with self._lock:
                session.commands.pop(message["command_id"], None)

    def _handle(self, sock, address):
        try:
            if not ipaddress.ip_address(address[0]).is_loopback:
                return
            sock.settimeout(SOCKET_TIMEOUT)
            reader = sock.makefile("rb")
            hello = _read(reader)
            if hello["type"] != "hello":
                raise ProtocolError("A hello message is required.")
            device_id, name = hello["device_id"], hello["display_name"]
            record = self.registry.get(device_id)
            if record and record["status"] == "revoked":
                _send_error(sock, device_id, "revoked", "Device authorization was revoked.")
                return
            if record:
                session = self._authenticate(sock, reader, record)
            else:
                _send(sock, {"type": "hello", "protocol_version": protocol.PROTOCOL_VERSION,
                             "message_id": protocol.new_id(), "device_id": device_id,
                             "status": "pair_required"})
                session = self._pair(sock, reader, device_id, name)
            if session is None:
                return
            with self._lock:
                if (device_id in self._sessions or (self._network_transport is not None
                        and device_id in self._network_transport._sessions)):
                    _send_error(sock, device_id, "already_connected", "Device is already connected.")
                    return
                self._sessions[device_id] = session
            _send(sock, {"type": "hello", "protocol_version": protocol.PROTOCOL_VERSION,
                         "message_id": protocol.new_id(), "device_id": device_id, "status": "authenticated"})
            self._receive_loop(sock, reader, session)
        except (ProtocolError, DeviceError, OSError, TimeoutError, ValueError):
            pass
        finally:
            with self._lock:
                for device_id, session in list(self._sessions.items()):
                    if session.sock is sock:
                        self._sessions.pop(device_id, None)
                for request_id, pending in list(self._pending.items()):
                    if pending.sock is sock:
                        self._pending.pop(request_id, None)
            try:
                sock.close()
            except OSError:
                pass

    def _pair(self, sock, reader, device_id, name):
        request = _read(reader)
        if request["type"] != "pair_request" or request["device_id"] != device_id:
            raise ProtocolError("Pairing request was expected.")
        with self._lock:
            if len(self._pending) >= MAX_PENDING or any(item.device_id == device_id for item in self._pending.values()):
                raise DeviceError("Pairing capacity is unavailable.")
            request_id = protocol.new_id()
            pending = _Pending(request_id, device_id, name, sock)
            self._pending[request_id] = pending
        _send(sock, {"type": "pair_request", "protocol_version": protocol.PROTOCOL_VERSION,
                     "message_id": protocol.new_id(), "device_id": device_id,
                     "request_id": request_id, "state": "pending"}, pending.send_lock)
        sock.settimeout(PAIR_TIMEOUT)
        confirmation = _read(reader)
        if confirmation["type"] != "pair_confirm" or confirmation["device_id"] != device_id:
            raise ProtocolError("Pairing confirmation was expected.")
        if not pending.approved or confirmation["request_id"] != request_id or confirmation["challenge"] != pending.challenge:
            raise ProtocolError("Pairing proof did not match.")
        expected = make_proof(pending.verifier, device_id, request_id, pending.challenge)
        if not hmac.compare_digest(expected, confirmation["proof"]):
            raise ProtocolError("Pairing proof did not match.")
        self.registry.add(device_id, name, pending.verifier, confirmation["message_id"])
        with self._lock:
            self._pending.pop(request_id, None)
        sock.settimeout(SOCKET_TIMEOUT)
        return _Session(device_id, sock)

    def _authenticate(self, sock, reader, record):
        device_id = record["device_id"]
        request_id = protocol.new_id()
        challenge = secrets.token_urlsafe(32)
        _send(sock, {"type": "hello", "protocol_version": protocol.PROTOCOL_VERSION,
                     "message_id": protocol.new_id(), "device_id": device_id,
                     "status": "challenge", "challenge": challenge, "request_id": request_id})
        confirmation = _read(reader)
        if confirmation["type"] != "pair_confirm" or confirmation["device_id"] != device_id:
            raise ProtocolError("Authentication proof was expected.")
        if confirmation["request_id"] != request_id or confirmation["challenge"] != challenge:
            raise ProtocolError("Authentication proof did not match.")
        verifier = bytes.fromhex(record["credential_verifier"])
        expected = make_proof(verifier, device_id, request_id, challenge)
        if not hmac.compare_digest(expected, confirmation["proof"]):
            raise ProtocolError("Authentication proof did not match.")
        self.registry.record_message(device_id, confirmation["message_id"])
        return _Session(device_id, sock)

    def _receive_loop(self, sock, reader, session):
        while True:
            message = _read(reader)
            if message["device_id"] != session.device_id:
                raise ProtocolError("Message identity changed within a connection.")
            self.registry.record_message(session.device_id, message["message_id"],
                                         telemetry=message.get("status"))
            session.last_seen = monotonic()
            if message["type"] == "heartbeat":
                _send(sock, {"type": "heartbeat", "protocol_version": protocol.PROTOCOL_VERSION,
                             "message_id": protocol.new_id(), "device_id": session.device_id, "accepted": True})
            elif message["type"] == "device_status":
                _send(sock, {"type": "device_status", "protocol_version": protocol.PROTOCOL_VERSION,
                             "message_id": protocol.new_id(), "device_id": session.device_id, "accepted": True})
            elif message["type"] == "command_result":
                with self._lock:
                    pending = session.commands.get(message["command_id"])
                    if pending is None:
                        raise ProtocolError("Unknown command result.")
                    pending["result"] = {"ok": message["ok"], "result": message["result"]}
                    pending["event"].set()
            else:
                raise ProtocolError("Message is not allowed in the authenticated state.")


def _send(sock, message, lock=None):
    raw = protocol.encode_line(message)
    if lock is None:
        sock.sendall(raw)
    else:
        with lock:
            sock.sendall(raw)


def _send_error(sock, device_id, code, message):
    _send(sock, {"type": "error", "protocol_version": protocol.PROTOCOL_VERSION,
                 "message_id": protocol.new_id(), "device_id": device_id,
                 "code": code, "message": message})


def _read(reader):
    raw = reader.readline(protocol.MAX_MESSAGE_BYTES + 1)
    if len(raw) > protocol.MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
        raise ProtocolError("Message frame is missing or too large.")
    return protocol.decode_line(raw, direction="device")
