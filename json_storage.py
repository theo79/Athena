"""Local JSON-list storage for ONE writer/process at a time.

Atomic replacement prevents partial files, not lost concurrent updates. Callers
must not run concurrent read/modify/write operations. Backups and quarantines
contain the same private data as the primary file and have no automatic expiry.
"""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4


def _decode(raw):
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, list):
        raise ValueError("Expected a JSON list")
    return value


def _atomic_bytes(path, raw):
    """Flush a unique sibling file before atomically replacing the destination."""
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=path.name + ".",
                                suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_list(path):
    """Recover invalid/missing primary from .bak, or initialize an empty list.

Before replacing corrupt bytes, preserve them in a unique .corrupt-* file.
If preservation fails, propagate the I/O error rather than discard history.
Invalid backups are left untouched. A valid primary is never rewritten here.
"""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = None
    if raw is not None:
        try:
            return _decode(raw)
        except (ValueError, UnicodeDecodeError):
            quarantine = path.with_name(path.name + ".corrupt-" + uuid4().hex)
            _atomic_bytes(quarantine, raw)
    backup = path.with_name(path.name + ".bak")
    try:
        backup_raw = backup.read_bytes()
    except FileNotFoundError:
        backup_raw = None
    if backup_raw is not None:
        try:
            recovered = _decode(backup_raw)
        except (ValueError, UnicodeDecodeError):
            pass
        else:
            _atomic_bytes(path, backup_raw)
            return recovered
    _atomic_bytes(path, b"[]")
    return []


def write_list(path, records):
    """Keep the last valid primary as .bak, then atomically commit new data."""
    if not isinstance(records, list):
        raise ValueError("Expected a JSON list")
    path = Path(path)
    raw = json.dumps(records, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if path.exists():
        # Recover/preserve corruption before changing the primary or its backup.
        load_list(path)
        backup = path.with_name(path.name + ".bak")
        if backup.exists():
            backup_raw = backup.read_bytes()
            try:
                _decode(backup_raw)
            except (ValueError, UnicodeDecodeError):
                _atomic_bytes(backup.with_name(backup.name + ".corrupt-" + uuid4().hex), backup_raw)
        _atomic_bytes(backup, path.read_bytes())
    _atomic_bytes(path, raw)
