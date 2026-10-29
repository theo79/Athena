"""Explicit, lossless legacy JSON import. Importing this module performs no I/O."""
import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

import experience as legacy

from memory_service import (MemoryService, MemoryValidationError, _experience_record,
                            _json, _text, _integer, _timestamp)
from sqlite_store import SQLiteStore, StoreError

EPOCH = '1970-01-01T00:00:00+00:00'


@dataclass
class MigrationResult:
    total: int = 0
    valid: int = 0
    skipped: int = 0
    conflicting: int = 0
    invalid: int = 0
    unsupported: int = 0
    imported: int = 0
    committed: bool = False
    dry_run: bool = True
    issues: list[str] = field(default_factory=list)

    @property
    def blocked(self):
        return bool(self.conflicting or self.invalid or self.unsupported)


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key.')
        result[key] = value
    return result


class UnsupportedRecord(ValueError):
    pass


def _prepare(kind, record, profile, principal, occurrences):
    if type(record) is not dict:
        raise MemoryValidationError('Expected a record object.')
    common = dict(profile_id=profile, access_scope='private', owner_id=principal)
    if kind == 'memory':
        if set(record) - {'text', 'saved_at', 'id', 'revision'}:
            raise UnsupportedRecord('Unsupported memory fields.')
        _text(record.get('text'), 2000)
        timestamp = _text(record.get('saved_at'), 80)
        try:
            datetime.fromisoformat(timestamp)
        except (ValueError, OverflowError):
            raise MemoryValidationError('Invalid saved_at timestamp.') from None
        revision = _integer(record.get('revision', 1), 1, 2147483647)
        identity = record.get('id')
        if 'id' not in record:
            digest = sha256(_canonical(record).encode('utf-8')).hexdigest()
            ordinal = occurrences.get(digest, 0)
            occurrences[digest] = ordinal + 1
            identity = 'legacy-' + sha256(f'{digest}:{ordinal}'.encode()).hexdigest()
        _text(identity, 80)
        return 'memories', dict(common, id=identity, kind='personal', content=record['text'],
            basis='legacy_unknown', epistemic_status='unknown', status='candidate',
            provenance_type='legacy_json', created_at=timestamp, updated_at=timestamp,
            revision=revision)
    allowed = set(legacy.Experience('validation').record)
    if set(record) - allowed:
        raise UnsupportedRecord('Unsupported experience fields.')
    validated = _experience_record(record)
    # M2.2 redacts, bounds, fills defaults and derives feedback metadata. Reject
    # any supplied field it would change, including nested tool metadata.
    if any(_canonical(value) != _canonical(validated.get(key)) for key, value in record.items()):
        raise UnsupportedRecord('M2.2 would alter supplied experience fields.')
    payload = _json(validated)
    occurred = None
    if isinstance(validated.get('timestamp'), str):
        try:
            occurred = _timestamp(validated['timestamp'])
        except MemoryValidationError:
            pass
    return 'episodes', dict(common, id=validated['id'], recorded_at=EPOCH,
        occurred_at=occurred, summary=validated['task'], basis='observation',
        provenance_type='task_execution', execution_status=validated['status'], payload_json=payload)


def migrate_json(database, *, memory_files=(), experience_files=(), profile_id,
                 principal_id, dry_run=True):
    """Validate all inputs, then atomically import or return a blocked summary.

    Paths must be explicit; no fallback/recovery loaders or runtime backend switch.
    Store/schema/I/O errors propagate. Invalid JSON/records appear in the summary.
    Existing destinations are opened read-only for preflight, even for execution.
    """
    if type(dry_run) is not bool:
        raise ValueError('dry_run must be a boolean.')
    service = MemoryService(SQLiteStore(Path(database)), profile_id, principal_id)
    result = MigrationResult(dry_run=dry_run)
    rows = []
    sources = [('memory', Path(p)) for p in memory_files]
    sources += [('experience', Path(p)) for p in experience_files]
    if not sources:
        raise ValueError('At least one source file is required.')
    destination = Path(database).resolve()
    reserved = {destination, *(Path(str(destination) + suffix) for suffix in ('-journal', '-wal', '-shm'))}
    for _, source in sources:
        if source.resolve() in reserved or (destination.exists() and source.samefile(destination)):
            raise ValueError('Source and destination must be different files.')
    for source_number, (kind, source) in enumerate(sources, 1):
        try:
            records = json.loads(source.read_bytes().decode('utf-8'), object_pairs_hook=_object,
                                 parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            if type(records) is not list:
                raise ValueError('Expected a JSON list.')
        except (ValueError, UnicodeError):
            result.invalid += 1
            result.issues.append(f'Source {source_number}: invalid JSON list.')
            continue
        occurrences = {}
        for index, record in enumerate(records, 1):
            result.total += 1
            try:
                rows.append(_prepare(kind, record, profile_id, principal_id, occurrences))
            except UnsupportedRecord as exc:
                result.unsupported += 1
                result.issues.append(f'Source {source_number}, record {index}: {exc}')
            except (MemoryValidationError, ValueError, TypeError, OverflowError):
                result.invalid += 1
                result.issues.append(f'Source {source_number}, record {index}: invalid record.')
    # Classify duplicate source IDs even when no database exists.
    unique, seen = [], {}
    for table, row in rows:
        key = table, row['id']
        if key in seen:
            if seen[key] == row:
                result.skipped += 1
            else:
                result.conflicting += 1
        else:
            seen[key] = row
            unique.append((table, row))
    store = service._store
    if destination.exists():
        store.open(read_only=True)
        try:
            valid, skipped, conflicts = service._import_legacy_rows(unique, dry_run=True)
        finally:
            store.close()
        result.valid = valid
        result.skipped += skipped
        result.conflicting += conflicts
    else:
        result.valid = len(unique)
    if dry_run or result.blocked:
        return result
    # Schema initialization can leave an empty schema after a later failed import;
    # all identity and record writes occur in the single transaction below.
    store.initialize()
    store.open(read_only=False)
    try:
        valid, skipped, conflicts = service._import_legacy_rows(unique)
        result.valid = valid
        # Recompute database classifications under the writer lock.
        result.skipped = len(rows) - len(unique) + skipped
        result.conflicting = conflicts
        if not conflicts:
            result.imported = valid
            result.committed = True
    finally:
        store.close()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory', action='append', default=[], help='Memory JSON list (repeatable)')
    parser.add_argument('--experience', action='append', default=[], help='Experience JSON list (repeatable)')
    parser.add_argument('--database', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--principal', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = migrate_json(args.database, memory_files=args.memory, experience_files=args.experience,
                              profile_id=args.profile, principal_id=args.principal, dry_run=args.dry_run)
    except (OSError, ValueError, StoreError):
        print('Migration failed: check source paths, database schema, access and integrity.')
        return 2
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 1 if result.blocked else 0


if __name__ == '__main__':
    raise SystemExit(main())
