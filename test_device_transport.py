"""Real loopback WebSockets, M3.1 pairing, and portable asyncio lifecycle tests."""
import asyncio
from contextlib import asynccontextmanager
import ssl
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from device_gateway import DeviceError, DeviceGateway
from device_simulator import SimulatedDevice, WebSocketSimulatedDevice
import device_protocol as protocol
from test_device_gateway import pair, wait_until


def run(function):
    """Keep the suite independent of an asyncio pytest plugin."""
    from functools import wraps
    @wraps(function)
    def test(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return test


@asynccontextmanager
async def setup(tmp_path, *, client_ssl_context=None, **options):
    gateway = DeviceGateway(tmp_path / "devices.json", port=0)
    transport = gateway.network_transport(port=0, **options)
    tcp = None
    device = None
    try:
        # Use the actual M3.1 owner-approved pairing path, never seed credentials.
        tcp = await asyncio.to_thread(pair, gateway)
        tcp.disconnect()
        assert await asyncio.to_thread(wait_until, lambda: not gateway._sessions)
        await transport.start()
        scheme = "wss" if transport.ssl_context is not None else "ws"
        device = WebSocketSimulatedDevice(tcp.device_id,
            uri=f"{scheme}://127.0.0.1:{transport.port}", ssl_context=client_ssl_context)
        device.credential = tcp.credential
        await device.connect()
        yield gateway, transport, device
    finally:
        if device:
            await device.disconnect()
        if tcp:
            tcp.disconnect()
        await transport.stop()
        await asyncio.to_thread(gateway.stop)


async def disconnected(transport):
    async with asyncio.timeout(2):
        while transport._sessions:
            await asyncio.sleep(0.005)


@run
async def test_paired_connection_heartbeat_and_reconnect(tmp_path):
    async with setup(tmp_path) as (gateway, transport, device):
        assert transport.status()["connected"] == [device.device_id]
        assert await device.heartbeat()
        credential = device.credential
        await device.disconnect()
        await disconnected(transport)
        assert await device.reconnect()
        assert device.credential == credential
        assert await device.heartbeat()
        assert gateway.registry.get(device.device_id)["last_seen"]
        assert await device.report_status() == {"expression": "neutral", "led_state": "off"}
        assert gateway.registry.get(device.device_id)["telemetry"] == {
            "expression": "neutral", "led_state": "off"}
        assert gateway.status()["paired"][0]["connection"] == "connected"
        assert gateway.status()["network"]["connected"] == [device.device_id]


@run
async def test_heartbeat_keeps_session_alive_then_times_out(tmp_path):
    async with setup(tmp_path, heartbeat_timeout=0.25) as (_, transport, device):
        for _ in range(5):
            await asyncio.sleep(0.07)
            assert await device.heartbeat()
        await disconnected(transport)
        with pytest.raises(ConnectionClosed):
            await device.receive()
        assert not device.connected


@run
async def test_telemetry_does_not_replace_heartbeat(tmp_path):
    async with setup(tmp_path, heartbeat_timeout=0.25) as (_, transport, device):
        with pytest.raises(ConnectionClosed):
            for _ in range(20):
                await device._async_send({"type": "device_status", "protocol_version": 1,
                    "message_id": protocol.new_id(), "device_id": device.device_id,
                    "status": {"expression": "neutral", "led_state": "off"}})
                await device.receive()
                await asyncio.sleep(0.04)
        await disconnected(transport)


@pytest.mark.parametrize("identity,credential", [
    ("unknown", "unknown"), ("sim-one", None), ("sim-one", "wrong-credential")])
@run
async def test_unauthorized_rejected(tmp_path, identity, credential):
    async with setup(tmp_path) as (_, transport, original):
        await original.disconnect()
        await disconnected(transport)
        attacker = WebSocketSimulatedDevice(identity, uri=original.uri)
        attacker.credential = credential
        with pytest.raises((DeviceError, ConnectionClosed)):
            await attacker.connect()
        assert not attacker.connected
        assert not transport._sessions


@pytest.mark.parametrize("raw", ['{broken', '{}', b'{}', 'x' * 4097,
    '{"type":"hello","type":"hello"}', '[1]', '{"protocol_version":NaN}', '{"type":[]}'])
@run
async def test_malformed_unauthenticated_messages(tmp_path, raw):
    gateway = DeviceGateway(tmp_path / "devices.json")
    transport = gateway.network_transport(port=0)
    async with transport:
        async with connect(f"ws://127.0.0.1:{transport.port}", proxy=None) as socket:
            await socket.send(raw)
            with pytest.raises(ConnectionClosed):
                await socket.recv()
        assert not transport._sessions
    assert not transport._connections


@pytest.mark.parametrize("variant", ["identity", "replay", "out_of_sequence", "bad_result", "malformed"])
@run
async def test_authenticated_protocol_rejection(tmp_path, variant):
    async with setup(tmp_path) as (_, transport, device):
        message = {"type": "heartbeat", "protocol_version": 1,
            "message_id": protocol.new_id(), "device_id": device.device_id, "uptime_seconds": 1}
        if variant == "identity":
            message["device_id"] = "another-device"
        elif variant == "replay":
            await device._async_send(message)
            await device.receive()
        elif variant == "out_of_sequence":
            message = device._hello()
        elif variant == "bad_result":
            message = {"type": "command_result", "protocol_version": 1,
                "message_id": protocol.new_id(), "device_id": device.device_id,
                "command_id": protocol.new_id(), "ok": True, "result": {}}
        if variant == "malformed":
            await device._ws.send('{broken')
        else:
            await device._async_send(message)
        with pytest.raises(ConnectionClosed):
            await device.receive()
        await disconnected(transport)


@pytest.mark.parametrize("name,arguments,attribute,expected", [
    ("display.set_text", {"text": "Hello Athena — Καλημέρα"}, "display_text", "Hello Athena — Καλημέρα"),
    ("display.set_status", {"status": "Ready"}, "display_status", "Ready"),
    ("display.set_expression", {"expression": "happy"}, "expression", "happy")])
@run
async def test_display_delivery(tmp_path, name, arguments, attribute, expected):
    async with setup(tmp_path) as (_, transport, device):
        pending = asyncio.create_task(transport.send_command(device.device_id, name, arguments))
        await device.process_command()
        result = await pending
        assert result["ok"] is True
        assert getattr(device, attribute) == expected
        assert not transport._sessions[device.device_id].commands


@pytest.mark.parametrize("name,args", [
    ("display.set_text", {"text": "x" * 257}),
    ("display.set_text", {"text": "unsafe\ntext"}),
    ("display.set_status", {"status": "x" * 65}),
    ("display.set_text", {"text": ""}),
    ("display.set_status", {"status": "Ready", "extra": 1})])
@run
async def test_display_validation(tmp_path, name, args):
    async with setup(tmp_path) as (_, transport, device):
        with pytest.raises(protocol.ProtocolError):
            await transport.send_command(device.device_id, name, args)
        assert await device.heartbeat()


@run
async def test_multiple_device_isolation(tmp_path):
    async with setup(tmp_path) as (gateway, transport, first):
        tcp = await asyncio.to_thread(pair, gateway, "second")
        tcp.disconnect()
        assert await asyncio.to_thread(wait_until, lambda: "second" not in gateway._sessions)
        second = WebSocketSimulatedDevice("second", uri=first.uri)
        second.credential = tcp.credential
        try:
            await second.connect()
            pending = asyncio.create_task(transport.send_command(first.device_id,
                "display.set_text", {"text": "first only"}))
            command = await first.receive()
            # A second device cannot complete another session's command.
            await second._async_send({"type": "command_result", "protocol_version": 1,
                "message_id": protocol.new_id(), "device_id": second.device_id,
                "command_id": command["command_id"], "ok": True, "result": {}})
            with pytest.raises(ConnectionClosed):
                await second.receive()
            assert not pending.done()
            await first.process_command(command)
            assert (await pending)["ok"]
            assert first.display_text == "first only" and second.display_text == ""
            assert await first.heartbeat()
        finally:
            await second.disconnect()


@run
async def test_duplicate_connection_cannot_displace_session(tmp_path):
    async with setup(tmp_path) as (_, transport, original):
        duplicate = WebSocketSimulatedDevice(original.device_id, uri=original.uri)
        duplicate.credential = original.credential
        with pytest.raises(ConnectionClosed):
            await duplicate.connect()
        assert await original.heartbeat()
        assert len(transport._sessions) == 1


@run
async def test_authentication_deadline(tmp_path):
    gateway = DeviceGateway(tmp_path / "devices.json")
    transport = gateway.network_transport(port=0, authentication_timeout=0.1)
    async with transport:
        async with connect(f"ws://127.0.0.1:{transport.port}", proxy=None) as socket:
            with pytest.raises(ConnectionClosed):
                await socket.recv()
    assert not transport._connections


@run
async def test_shutdown_releases_commands_connections_and_listener(tmp_path):
    async with setup(tmp_path) as (gateway, transport, device):
        pending = asyncio.create_task(transport.send_command(device.device_id,
            "device.get_status", {}, timeout=10))
        await device.receive()
        gateway.stop()  # Also requests network shutdown when TCP wasn't running.
        await transport.stop()
        with pytest.raises(DeviceError, match="disconnected"):
            await pending
        assert not transport._connections and not transport._sessions
        assert not transport.running and transport.address is None
        await transport.stop()
        await transport.start()
        assert await device.reconnect()
        assert await device.heartbeat()


@run
async def test_revocation_closes_network_session(tmp_path):
    async with setup(tmp_path) as (gateway, transport, device):
        gateway.revoke_device(device.device_id)
        await disconnected(transport)
        with pytest.raises(ConnectionClosed):
            await device.reconnect()


@run
async def test_command_timeout_and_cancellation_cleanup(tmp_path):
    async with setup(tmp_path) as (_, transport, device):
        with pytest.raises(DeviceError):
            await transport.send_command(device.device_id, "device.get_status", timeout=0.03)
        await device.receive()  # Deliberately never acknowledge.
        pending = asyncio.create_task(transport.send_command(device.device_id, "device.get_status"))
        await device.receive()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not transport._sessions[device.device_id].commands


@run
async def test_simulator_run_loop_delivers_commands_and_cleans_up(tmp_path):
    async with setup(tmp_path, heartbeat_timeout=0.6) as (_, transport, device):
        await device.disconnect()
        await disconnected(transport)
        task = asyncio.create_task(device.run_forever(heartbeat_interval=0.2))
        try:
            async with asyncio.timeout(2):
                while not transport._sessions:
                    await asyncio.sleep(0.005)
            for _ in range(3):
                assert (await transport.send_command(device.device_id,
                    "display.set_text", {"text": "Running"}))["ok"]
                await asyncio.sleep(0.25)
            assert device.display_text == "Running"
        finally:
            await device.stop()
            assert task.done()
        await disconnected(transport)


@run
async def test_verified_tls_connection_and_untrusted_certificate_rejection(tmp_path):
    fixtures = Path(__file__).parent / "test_fixtures"
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(fixtures / "localhost-cert.pem", fixtures / "localhost-key.pem")
    client_context = ssl.create_default_context(cafile=str(fixtures / "localhost-cert.pem"))
    # Exercise the supported single registered adapter, with real wildcard TLS.
    async with setup(tmp_path, host="0.0.0.0", allow_lan=True,
                     ssl_context=server_context, client_ssl_context=client_context) as (_, transport, device):
        assert transport.status()["tls"] and await device.heartbeat()
        untrusted = WebSocketSimulatedDevice(device.device_id, uri=device.uri)
        untrusted.credential = device.credential
        with pytest.raises(ssl.SSLCertVerificationError):
            await untrusted.connect()
        assert not untrusted.connected


@run
async def test_browser_origin_is_rejected(tmp_path):
    gateway = DeviceGateway(tmp_path / "devices.json")
    async with gateway.network_transport(port=0) as transport:
        from websockets.exceptions import InvalidStatus
        with pytest.raises(InvalidStatus):
            async with connect(f"ws://127.0.0.1:{transport.port}",
                               origin="https://untrusted.example", proxy=None):
                pytest.fail("Browser origin accepted")


@run
async def test_stop_closes_unauthenticated_connections(tmp_path):
    gateway = DeviceGateway(tmp_path / "devices.json")
    transport = gateway.network_transport(port=0, authentication_timeout=30)
    await transport.start()
    try:
        async with connect(f"ws://127.0.0.1:{transport.port}", proxy=None) as socket:
            await transport.stop()
            with pytest.raises(ConnectionClosed):
                await socket.recv()
        assert not transport._connections and not transport._sessions
    finally:
        await transport.stop()


@run
async def test_simulator_automatically_reconnects_after_listener_restart(tmp_path):
    async with setup(tmp_path) as (_, transport, device):
        await device.disconnect()
        await disconnected(transport)
        task = asyncio.create_task(device.run_forever(heartbeat_interval=0.2))
        try:
            async with asyncio.timeout(2):
                while not transport._sessions:
                    await asyncio.sleep(0.005)
            await transport.stop()
            await transport.start()
            async with asyncio.timeout(4):
                while not transport._sessions:
                    await asyncio.sleep(0.005)
            assert (await transport.send_command(device.device_id,
                "display.set_status", {"status": "Reconnected"}))["ok"]
            assert device.display_status == "Reconnected"
        finally:
            await device.stop()
            assert task.done()


@pytest.mark.parametrize("name,arguments,attribute", [
    ("display.set_text", {"text": "TCP still works"}, "display_text"),
    ("display.set_status", {"status": "Ready"}, "display_status")])
@run
async def test_display_extensions_work_over_legacy_transport(tmp_path, name, arguments, attribute):
    gateway = DeviceGateway(tmp_path / "devices.json", port=0)
    device = await asyncio.to_thread(pair, gateway)
    try:
        pending = asyncio.create_task(asyncio.to_thread(gateway.send_command,
            device.device_id, name, arguments))
        await asyncio.to_thread(device.process_command)
        assert (await pending) == {"ok": True, "result": {}}
        assert getattr(device, attribute) == next(iter(arguments.values()))
    finally:
        device.disconnect()
        await asyncio.to_thread(gateway.stop)


def test_disabled_by_default_and_explicit_lan_configuration(tmp_path):
    gateway = DeviceGateway(tmp_path / "devices.json")
    assert gateway._network_transport is None
    transport = gateway.network_transport()
    assert transport.host == "127.0.0.1" and not transport.running
    from device_transport import WebSocketTransport
    for options in ({"host": "0.0.0.0"}, {"host": "0.0.0.0", "allow_lan": True}):
        with pytest.raises(ValueError):
            WebSocketTransport(gateway, **options)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    assert WebSocketTransport(DeviceGateway(tmp_path / "tls-devices.json"),
                              host="0.0.0.0", allow_lan=True,
                              ssl_context=context).host == "0.0.0.0"
    for timeout in (0, -1, True, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            WebSocketTransport(gateway, heartbeat_timeout=timeout)


@run
async def test_synchronous_gateway_routes_websocket_display_commands(tmp_path):
    async with setup(tmp_path) as (gateway, transport, device):
        pending = asyncio.create_task(asyncio.to_thread(gateway.send_command,
            device.device_id, "display.set_text", {"text": "Gateway route"}))
        await device.process_command()
        assert await pending == {"ok": True, "result": {}}
        assert device.display_text == "Gateway route"
        assert not transport._sessions[device.device_id].commands


@run
async def test_queued_gateway_command_cannot_cross_reconnected_session(tmp_path):
    async with setup(tmp_path) as (_, transport, device):
        previous = transport._sessions[device.device_id]
        await device.disconnect()
        await disconnected(transport)
        await device.reconnect()
        with pytest.raises(DeviceError):
            await asyncio.to_thread(transport.send_command_sync, device.device_id,
                "display.set_text", {"text": "Stale command"}, timeout=1, session=previous)
        assert device.display_text == ""
        assert await device.heartbeat()


@run
async def test_gateway_command_expired_in_dispatch_queue_is_never_sent(tmp_path):
    async with setup(tmp_path) as (_, transport, device):
        session = transport._sessions[device.device_id]
        with pytest.raises(DeviceError, match="before dispatch"):
            await transport.send_command(device.device_id, "display.set_text",
                {"text": "Expired"}, _session=session, _deadline=monotonic() - 1)
        assert not session.commands
        assert await device.heartbeat()  # Next frame is the acknowledgement, not a stale command.


@pytest.mark.parametrize("action", ["cancel", "timeout"])
@run
async def test_backpressured_command_cancellation_retires_socket_without_leaks(tmp_path, action):
    async with setup(tmp_path) as (_, transport, device):
        session = transport._sessions[device.device_id]
        socket = session.socket
        socket.pause_writing()
        pending = asyncio.create_task(transport.send_command(device.device_id,
            "device.get_status", timeout=0.1 if action == "timeout" else 3))
        try:
            await device.receive()  # Frame delivered, send still blocked on drain.
            if action == "cancel":
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                with pytest.raises(DeviceError):
                    await pending
            async with asyncio.timeout(2):
                while transport._connections:
                    await asyncio.sleep(0.005)
            assert not transport._sessions and not session.commands
            assert not transport._server.handlers
        finally:
            if socket.paused:
                socket.resume_writing()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        assert await device.reconnect()
        assert await device.heartbeat()


@run
async def test_backpressured_command_writers_are_bounded_and_shutdown_joins_them(tmp_path):
    async with setup(tmp_path) as (_, transport, device):
        session = transport._sessions[device.device_id]
        socket = session.socket
        socket.pause_writing()
        commands = [asyncio.create_task(transport.send_command(device.device_id,
                    "device.get_status")) for _ in range(16)]
        try:
            await device.receive()
            async with asyncio.timeout(2):
                while len(session.commands) < 16:
                    await asyncio.sleep(0)
            with pytest.raises(DeviceError, match="capacity"):
                await transport.send_command(device.device_id, "device.get_status")
            await transport.stop()
            results = await asyncio.gather(*commands, return_exceptions=True)
            assert all(isinstance(result, DeviceError) for result in results)
            assert not session.commands and not transport._connections
            assert all(command.done() for command in commands)
        finally:
            if socket.paused:
                socket.resume_writing()
            for command in commands:
                if not command.done():
                    command.cancel()
            await asyncio.gather(*commands, return_exceptions=True)


@run
async def test_cross_loop_listener_operations_are_rejected(tmp_path):
    gateway = DeviceGateway(tmp_path / "devices.json")
    transport = gateway.network_transport(port=0)
    async with transport:
        for operation in (transport.start, transport.stop):
            with pytest.raises(DeviceError, match="owning event loop"):
                await asyncio.to_thread(lambda: asyncio.run(operation()))
        assert transport.running


@run
async def test_gateway_same_loop_call_fails_without_deadlock(tmp_path):
    async with setup(tmp_path) as (gateway, transport, device):
        started = monotonic()
        with pytest.raises(DeviceError, match="await network.send_command"):
            gateway.send_command(device.device_id, "device.get_status")
        assert monotonic() - started < 0.5
        assert await device.heartbeat()
        assert not transport._sessions[device.device_id].commands


@pytest.mark.parametrize("action", ["disconnect", "shutdown", "timeout"])
@run
async def test_gateway_routed_commands_cleanup_on_disconnect_shutdown_timeout(tmp_path, action):
    async with setup(tmp_path) as (gateway, transport, device):
        pending = asyncio.create_task(asyncio.to_thread(gateway.send_command,
            device.device_id, "device.get_status", {}, timeout=0.1 if action == "timeout" else 3))
        await device.receive()
        session = transport._sessions[device.device_id]
        if action == "disconnect":
            await device.disconnect()
        elif action == "shutdown":
            await transport.stop()
        with pytest.raises(DeviceError):
            await asyncio.wait_for(pending, 1)
        async with asyncio.timeout(1):
            while session.commands:
                await asyncio.sleep(0)


@run
async def test_settings_command_endpoint_routes_to_websocket_session(tmp_path):
    import json
    from test_device_gateway import _settings_server, _http
    server, thread = _settings_server(tmp_path)
    transport = server.device_gateway.network_transport(port=0)
    device = None
    tcp = None
    try:
        tcp = await asyncio.to_thread(pair, server.device_gateway)
        tcp.disconnect()
        assert await asyncio.to_thread(wait_until, lambda: not server.device_gateway._sessions)
        await transport.start()
        device = WebSocketSimulatedDevice(tcp.device_id, uri=f"ws://127.0.0.1:{transport.port}")
        device.credential = tcp.credential
        await device.connect()
        status, body = await asyncio.to_thread(_http, server, "/api/state")
        assert status == 200
        pending = asyncio.create_task(asyncio.to_thread(_http, server, "/api/devices/command",
            payload={"device_id": device.device_id, "name": "display.set_status",
                     "arguments": {"status": "Settings route"}}, csrf=json.loads(body)["csrf"]))
        await device.process_command()
        status, body = await pending
        assert status == 200 and json.loads(body)["ok"] is True
        assert device.display_status == "Settings route"
    finally:
        if device:
            await device.disconnect()
        if tcp:
            tcp.disconnect()
        await transport.stop()
        await asyncio.to_thread(server.shutdown)
        await asyncio.to_thread(server.server_close)
        thread.join(3)


@run
async def test_heartbeat_expiry_releases_backpressured_writer_and_allows_reconnect(tmp_path):
    async with setup(tmp_path, heartbeat_timeout=0.25) as (_, transport, device):
        session = transport._sessions[device.device_id]
        socket = session.socket
        socket.pause_writing()  # Reproduce the actual TCP high-water flow-control state.
        try:
            # The acknowledgement can reach the client while its send is blocked
            # draining the server buffer. No more heartbeats follow.
            assert await device.heartbeat()
            pending = asyncio.create_task(transport.send_command(device.device_id, "device.get_status"))
            async with asyncio.timeout(2):
                while transport._sessions or transport._connections:
                    await asyncio.sleep(0.005)
            with pytest.raises(DeviceError):
                await pending
            assert not session.commands
            assert not transport._server.handlers
        finally:
            if socket.paused:
                socket.resume_writing()
        assert await device.reconnect()
        assert await device.heartbeat()


def test_direct_adapter_registers_and_duplicate_adapters_are_rejected(tmp_path):
    from device_transport import WebSocketTransport
    gateway = DeviceGateway(tmp_path / "devices.json")
    direct = WebSocketTransport(gateway, port=0)
    assert gateway.network_transport() is direct
    with pytest.raises(DeviceError, match="one WebSocket adapter"):
        WebSocketTransport(gateway, port=0)
    with pytest.raises(DeviceError, match="already configured"):
        gateway.network_transport(port=0)


@pytest.mark.parametrize("action", ["revoke", "stop"])
@run
async def test_direct_adapter_participates_in_gateway_lifecycle(tmp_path, action):
    from device_transport import WebSocketTransport
    gateway = DeviceGateway(tmp_path / "devices.json", port=0)
    transport = WebSocketTransport(gateway, port=0)
    tcp = await asyncio.to_thread(pair, gateway)
    tcp.disconnect()
    device = None
    try:
        assert await asyncio.to_thread(wait_until, lambda: not gateway._sessions)
        await transport.start()
        device = WebSocketSimulatedDevice(tcp.device_id, uri=f"ws://127.0.0.1:{transport.port}")
        device.credential = tcp.credential
        await device.connect()
        if action == "revoke":
            await asyncio.to_thread(gateway.revoke_device, device.device_id)
        else:
            await asyncio.to_thread(gateway.stop)
        await disconnected(transport)
        with pytest.raises(ConnectionClosed):
            await device.receive()
    finally:
        if device:
            await device.disconnect()
        await transport.stop()
        await asyncio.to_thread(gateway.stop)


@run
async def test_simultaneous_websocket_authentication_has_one_owner(tmp_path):
    async with setup(tmp_path) as (_, transport, original):
        await original.disconnect()
        await disconnected(transport)
        devices = [WebSocketSimulatedDevice(original.device_id, uri=original.uri) for _ in range(2)]
        for device in devices:
            device.credential = original.credential
        try:
            results = await asyncio.gather(*(device.connect() for device in devices), return_exceptions=True)
            assert sum(value is True for value in results) == 1
            assert len(transport._sessions) == 1
            winner = devices[next(i for i, result in enumerate(results) if result is True)]
            assert await winner.heartbeat()
        finally:
            await asyncio.gather(*(device.disconnect() for device in devices))


@run
async def test_simultaneous_tcp_websocket_authentication_has_one_owner(tmp_path):
    async with setup(tmp_path) as (gateway, transport, device):
        await device.disconnect()
        await disconnected(transport)
        tcp = SimulatedDevice(device.device_id, port=gateway.port)
        tcp.credential = device.credential
        try:
            results = await asyncio.gather(device.connect(), asyncio.to_thread(tcp.connect),
                                           return_exceptions=True)
            assert sum(result is True for result in results) == 1
            assert len(gateway._sessions) + len(transport._sessions) == 1
            if results[0] is True:
                assert await device.heartbeat()
            else:
                assert await asyncio.to_thread(tcp.heartbeat)
        finally:
            tcp.disconnect()
            await device.disconnect()


@run
async def test_status_snapshot_is_safe_during_cross_thread_restart(tmp_path):
    gateway = DeviceGateway(tmp_path / "devices.json")
    transport = gateway.network_transport(port=0)
    stop = Event()
    errors = []
    reads = []
    def read_status():
        while not stop.is_set():
            try:
                state = gateway.status()["network"]
                assert state["enabled"] == (state["address"] is not None)
                if not reads:
                    reads.append(True)
            except BaseException as exc:
                errors.append(exc)
                stop.set()
    reader = Thread(target=read_status)
    reader.start()
    try:
        for _ in range(60):
            await transport.start()
            await asyncio.sleep(0)
            await transport.stop()
        assert reads and not errors
        # A foreign thread must not access listener sockets at all, even if
        # shutdown has already invalidated them. Only the stored tuple is read.
        class ClosedListener:
            @property
            def sockets(self):
                raise AssertionError("status accessed invalidated listener sockets")
        original = transport._server
        transport._server = ClosedListener()
        try:
            assert transport.address is None and not transport.running
            assert transport.status()["address"] is None
        finally:
            transport._server = original
    finally:
        stop.set()
        reader.join(3)
        await transport.stop()
    assert not reader.is_alive()
