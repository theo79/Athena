"""Small bounded JSON-line protocol shared by Athena Core and device simulators."""
import json
import re
from uuid import UUID

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 4096
ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
DEVICE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
EXPRESSIONS = frozenset({"neutral", "happy", "curious", "thinking", "sleepy"})
LED_STATES = frozenset({"on", "off"})
COMMANDS = frozenset({"display.set_expression", "led.set_state", "device.get_status", "display.set_text", "display.set_status"})


class ProtocolError(ValueError):
    """A malformed, unsupported, or out-of-sequence protocol message."""


class UnsupportedVersion(ProtocolError):
    """The peer selected a protocol version this implementation cannot handle."""


def new_id():
    from uuid import uuid4
    return str(uuid4())


def _id(value, label):
    if type(value) is not str or not ID_PATTERN.fullmatch(value):
        raise ProtocolError(f"Invalid {label}.")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError as exc:
        raise ProtocolError(f"Invalid {label}.") from exc
    return value


def _text(value, label, maximum, *, pattern=None):
    if type(value) is not str or not value or len(value) > maximum:
        raise ProtocolError(f"Invalid {label}.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ProtocolError(f"Invalid {label}.")
    if pattern is not None and not pattern.fullmatch(value):
        raise ProtocolError(f"Invalid {label}.")
    return value


def _exact_fields(message, required, optional=()):
    if type(message) is not dict or set(message) - set(required) - set(optional) or not set(required).issubset(message):
        raise ProtocolError("Invalid protocol message fields.")


def _base(message):
    version = message.get("protocol_version")
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise UnsupportedVersion("Unsupported protocol version.")
    _id(message.get("message_id"), "message identifier")
    _text(message.get("device_id"), "device identifier", 64, pattern=DEVICE_PATTERN)


def _arguments(name, arguments):
    if name == "display.set_expression":
        _exact_fields(arguments, {"expression"})
        if type(arguments["expression"]) is not str or arguments["expression"] not in EXPRESSIONS:
            raise ProtocolError("Invalid expression.")
    elif name in ("display.set_text", "display.set_status"):
        field = "text" if name == "display.set_text" else "status"
        _exact_fields(arguments, {field})
        _text(arguments[field], field, 256 if field == "text" else 64)
    elif name == "led.set_state":
        _exact_fields(arguments, {"state"})
        if type(arguments["state"]) is not str or arguments["state"] not in LED_STATES:
            raise ProtocolError("Invalid LED state.")
    elif name == "device.get_status":
        _exact_fields(arguments, set())
    else:
        raise ProtocolError("Command is not allowed.")


def _status(value):
    _exact_fields(value, {"expression", "led_state"})
    if (type(value["expression"]) is not str or value["expression"] not in EXPRESSIONS
            or type(value["led_state"]) is not str or value["led_state"] not in LED_STATES):
        raise ProtocolError("Invalid simulated status.")


def validate_message(message, *, direction="device"):
    """Validate exact fields and values; direction is the sender relative to Core."""
    if direction not in ("device", "core") or type(message) is not dict:
        raise ProtocolError("Invalid protocol message.")
    kind = message.get("type")
    if direction == "device":
        schemas = {
            "hello": ({"type", "protocol_version", "message_id", "device_id", "display_name"}, set()),
            "pair_request": ({"type", "protocol_version", "message_id", "device_id"}, set()),
            "pair_confirm": ({"type", "protocol_version", "message_id", "device_id", "request_id", "challenge", "proof"}, set()),
            "heartbeat": ({"type", "protocol_version", "message_id", "device_id", "uptime_seconds"}, set()),
            "device_status": ({"type", "protocol_version", "message_id", "device_id", "status"}, set()),
            "command_result": ({"type", "protocol_version", "message_id", "device_id", "command_id", "ok", "result"}, set()),
        }
    else:
        schemas = {
            "hello": ({"type", "protocol_version", "message_id", "device_id", "status"}, {"challenge", "request_id"}),
            "pair_request": ({"type", "protocol_version", "message_id", "device_id", "request_id", "state"}, set()),
            "pair_confirm": ({"type", "protocol_version", "message_id", "device_id", "request_id", "challenge", "device_credential"}, set()),
            "heartbeat": ({"type", "protocol_version", "message_id", "device_id", "accepted"}, set()),
            "device_status": ({"type", "protocol_version", "message_id", "device_id", "accepted"}, set()),
            "command": ({"type", "protocol_version", "message_id", "device_id", "command_id", "name", "arguments"}, set()),
            "error": ({"type", "protocol_version", "message_id", "device_id", "code", "message"}, set()),
        }
    if type(kind) is not str:
        raise ProtocolError("Invalid protocol message type.")
    schema = schemas.get(kind)
    if schema is None:
        raise ProtocolError("Unknown protocol message type.")
    required, optional = schema
    _exact_fields(message, required, optional)
    _base(message)

    if direction == "device":
        if kind == "hello":
            _text(message["display_name"], "display name", 64)
        elif kind == "pair_confirm":
            _id(message["request_id"], "pairing request identifier")
            _text(message["challenge"], "challenge", 128, pattern=re.compile(r"^[A-Za-z0-9_-]+$"))
            _text(message["proof"], "proof", 64, pattern=re.compile(r"^[0-9a-f]{64}$"))
        elif kind == "heartbeat":
            if type(message["uptime_seconds"]) is not int or not 0 <= message["uptime_seconds"] <= 2**31 - 1:
                raise ProtocolError("Invalid heartbeat.")
        elif kind == "device_status":
            _status(message["status"])
        elif kind == "command_result":
            _id(message["command_id"], "command identifier")
            if type(message["ok"]) is not bool:
                raise ProtocolError("Invalid command result.")
            _exact_fields(message["result"], set(), {"expression", "led_state", "error"})
            result = message["result"]
            if "expression" in result and (type(result["expression"]) is not str or result["expression"] not in EXPRESSIONS):
                raise ProtocolError("Invalid command result.")
            if "led_state" in result and (type(result["led_state"]) is not str or result["led_state"] not in LED_STATES):
                raise ProtocolError("Invalid command result.")
            if "error" in result and result["error"] not in ("unsupported_command", "invalid_arguments"):
                raise ProtocolError("Invalid command result.")
            if len(json.dumps(result, allow_nan=False)) > 512:
                raise ProtocolError("Command result is too large.")
    else:
        if kind == "hello":
            if message["status"] not in ("pair_required", "challenge", "authenticated"):
                raise ProtocolError("Invalid connection status.")
            if message["status"] == "challenge":
                if "challenge" not in message or "request_id" not in message:
                    raise ProtocolError("Incomplete authentication challenge.")
                _text(message["challenge"], "challenge", 128, pattern=re.compile(r"^[A-Za-z0-9_-]+$"))
                _id(message["request_id"], "pairing request identifier")
            elif "challenge" in message or "request_id" in message:
                raise ProtocolError("Unexpected authentication challenge fields.")
        elif kind == "pair_request":
            _id(message["request_id"], "pairing request identifier")
            if message["state"] != "pending":
                raise ProtocolError("Invalid pairing state.")
        elif kind == "pair_confirm":
            _id(message["request_id"], "pairing request identifier")
            _text(message["challenge"], "challenge", 128, pattern=re.compile(r"^[A-Za-z0-9_-]+$"))
            _text(message["device_credential"], "device credential", 128, pattern=re.compile(r"^[A-Za-z0-9_-]+$"))
        elif kind in ("heartbeat", "device_status"):
            if type(message["accepted"]) is not bool:
                raise ProtocolError("Invalid acknowledgement.")
        elif kind == "command":
            _id(message["command_id"], "command identifier")
            _text(message["name"], "command name", 64)
            if message["name"] not in COMMANDS:
                raise ProtocolError("Command is not allowed.")
            _arguments(message["name"], message["arguments"])
        elif kind == "error":
            _text(message["code"], "error code", 40, pattern=re.compile(r"^[a-z_]+$"))
            _text(message["message"], "error message", 160)
    return message


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON fields are not allowed.")
        result[key] = value
    return result


def decode_line(raw, *, direction="device"):
    """Decode one bounded newline-terminated UTF-8 JSON message."""
    if type(raw) is not bytes or len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n") or b"\n" in raw[:-1]:
        raise ProtocolError("Invalid or oversized message frame.")
    try:
        value = json.loads(raw[:-1].decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ProtocolError("Invalid JSON number.")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProtocolError("Invalid JSON message.") from exc
    return validate_message(value, direction=direction)


def encode_line(message):
    """Validate and encode one Core-to-device frame."""
    validate_message(message, direction="core")
    try:
        raw = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii") + b"\n"
    except (TypeError, ValueError) as exc:
        raise ProtocolError("Message cannot be encoded.") from exc
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Message exceeds the size limit.")
    return raw


def device_line(message):
    """Validate and encode one device-to-Core frame (used by simulator and tests)."""
    validate_message(message, direction="device")
    try:
        raw = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii") + b"\n"
    except (TypeError, ValueError) as exc:
        raise ProtocolError("Message cannot be encoded.") from exc
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Message exceeds the size limit.")
    return raw
