"""Python simulated device for the M3.1 local protocol."""
import argparse
import hashlib
import hmac
import socket
from threading import Event
from time import monotonic, sleep

import device_protocol as protocol
from device_gateway import DeviceError, make_proof
from device_protocol import ProtocolError


class SimulatedDevice:
    """Minimal client with safe simulated display/LED state; no GPIO or shell access."""

    def __init__(self, device_id="esp32-sim", name="Athena ESP32 simulator",
                 *, host="127.0.0.1", port=8766):
        if type(device_id) is not str or not protocol.DEVICE_PATTERN.fullmatch(device_id):
            raise ValueError("Invalid simulator device identifier.")
        self.device_id = device_id
        self.name = name
        self.host = host
        self.port = port
        self.credential = None
        self.expression = "neutral"
        self.led_state = "off"
        self.display_text = ""
        self.display_status = ""
        self._started = monotonic()
        self._sock = None
        self._reader = None
        self._stop = Event()

    @property
    def connected(self):
        return self._sock is not None

    def _send(self, message):
        if self._sock is None:
            raise DeviceError("Simulator is disconnected.")
        self._sock.sendall(protocol.device_line(message))

    def _read(self):
        if self._reader is None:
            raise DeviceError("Simulator is disconnected.")
        raw = self._reader.readline(protocol.MAX_MESSAGE_BYTES + 1)
        if len(raw) > protocol.MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
            raise ProtocolError("Invalid or oversized Core frame.")
        return protocol.decode_line(raw, direction="core")

    def _hello(self):
        return {"type": "hello", "protocol_version": protocol.PROTOCOL_VERSION,
                "message_id": protocol.new_id(), "device_id": self.device_id,
                "display_name": self.name}

    def connect(self, *, on_pair_request=None, timeout=5):
        self.disconnect()
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        sock.settimeout(timeout)
        self._sock = sock
        self._reader = sock.makefile("rb")
        try:
            self._send(self._hello())
            response = self._read()
            if response["type"] == "error":
                raise DeviceError(response["message"])
            if response["type"] != "hello":
                raise ProtocolError("Core did not start with hello.")
            if response["status"] == "pair_required":
                self._send({"type": "pair_request", "protocol_version": protocol.PROTOCOL_VERSION,
                            "message_id": protocol.new_id(), "device_id": self.device_id})
                sock.settimeout(120)
                request = self._read()
                if request["type"] == "error":
                    raise DeviceError(request["message"])
                if request["type"] != "pair_request":
                    raise ProtocolError("Core did not acknowledge pairing.")
                if on_pair_request is not None:
                    on_pair_request(request["request_id"])
                confirmation = self._read()
                if confirmation["type"] == "error":
                    raise DeviceError(confirmation["message"])
                if confirmation["type"] != "pair_confirm":
                    raise ProtocolError("Core did not confirm pairing.")
                self.credential = confirmation["device_credential"]
                proof = make_proof(hashlib.sha256(self.credential.encode("ascii")).digest(),
                                   self.device_id, confirmation["request_id"], confirmation["challenge"])
                self._send({"type": "pair_confirm", "protocol_version": protocol.PROTOCOL_VERSION,
                            "message_id": protocol.new_id(), "device_id": self.device_id,
                            "request_id": confirmation["request_id"], "challenge": confirmation["challenge"],
                            "proof": proof})
                response = self._read()
            elif response["status"] == "challenge":
                if not self.credential:
                    raise DeviceError("Simulator has no paired credential for this device identity.")
                proof = make_proof(hashlib.sha256(self.credential.encode("ascii")).digest(),
                                   self.device_id, response["request_id"], response["challenge"])
                self._send({"type": "pair_confirm", "protocol_version": protocol.PROTOCOL_VERSION,
                            "message_id": protocol.new_id(), "device_id": self.device_id,
                            "request_id": response["request_id"], "challenge": response["challenge"],
                            "proof": proof})
                response = self._read()
            if response["type"] == "error":
                raise DeviceError(response["message"])
            if response["type"] != "hello" or response["status"] != "authenticated":
                raise ProtocolError("Core authentication did not complete.")
            sock.settimeout(5)
            return True
        except Exception:
            self.disconnect()
            raise

    def heartbeat(self):
        self._send({"type": "heartbeat", "protocol_version": protocol.PROTOCOL_VERSION,
                    "message_id": protocol.new_id(), "device_id": self.device_id,
                    "uptime_seconds": int(monotonic() - self._started)})
        response = self._read()
        if response["type"] == "error" or response["type"] != "heartbeat" or not response["accepted"]:
            raise DeviceError("Core did not accept the heartbeat.")
        return True

    def report_status(self):
        status = {"expression": self.expression, "led_state": self.led_state}
        self._send({"type": "device_status", "protocol_version": protocol.PROTOCOL_VERSION,
                    "message_id": protocol.new_id(), "device_id": self.device_id, "status": status})
        response = self._read()
        if response["type"] == "error" or response["type"] != "device_status" or not response["accepted"]:
            raise DeviceError("Core did not accept device status.")
        return status

    def process_command(self, message=None):
        message = self._read() if message is None else message
        response = self._execute_command(message)
        self._send(response)
        return {"ok": response["ok"], "result": response["result"],
                "command_id": response["command_id"]}

    def _execute_command(self, message):
        message = protocol.validate_message(message, direction="core")
        if message["type"] == "error":
            raise DeviceError(message["message"])
        if message["type"] != "command" or message["device_id"] != self.device_id:
            raise ProtocolError("Expected a command for this device.")
        name, args = message["name"], message["arguments"]
        if name == "display.set_expression":
            self.expression = args["expression"]
            result = {"expression": self.expression}
        elif name == "display.set_text":
            self.display_text = args["text"]
            result = {}
        elif name == "display.set_status":
            self.display_status = args["status"]
            result = {}
        elif name == "led.set_state":
            self.led_state = args["state"]
            result = {"led_state": self.led_state}
        elif name == "device.get_status":
            result = {"expression": self.expression, "led_state": self.led_state}
        else:
            raise ProtocolError("Command is not allowed.")
        response = {"type": "command_result", "protocol_version": protocol.PROTOCOL_VERSION,
                    "message_id": protocol.new_id(), "device_id": self.device_id,
                    "command_id": message["command_id"], "ok": True, "result": result}
        return response

    def reconnect(self, *, on_pair_request=None, timeout=5):
        self.disconnect()
        return self.connect(on_pair_request=on_pair_request, timeout=timeout)

    def disconnect(self):
        sock, reader = self._sock, self._reader
        self._sock = self._reader = None
        if reader is not None:
            try:
                reader.close()
            except OSError:
                pass
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def run_forever(self, *, on_pair_request=None, heartbeat_interval=2):
        if type(heartbeat_interval) not in (int, float) or not 0.2 <= heartbeat_interval <= 3:
            raise ValueError("Heartbeat interval must be between 0.2 and 3 seconds.")
        self._stop.clear()
        backoff = 1
        while not self._stop.is_set():
            try:
                if not self.connected:
                    self.connect(on_pair_request=on_pair_request, timeout=5)
                    self._send({"type": "device_status", "protocol_version": protocol.PROTOCOL_VERSION,
                                "message_id": protocol.new_id(), "device_id": self.device_id,
                                "status": {"expression": self.expression, "led_state": self.led_state}})
                    self._sock.settimeout(heartbeat_interval)
                    backoff = 1
                try:
                    message = self._read()
                except socket.timeout:
                    self._send({"type": "heartbeat", "protocol_version": protocol.PROTOCOL_VERSION,
                                "message_id": protocol.new_id(), "device_id": self.device_id,
                                "uptime_seconds": int(monotonic() - self._started)})
                    continue
                if message["type"] == "command":
                    self.process_command(message)
                elif message["type"] in ("heartbeat", "device_status") and message["accepted"]:
                    continue
                elif message["type"] == "error":
                    raise DeviceError(message["message"])
                else:
                    raise ProtocolError("Unexpected Core message.")
            except (OSError, DeviceError, ProtocolError):
                self.disconnect()
                if self._stop.wait(backoff):
                    break
                backoff = min(10, backoff * 2)
        self.disconnect()

    def stop(self):
        self._stop.set()
        self.disconnect()


class WebSocketSimulatedDevice(SimulatedDevice):
    """Async client using the same identity, credential and simulated commands.

    Pair with SimulatedDevice through the existing owner flow, then transfer its
    in-memory credential. No credential provisioning happens over WebSocket.
    """

    def __init__(self, *args, uri="ws://127.0.0.1:8767", ssl_context=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.uri, self.ssl_context = uri, ssl_context
        self._ws = None
        self._run_task = None

    @property
    def connected(self):
        from websockets.protocol import State
        return self._ws is not None and self._ws.state == State.OPEN

    async def _async_send(self, message):
        await self._ws.send(protocol.device_line(message)[:-1].decode("ascii"))

    async def receive(self):
        raw = await self._ws.recv()
        if type(raw) is not str:
            raise ProtocolError("Expected WebSocket text message.")
        message = protocol.decode_line(raw.encode("utf-8") + b"\n", direction="core")
        if message["device_id"] != self.device_id:
            raise ProtocolError("Core message identity changed.")
        return message

    async def connect(self, *, timeout=5):
        import asyncio
        from websockets.asyncio.client import connect
        await self.disconnect()
        try:
            async with asyncio.timeout(timeout):
                options = ({"ssl": self.ssl_context}
                           if self.uri.startswith("wss:") and self.ssl_context is not None else {})
                self._ws = await connect(self.uri, proxy=None, compression=None,
                                         max_size=protocol.MAX_MESSAGE_BYTES, max_queue=4,
                                         close_timeout=1, ping_interval=None, **options)
                await self._async_send(self._hello())
                response = await self.receive()
                if response["type"] != "hello" or response["status"] != "challenge" or not self.credential:
                    raise DeviceError("A paired credential and authentication challenge are required.")
                proof = make_proof(hashlib.sha256(self.credential.encode("ascii")).digest(),
                                   self.device_id, response["request_id"], response["challenge"])
                await self._async_send({"type": "pair_confirm", "protocol_version": 1,
                    "message_id": protocol.new_id(), "device_id": self.device_id,
                    "request_id": response["request_id"], "challenge": response["challenge"],
                    "proof": proof})
                result = await self.receive()
                if result["type"] != "hello" or result["status"] != "authenticated":
                    raise DeviceError("Authentication did not complete.")
                return True
        except BaseException:
            await self.disconnect()
            raise

    async def heartbeat(self):
        await self._async_send({"type": "heartbeat", "protocol_version": 1,
            "message_id": protocol.new_id(), "device_id": self.device_id,
            "uptime_seconds": int(monotonic() - self._started)})
        response = await self.receive()
        if response["type"] != "heartbeat" or not response["accepted"]:
            raise DeviceError("Core did not accept heartbeat.")
        return True

    async def process_command(self, message=None):
        message = await self.receive() if message is None else message
        response = self._execute_command(message)
        await self._async_send(response)
        return {"ok": response["ok"], "result": response["result"],
                "command_id": response["command_id"]}

    async def report_status(self):
        status = {"expression": self.expression, "led_state": self.led_state}
        await self._async_send({"type": "device_status", "protocol_version": 1,
            "message_id": protocol.new_id(), "device_id": self.device_id, "status": status})
        response = await self.receive()
        if response["type"] != "device_status" or not response["accepted"]:
            raise DeviceError("Core did not accept device status.")
        return status

    async def reconnect(self, *, timeout=5):
        return await self.connect(timeout=timeout)

    async def disconnect(self):
        socket, self._ws = self._ws, None
        if socket is not None:
            await socket.close()

    async def run_forever(self, *, heartbeat_interval=2):
        import asyncio
        from websockets.exceptions import ConnectionClosed
        if type(heartbeat_interval) not in (int, float) or not 0.2 <= heartbeat_interval <= 3:
            raise ValueError("Heartbeat interval must be between 0.2 and 3 seconds.")
        if self._run_task is not None:
            raise DeviceError("Simulator run loop is already active.")
        self._run_task = asyncio.current_task()
        backoff = 1
        try:
            while True:
                try:
                    await self.connect()
                    backoff = 1
                    deadline = monotonic() + heartbeat_interval
                    while True:
                        remaining = deadline - monotonic()
                        if remaining <= 0:
                            await self._async_send({"type": "heartbeat", "protocol_version": 1,
                                "message_id": protocol.new_id(), "device_id": self.device_id,
                                "uptime_seconds": int(monotonic() - self._started)})
                            deadline = monotonic() + heartbeat_interval
                            remaining = heartbeat_interval
                        try:
                            async with asyncio.timeout(remaining):
                                message = await self.receive()
                        except TimeoutError:
                            continue
                        if message["type"] == "command":
                            await self.process_command(message)
                        elif message["type"] not in ("heartbeat", "device_status"):
                            raise ProtocolError("Unexpected Core message.")
                except (OSError, DeviceError, ProtocolError, ConnectionClosed, TimeoutError):
                    await self.disconnect()
                    await asyncio.sleep(backoff)
                    backoff = min(10, backoff * 2)
        finally:
            await self.disconnect()
            self._run_task = None

    async def stop(self):
        import asyncio
        task = self._run_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.disconnect()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Athena gateway loopback address")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--websocket-uri", help="Pair through TCP, then use ws:// or wss://")
    parser.add_argument("--device-id", default="esp32-sim")
    parser.add_argument("--name", default="Athena ESP32 simulator")
    args = parser.parse_args(argv)
    device = SimulatedDevice(args.device_id, args.name, host=args.host, port=args.port)

    def requested(request_id):
        print(f"Pairing request {request_id} awaits owner approval in Athena Settings.")

    print("Simulator credentials remain in this process; reconnect is supported until it exits.")
    print("Start the Athena device gateway in the owner-authenticated Settings page first.")
    try:
        if args.websocket_uri:
            import asyncio
            device.connect(on_pair_request=requested)
            device.disconnect()
            network_device = WebSocketSimulatedDevice(args.device_id, args.name,
                                                       uri=args.websocket_uri)
            network_device.credential = device.credential
            asyncio.run(network_device.run_forever())
        else:
            device.run_forever(on_pair_request=requested)
    except KeyboardInterrupt:
        device.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
