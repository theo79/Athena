"""Portable loopback tests for the versioned simulated-device protocol."""
import json
import socket
from threading import Event, Thread
from time import sleep, monotonic

import pytest

import device_gateway as gateway_module
import device_protocol as protocol
from device_gateway import DeviceError, DeviceGateway, DeviceRegistry
from device_protocol import ProtocolError, UnsupportedVersion
from device_simulator import SimulatedDevice


def message(kind="heartbeat", **extra):
    value = {"type": kind, "protocol_version": protocol.PROTOCOL_VERSION,
             "message_id": protocol.new_id(), "device_id": "sim-one"}
    value.update(extra)
    return value


@pytest.fixture
def gateway(tmp_path):
    service = DeviceGateway(tmp_path / "devices.json", port=0)
    yield service
    service.stop()


def pair(gateway, device_id="sim-one"):
    if not gateway.running:
        gateway.start()
    device = SimulatedDevice(device_id, "Test simulator", port=gateway.address[1])
    request_id, ready, errors = [], Event(), []
    def connect():
        try:
            device.connect(on_pair_request=lambda value: (request_id.append(value), ready.set()))
        except Exception as exc:
            errors.append(exc)
    thread = Thread(target=connect, daemon=True)
    thread.start()
    assert ready.wait(3), "simulator did not request pairing"
    assert gateway.status()["pending"]
    gateway.approve_pairing(request_id[0])
    thread.join(3)
    assert not thread.is_alive()
    assert not errors
    assert device.connected
    return device


def wait_until(predicate, timeout=2):
    end = monotonic() + timeout
    while monotonic() < end:
        if predicate():
            return True
        sleep(0.01)
    return bool(predicate())


def test_protocol_accepts_supported_device_frames():
    value = {"type":"hello","protocol_version":1,"message_id":protocol.new_id(),
             "device_id":"sim-one","display_name":"Desk simulator"}
    assert protocol.decode_line(protocol.device_line(value)) == value


def test_unsupported_protocol_version_is_rejected():
    value=message(uptime_seconds=0,protocol_version=2)
    with pytest.raises(UnsupportedVersion):
        protocol.validate_message(value)


@pytest.mark.parametrize("value", [
    {"type":"not-a-message","protocol_version":1,"message_id":protocol.new_id(),"device_id":"sim-one"},
    {"type":"heartbeat","protocol_version":1,"message_id":protocol.new_id(),"device_id":"sim-one","uptime_seconds":True},
    {"type":"heartbeat","protocol_version":1,"device_id":"sim-one","uptime_seconds":1},
    {"type":"hello","protocol_version":1,"message_id":protocol.new_id(),"device_id":"bad id","display_name":"x"},
    {"type":"device_status","protocol_version":1,"message_id":protocol.new_id(),"device_id":"sim-one","status":{"expression":[],"led_state":"off"}},
])
def test_invalid_message_types_fields_and_values_are_rejected(value):
    with pytest.raises(ProtocolError):
        protocol.validate_message(value)


def test_unknown_fields_are_rejected():
    with pytest.raises(ProtocolError):
        protocol.validate_message(message(extra="no"))


def test_duplicate_json_fields_are_rejected():
    mid=protocol.new_id()
    raw=(f'{{"type":"heartbeat","type":"heartbeat","protocol_version":1,"message_id":"{mid}",'
         '"device_id":"sim-one","uptime_seconds":1}').encode()+bytes([10])
    with pytest.raises(ProtocolError):
        protocol.decode_line(raw)


def test_malformed_and_unterminated_json_are_rejected():
    with pytest.raises(ProtocolError):
        protocol.decode_line(b'{broken'+bytes([10]))
    with pytest.raises(ProtocolError):
        protocol.decode_line(b'{}')


def test_oversized_message_is_rejected():
    with pytest.raises(ProtocolError):
        protocol.decode_line(b' '* (protocol.MAX_MESSAGE_BYTES+1)+bytes([10]))


@pytest.mark.parametrize("name,args", [
    ("os.execute",{}),("http.fetch",{"url":"http://localhost"}),
    ("display.set_expression",{"expression":"shell"}),
    ("led.set_state",{"state":"toggle"}),("device.get_status",{"path":"C:\\\\secret"}),
    ("device.get_status",None),
])
def test_command_allowlist_and_arguments_are_strict(name,args):
    value={"type":"command","protocol_version":1,"message_id":protocol.new_id(),
           "device_id":"sim-one","command_id":protocol.new_id(),"name":name,"arguments":args}
    with pytest.raises(ProtocolError):
        protocol.validate_message(value,direction="core")


def test_gateway_is_disabled_until_explicit_start_and_defaults_to_loopback(gateway):
    assert not gateway.running and gateway.address is None
    address=gateway.start()
    assert gateway.running and address[0]=="127.0.0.1"
    gateway.stop()
    assert not gateway.running


@pytest.mark.parametrize("host",["0.0.0.0","192.168.1.10","::1"])
def test_gateway_refuses_non_loopback_and_non_ipv4_bind(host,tmp_path):
    with pytest.raises(ValueError):
        DeviceGateway(tmp_path/"devices.json",host=host,port=0)


def test_unknown_device_cannot_send_commands(gateway):
    with pytest.raises(DeviceError):
        gateway.send_command("sim-one","device.get_status",{})


def test_pair_requires_owner_approval_and_persists_only_after_confirmation(gateway,tmp_path):
    gateway.start()
    device=pair(gateway)
    assert gateway.registry.path.exists()
    record=gateway.registry.get("sim-one")
    assert record["status"]=="paired" and record["credential_verifier"]
    assert device.credential not in gateway.registry.path.read_text()
    assert gateway.status()["paired"][0]["status"]=="paired"
    assert "credential_verifier" not in gateway.registry.public_devices()[0]

def test_explicit_pairing_rejection_does_not_register_device(gateway):
    gateway.start()
    device=SimulatedDevice("sim-reject",port=gateway.address[1])
    ready,request_id,errors=Event(),[],[]
    def connect():
        try: device.connect(on_pair_request=lambda value:(request_id.append(value),ready.set()))
        except Exception as exc: errors.append(exc)
    thread=Thread(target=connect,daemon=True);thread.start()
    assert ready.wait(3)
    gateway.reject_pairing(request_id[0])
    thread.join(3)
    assert not thread.is_alive() and errors
    assert gateway.registry.get("sim-reject") is None


def test_pairing_reconnection_uses_challenge_response(gateway):
    device=pair(gateway)
    device.disconnect()
    assert device.reconnect()
    assert device.credential
    assert device.heartbeat()
    assert gateway.status()["paired"][0]["connection"]=="connected"


def test_revoked_device_cannot_reconnect_or_pair_again(gateway):
    device=pair(gateway)
    device_id=device.device_id
    device.disconnect()
    gateway.revoke_device(device_id)
    assert gateway.registry.get(device_id)["status"]=="revoked"
    spoof=SimulatedDevice(device_id,port=gateway.port)
    with pytest.raises((DeviceError,ProtocolError,OSError)):
        spoof.connect()
    assert gateway.status()["paired"][0]["connection"]=="disconnected"


def test_spoofed_known_identity_without_credential_is_rejected(gateway):
    original=pair(gateway)
    original.disconnect()
    spoof=SimulatedDevice(original.device_id,port=gateway.port)
    with pytest.raises(DeviceError):
        spoof.connect()
    assert gateway.status()["paired"][0]["connection"]=="disconnected"


def test_public_registry_and_status_do_not_expose_credentials(gateway):
    device=pair(gateway)
    status=json.dumps(gateway.status())
    registry=json.dumps(gateway.registry.public_devices())
    assert device.credential not in status+registry
    assert "credential_verifier" not in status+registry
    assert device.credential not in gateway.registry.path.read_text()


def test_heartbeat_and_status_updates_are_acknowledged(gateway):
    device=pair(gateway)
    assert device.heartbeat()
    assert device.report_status()=={"expression":"neutral","led_state":"off"}
    assert gateway.registry.get(device.device_id)["last_seen"]
    assert gateway.status()["paired"][0]["telemetry"]=={"expression":"neutral","led_state":"off"}


def test_idle_connection_times_out(monkeypatch,tmp_path):
    monkeypatch.setattr(gateway_module,"SOCKET_TIMEOUT",0.1)
    gateway=DeviceGateway(tmp_path/"devices.json",port=0)
    try:
        device=pair(gateway)
        assert wait_until(lambda: gateway.status()["paired"][0]["connection"]=="disconnected")
    finally:
        gateway.stop()


def test_only_allowlisted_simulated_commands_execute(gateway):
    device=pair(gateway)
    response=[]
    command=Thread(target=lambda:response.append(gateway.send_command(
        device.device_id,"display.set_expression",{"expression":"happy"})),daemon=True)
    command.start()
    result=device.process_command()
    command.join(2)
    assert not command.is_alive()
    assert result["result"]=={"expression":"happy"}
    assert response==[{"ok":True,"result":{"expression":"happy"}}]
    assert device.expression=="happy"


def test_invalid_command_arguments_are_rejected_before_dispatch(gateway):
    device=pair(gateway)
    with pytest.raises(ProtocolError):
        gateway.send_command(device.device_id,"display.set_expression",{"expression":"run shell"})


def test_command_result_is_correlated_to_command_identifier(gateway):
    device=pair(gateway)
    response=[]
    command=Thread(target=lambda:response.append(gateway.send_command(
        device.device_id,"device.get_status",{})),daemon=True)
    command.start()
    result=device.process_command()
    command.join(2)
    assert result["command_id"]
    assert response==[{"ok":True,"result":{"expression":"neutral","led_state":"off"}}]


def test_replayed_message_identifier_disconnects_device(gateway):
    device=pair(gateway)
    repeated={"type":"heartbeat","protocol_version":1,"message_id":protocol.new_id(),
              "device_id":device.device_id,"uptime_seconds":1}
    device._send(repeated)
    assert device._read()["type"]=="heartbeat"
    device._send(repeated)
    assert wait_until(lambda:gateway.status()["paired"][0]["connection"]=="disconnected")


def test_registry_survives_gateway_restart(tmp_path):
    first=DeviceGateway(tmp_path/"devices.json",port=0)
    first.start()
    device=pair(first)
    credential=device.credential
    device.disconnect()
    first.stop()
    second=DeviceGateway(tmp_path/"devices.json",port=0)
    second.start()
    try:
        reconnect=SimulatedDevice(device.device_id,port=second.port)
        reconnect.credential=credential
        assert reconnect.connect()
        reconnect.disconnect()
    finally:
        second.stop()


def test_incompatible_registry_schema_fails_closed(tmp_path):
    path=tmp_path/"devices.json"
    path.write_text('{"schema_version":999,"devices":[]}',encoding="utf-8")
    service=DeviceGateway(path,port=0)
    before=path.read_bytes()
    with pytest.raises(DeviceError):
        service.start()
    assert path.read_bytes()==before and not service.running


def test_gateway_is_independent_of_provider_memory_and_cloud_services(gateway,monkeypatch):
    device=pair(gateway)
    monkeypatch.setenv("OPENAI_API_KEY","")
    monkeypatch.setenv("GEMINI_API_KEY","")
    monkeypatch.setenv("OPENROUTER_API_KEY","")
    assert device.heartbeat()
    assert sorted(path.name for path in gateway.registry.path.parent.iterdir()) == ["devices.json"]


def test_unsupported_external_host_never_gets_a_listener(gateway):
    address=gateway.start()
    assert address[0]=="127.0.0.1"
    assert gateway.status()["transport"]=="loopback_unencrypted"


def _settings_server(tmp_path):
    from provider_configuration import ProviderConfigurationService
    from web_settings import SettingsServer
    from threading import Thread
    service=ProviderConfigurationService(tmp_path/"settings.json",environ={})
    server=SettingsServer(service,owner_token="owner-token-for-device-tests-123456",
                          port=0,device_port=0,device_registry_path=tmp_path/"devices.json",
                          network_reader=lambda:{"hostname":"test","addresses":[],"reachability":"Not tested."})
    thread=Thread(target=server.serve_forever,kwargs={"poll_interval":0.01},daemon=True)
    thread.start()
    return server,thread


def _http(server,path,*,payload=None,auth=True,csrf=None):
    import http.client
    connection=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=3)
    headers={}
    if auth:
        headers["Authorization"]="Bearer owner-token-for-device-tests-123456"
    if payload is not None:
        headers.update({"Origin":server.url,"Content-Type":"application/json"})
        if csrf:
            headers["X-Athena-CSRF"]=csrf
    body=None if payload is None else json.dumps(payload)
    connection.request("POST" if payload is not None else "GET",path,body=body,headers=headers)
    response=connection.getresponse()
    result=(response.status,response.read().decode("utf-8"))
    connection.close()
    return result


def test_settings_owner_auth_controls_device_gateway_without_exposing_secrets(tmp_path):
    server,thread=_settings_server(tmp_path)
    try:
        assert not server.device_gateway.running
        assert _http(server,"/api/devices/start",payload={},auth=False)[0]==401
        status,body=_http(server,"/api/state")
        assert status==200
        state=json.loads(body)
        assert not state["devices"]["enabled"] and state["devices"]["paired"]==[]
        csrf=state["csrf"]
        assert _http(server,"/api/devices/start",payload={},csrf="bad")[0]==403
        status,body=_http(server,"/api/devices/start",payload={},csrf=csrf)
        assert status==200 and server.device_gateway.running
        assert "127.0.0.1" in body
        assert _http(server,"/api/devices/stop",payload={},csrf=csrf)[0]==200
        assert not server.device_gateway.running
        asset=_http(server,"/",auth=False)[1]
        assert "Paired devices" in asset and "not encrypted" in asset
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_settings_pairing_api_returns_no_pairing_credential(tmp_path):
    server,thread=_settings_server(tmp_path)
    errors=[]
    try:
        _,state_body=_http(server,"/api/state")
        csrf=json.loads(state_body)["csrf"]
        _http(server,"/api/devices/start",payload={},csrf=csrf)
        device=SimulatedDevice("web-sim",port=server.device_gateway.port)
        ready,request_ids=Event(),[]
        def connect():
            try: device.connect(on_pair_request=lambda value:(request_ids.append(value),ready.set()))
            except Exception as exc: errors.append(exc)
        client=Thread(target=connect,daemon=True);client.start()
        assert ready.wait(3)
        status,body=_http(server,"/api/devices/approve",payload={"request_id":request_ids[0]},csrf=csrf)
        assert status==200 and "credential" not in body
        client.join(3)
        assert not client.is_alive() and not errors
        status,body=_http(server,"/api/state")
        state=json.loads(body)
        assert state["devices"]["paired"][0]["device_id"]=="web-sim"
        assert device.credential not in body
        status,body=_http(server,"/api/devices/revoke",
                          payload={"device_id":"web-sim"},csrf=csrf)
        assert status==200 and "credential" not in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
