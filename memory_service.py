"""Optional path/profile-bound persistence. No default backend or model SQL API.

The trusted host supplies authenticated profile/principal IDs and an approval
callback; these must never be taken from model arguments. A shared scope is an
explicit same-profile grant, not cross-profile or anonymous access.
"""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
import math
import re
import sqlite3
from uuid import uuid4

import experience as legacy
from sqlite_store import SQLiteStore, StoreError, StoreStateError
from tool_modules.results import ToolResult


class MemoryAccessDenied(PermissionError):
    pass


class MemoryValidationError(ValueError):
    pass


class RevisionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class MemoryDraft:
    content: str
    kind: str = 'personal'
    basis: str = 'observation'
    provenance_type: str = 'user_statement'
    provenance_ref: str | None = None
    epistemic_status: str = 'unknown'
    confidence_estimate: float | None = None
    scope: str = 'private'
    status: str = 'active'
    subject_key: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True)
class MemoryAction:
    operation: str
    profile_id: str
    principal_id: str
    record_id: str
    expected_revision: int | None
    draft: MemoryDraft


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    profile_id: str
    owner_id: str
    draft: MemoryDraft
    revision: int
    created_at: str
    updated_at: str
    approved_by: str | None
    approved_at: str | None


def _text(value, maximum, *, optional=False):
    if optional and value is None:
        return None
    if type(value) is not str or not value.strip() or len(value) > maximum or '\x00' in value:
        raise MemoryValidationError('Invalid or oversized text field.')
    return value


def _choice(value, choices):
    if type(value) is not str or value not in choices:
        raise MemoryValidationError('Unsupported record classification.')
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise MemoryValidationError('Invalid integer field or limit.')
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value):
    _text(value, 80)
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise MemoryValidationError('An aware ISO timestamp is required.') from None


def _json(value, maximum=20000):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        raise MemoryValidationError('Invalid record payload.') from None
    if len(encoded) > maximum:
        raise MemoryValidationError('Record exceeds the storage limit.')
    return encoded


class MemoryService:
    """One explicitly opened store, host identity and immutable access policy.

    All memory saves/updates require exact-True host approval, including candidate
    records. Automatic episodes are separate private, bounded task telemetry.
    The host must escape/redact approval previews in its own UI. This is an
    application boundary, not a sandbox against Python code or raw database access.
    """

    def __init__(self, store: SQLiteStore, profile_id: str, principal_id: str, *,
                 authorize=None, scopes=('private', 'profile'), max_results=5,
                 candidate_limit=200, context_chars=8000):
        if not isinstance(store, SQLiteStore):
            raise MemoryValidationError('A SQLiteStore dependency is required.')
        self._profile_id = _text(profile_id, 80)
        self._principal_id = _text(principal_id, 80)
        if authorize is not None and not callable(authorize):
            raise MemoryValidationError('Authorization must be a host callback.')
        if not isinstance(scopes, (tuple, list, frozenset, set)) or not scopes:
            raise MemoryValidationError('Explicit access scopes are required.')
        self._scopes = frozenset(_choice(s, {'private', 'profile', 'shared'}) for s in scopes)
        self._max_results = _integer(max_results, 1, 20)
        self._candidate_limit = _integer(candidate_limit, 1, 1000)
        self._context_chars = _integer(context_chars, 4000, 20000)
        self._store, self._authorize_callback = store, authorize
        self._opened, self._writable = False, False

    @property
    def profile_id(self):
        return self._profile_id

    @property
    def principal_id(self):
        return self._principal_id

    def initialize(self):
        """Explicitly initialize the schema/profile and leave the connection closed."""
        if self._opened:
            raise StoreStateError('Close the service before initializing it.')
        self._store.initialize()
        self._store.open(read_only=False)
        try:
            with self._store.transaction() as connection:
                connection.execute('INSERT INTO identities(id, created_at) VALUES (?, ?) '
                                   'ON CONFLICT(id) DO NOTHING', (self.profile_id, _now()))
        finally:
            self._store.close()

    def open(self, *, read_only=True):
        self._store.open(read_only=read_only)
        self._opened, self._writable = True, not read_only
        try:
            self._require_open()
        except BaseException:
            self.close()
            raise
        return self

    def close(self):
        self._store.close()
        self._opened, self._writable = False, False

    def _rows(self, sql, parameters=()):
        try:
            cursor = self._store.connection.execute(sql, parameters)
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]
        except sqlite3.Error:
            raise StoreError('Memory storage read failed.') from None

    def _require_open(self, *, write=False):
        if not self._opened or (write and not self._writable):
            raise StoreStateError('The service must be explicitly opened in the required mode.')
        if not self._rows('SELECT id FROM identities WHERE id=?', (self.profile_id,)):
            raise MemoryAccessDenied('Profile is unavailable.')

    def _visibility(self, *, include_expired=False):
        scopes = sorted(self._scopes)
        placeholders = ','.join('?' for _ in scopes)
        sql = (f'profile_id=? AND access_scope IN ({placeholders}) '
               "AND (access_scope!='private' OR owner_id=?)")
        parameters = (self.profile_id, *scopes, self.principal_id)
        if not include_expired:
            sql += ' AND (expires_at IS NULL OR expires_at>?)'
            parameters += (_now(),)
        return sql, parameters

    def _draft(self, draft):
        if type(draft) is not MemoryDraft:
            raise MemoryValidationError('Use a MemoryDraft without authorization fields.')
        _text(draft.content, 2000)
        _choice(draft.kind, {'personal', 'knowledge', 'preference'})
        _choice(draft.basis, {'observation', 'inference', 'legacy_unknown'})
        _choice(draft.status, {'candidate', 'active', 'disputed', 'superseded', 'rejected'})
        _choice(draft.epistemic_status, {'unknown', 'user_asserted', 'disputed'})
        _choice(draft.scope, self._scopes)
        _text(draft.provenance_type, 80)
        _text(draft.provenance_ref, 500, optional=True)
        _text(draft.subject_key, 200, optional=True)
        if draft.basis in {'inference', 'legacy_unknown'} and draft.epistemic_status != 'unknown':
            raise MemoryValidationError('An inference or unknown legacy basis cannot assert verification.')
        if draft.epistemic_status == 'user_asserted' and draft.provenance_type != 'user_statement':
            raise MemoryValidationError('User assertions require user-statement provenance.')
        confidence = draft.confidence_estimate
        if confidence is not None and (type(confidence) not in (int, float)
                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise MemoryValidationError('Confidence must be an uncalibrated estimate in [0, 1].')
        if draft.expires_at is not None:
            draft = replace(draft, expires_at=_timestamp(draft.expires_at))
        _json(asdict(draft), 4000)
        return draft

    def _approve(self, action):
        try:
            approved = (self._authorize_callback is not None
                        and self._authorize_callback(action) is True)
        except Exception:
            approved = False
        if not approved:
            raise MemoryAccessDenied('Host approval is required; nothing was saved.')

    def save_memory(self, draft: MemoryDraft) -> str:
        draft = self._draft(draft)
        self._require_open(write=True)
        identity = str(uuid4())
        self._approve(MemoryAction('save', self.profile_id, self.principal_id, identity, None, draft))
        timestamp = _now()
        values = self._values(draft)
        with self._store.transaction() as connection:
            connection.execute(
                'INSERT INTO memories (id, profile_id, owner_id, kind, content, basis, provenance_type, '
                'provenance_ref, epistemic_status, confidence_estimate, access_scope, status, subject_key, '
                'expires_at, created_at, updated_at, approved_by, approved_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity, self.profile_id, self.principal_id, *values, timestamp, timestamp,
                 self.principal_id, timestamp))
        return identity

    @staticmethod
    def _values(draft):
        return (draft.kind, draft.content, draft.basis, draft.provenance_type, draft.provenance_ref,
                draft.epistemic_status, draft.confidence_estimate, draft.scope, draft.status,
                draft.subject_key, draft.expires_at)

    def _memory_row(self, identity, *, include_expired=False):
        _text(identity, 80)
        self._require_open()
        clause, parameters = self._visibility(include_expired=include_expired)
        rows = self._rows('SELECT * FROM memories WHERE id=? AND ' + clause
                          + " AND length(content)<=2000 AND (status='active' OR owner_id=?) LIMIT 1",
                          (identity, *parameters, self.principal_id))
        if not rows:
            raise MemoryAccessDenied('Record is unavailable or not permitted.')
        return rows[0]

    def _record(self, row):
        for key in ('id', 'profile_id', 'owner_id'):
            _text(row[key], 80)
        _integer(row['revision'], 1, 2**31 - 1)
        for key in ('created_at', 'updated_at'):
            if key == 'created_at' or (row['basis'] == 'legacy_unknown' and row['provenance_type'] == 'legacy_json'):
                # M2.3 retains local saved_at verbatim; created_at must remain
                # readable even after an explicit provenance/content correction.
                _text(row[key], 80)
                try:
                    datetime.fromisoformat(row[key])
                except (ValueError, OverflowError):
                    raise MemoryValidationError('Invalid legacy memory timestamp.') from None
            else:
                _timestamp(row[key])
        _text(row['approved_by'], 80, optional=True)
        if row['approved_at'] is not None:
            _timestamp(row['approved_at'])
        draft = self._draft(MemoryDraft(
            content=row['content'], kind=row['kind'], basis=row['basis'],
            provenance_type=row['provenance_type'], provenance_ref=row['provenance_ref'],
            epistemic_status=row['epistemic_status'], confidence_estimate=row['confidence_estimate'],
            scope=row['access_scope'], status=row['status'], subject_key=row['subject_key'],
            expires_at=row['expires_at']))
        return MemoryRecord(row['id'], row['profile_id'], row['owner_id'], draft,
                            row['revision'], row['created_at'], row['updated_at'],
                            row['approved_by'], row['approved_at'])

    def read_memory(self, identity: str) -> MemoryRecord:
        return self._record(self._memory_row(identity))

    def inspect_memory(self, identity: str) -> MemoryRecord:
        """Owner-only review, including expired and inactive records; no writes."""
        row = self._memory_row(identity, include_expired=True)
        if row['owner_id'] != self.principal_id:
            raise MemoryAccessDenied('Only the record owner can inspect its lifecycle.')
        return self._record(row)

    @staticmethod
    def _transition(previous, target):
        allowed = {
            'candidate': {'candidate', 'active', 'rejected'},
            'active': {'active', 'disputed', 'superseded', 'rejected'},
            'disputed': {'disputed', 'active', 'superseded', 'rejected'},
            'superseded': {'superseded'},
            'rejected': {'rejected'},
        }
        if target not in allowed.get(previous, set()):
            raise MemoryValidationError('Invalid memory lifecycle transition.')

    def promote_memory(self, identity: str, *, expected_revision: int) -> int:
        """Explicit approved candidate -> active; preserve all other draft fields."""
        record = self.read_memory(identity)
        if record.draft.status != 'candidate':
            raise MemoryValidationError('Only candidate memories can be promoted.')
        return self.update_memory(identity, replace(record.draft, status='active'),
                                  expected_revision=expected_revision)

    def retire_memory(self, identity: str, *, expected_revision: int) -> int:
        """Explicit approved active -> superseded; retain the record for review."""
        record = self.read_memory(identity)
        if record.draft.status != 'active':
            raise MemoryValidationError('Only active memories can be retired.')
        return self.update_memory(identity, replace(record.draft, status='superseded'),
                                  expected_revision=expected_revision)

    def update_memory(self, identity: str, draft: MemoryDraft, *, expected_revision: int) -> int:
        draft = self._draft(draft)
        _integer(expected_revision, 1, 2**31 - 1)
        self._require_open(write=True)
        previous = self._memory_row(identity)
        if previous['owner_id'] != self.principal_id:
            raise MemoryAccessDenied('Only the record owner can update it.')
        if previous['revision'] != expected_revision:
            raise RevisionConflict('Memory revision changed; reload before updating.')
        if previous['kind'] != draft.kind:
            raise MemoryValidationError('Updates cannot change the memory classification.')
        self._transition(previous['status'], draft.status)
        self._approve(MemoryAction('update', self.profile_id, self.principal_id,
                                  identity, expected_revision, draft))
        with self._store.transaction() as connection:
            current = self._memory_row(identity)
            if current['owner_id'] != self.principal_id:
                raise MemoryAccessDenied('Only the record owner can update it.')
            if current['revision'] != expected_revision:
                raise RevisionConflict('Memory revision changed; reload before updating.')
            self._transition(current['status'], draft.status)
            timestamp = _now()
            connection.execute(
                'UPDATE memories SET kind=?, content=?, basis=?, provenance_type=?, provenance_ref=?, '
                'epistemic_status=?, confidence_estimate=?, access_scope=?, status=?, subject_key=?, '
                'expires_at=?, revision=revision+1, updated_at=?, approved_by=?, approved_at=? '
                'WHERE id=? AND profile_id=? AND owner_id=? AND revision=?',
                (*self._values(draft), timestamp, self.principal_id, timestamp,
                 identity, self.profile_id, self.principal_id, expected_revision))
        return expected_revision + 1

    def find_memories(self, query: str, *, limit=None, kind=None) -> list[MemoryRecord]:
        _text(query, 500)
        limit = self._max_results if limit is None else _integer(limit, 1, self._max_results)
        if kind is not None:
            _choice(kind, {'personal', 'knowledge', 'preference'})
        self._require_open()
        clause, parameters = self._visibility()
        if kind is not None:
            clause += ' AND kind=?'
            parameters += (kind,)
        rows = self._rows('SELECT * FROM memories WHERE ' + clause
                          + " AND status='active' AND length(content)<=2000 "
                          'ORDER BY updated_at DESC, id ASC LIMIT ?',
                          (*parameters, self._candidate_limit))
        words = set(re.findall(r'\w+', query.casefold()))
        ranked = []
        for row in rows:
            record = self._record(row)
            text = record.draft.content.casefold()
            past = set(re.findall(r'\w+', text))
            score = 1.0 if query.casefold() in text else len(words & past) / max(1, len(words | past))
            if score:
                ranked.append((score, record))
        ranked.sort(key=lambda item: -item[0])  # SQL order is the deterministic tie break.
        results, used = [], 2
        for _, record in ranked:
            size = len(_json(asdict(record), 8000)) + (2 if results else 0)
            if used + size <= self._context_chars:
                results.append(record)
                used += size
            if len(results) == limit:
                break
        return results

    def _import_legacy_rows(self, rows, *, dry_run=False):
        """Trusted migration helper; rows are validated by memory_migration.

        Compare complete imported state, including ownership, inside the write
        transaction. Never overwrite an existing identifier or grant approval.
        """
        def inspect(connection):
            pending, skipped, conflicts = [], 0, 0
            seen = {}
            for table, row in rows:
                key = (table, row['id'])
                prior = seen.get(key)
                if prior is None:
                    found = connection.execute(
                        f'SELECT * FROM {table} WHERE id=?', (row['id'],))
                    names = [column[0] for column in found.description]
                    value = found.fetchone()
                    prior = dict(zip(names, value)) if value else None
                if prior is not None:
                    # SQLite fills optional columns with NULL and revision with 1.
                    equal = all(prior.get(k) == v for k, v in row.items())
                    equal = equal and all(v is None or k in row for k, v in prior.items())
                    if equal:
                        skipped += 1
                    else:
                        conflicts += 1
                else:
                    pending.append((table, row))
                seen[key] = prior if prior is not None else row
            return pending, skipped, conflicts

        if dry_run:
            # One stable read snapshot; this does not require a bound profile yet.
            connection = self._store.connection
            connection.execute('BEGIN')
            try:
                pending, skipped, conflicts = inspect(connection)
            finally:
                connection.execute('ROLLBACK')
            return len(pending), skipped, conflicts
        with self._store.transaction() as connection:
            pending, skipped, conflicts = inspect(connection)
            if conflicts:
                return len(pending), skipped, conflicts
            connection.execute('INSERT INTO identities(id, created_at) VALUES (?, ?) '
                               'ON CONFLICT(id) DO NOTHING', (self.profile_id, _now()))
            for table, row in pending:
                columns = ','.join(row)
                placeholders = ','.join('?' for _ in row)
                connection.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})',
                                   tuple(row.values()))
        return len(pending), skipped, conflicts

    def record_experience(self, record: dict) -> str:
        """Retain only bounded legacy task telemetry, always private to this principal."""
        self._require_open(write=True)
        if 'private' not in self._scopes:
            raise MemoryAccessDenied('Private telemetry scope is required.')
        record = _experience_record(record)
        encoded = _json(record)
        occurred = None
        if isinstance(record.get('timestamp'), str):
            try:
                occurred = _timestamp(record['timestamp'])
            except MemoryValidationError:
                pass  # Preserve the original timestamp in the compatibility payload.
        with self._store.transaction() as connection:
            connection.execute(
                'INSERT INTO episodes (id, profile_id, access_scope, owner_id, recorded_at, occurred_at, '
                'summary, basis, provenance_type, execution_status, payload_json) '
                "VALUES (?,?,'private',?,?,?,?,'observation','task_execution',?,?)",
                (record['id'], self.profile_id, self.principal_id, _now(), occurred,
                 record['task'], record['status'], encoded))
        return record['id']

    def _experience_row(self, identity):
        _text(identity, 80)
        self._require_open()
        clause, parameters = self._visibility()
        rows = self._rows('SELECT * FROM episodes WHERE id=? AND ' + clause
                          + ' AND length(payload_json)<=20000 LIMIT 1',
                          (identity, *parameters))
        if not rows:
            raise MemoryAccessDenied('Record is unavailable or not permitted.')
        return rows[0]

    def read_experience(self, identity: str) -> dict:
        return self._decode_episode(self._experience_row(identity)['payload_json'])

    @staticmethod
    def _decode_episode(encoded):
        if type(encoded) is not str or len(encoded) > 20000:
            raise MemoryValidationError('Invalid stored experience payload.')
        try:
            return _experience_record(json.loads(encoded))
        except (ValueError, TypeError):
            raise MemoryValidationError('Invalid stored experience payload.') from None

    def find_relevant_experiences(self, task: str, limit=3) -> list[dict]:
        _text(task, 1000)
        _integer(limit, 1, legacy.MAX_RETRIEVED_EXPERIENCES)
        self._require_open()
        clause, parameters = self._visibility()
        rows = self._rows('SELECT payload_json FROM (SELECT rowid AS sequence, payload_json '
                          'FROM episodes WHERE ' + clause
                          + ' AND length(payload_json)<=20000 '
                          'ORDER BY rowid DESC LIMIT ?) ORDER BY sequence ASC',
                          (*parameters, self._candidate_limit))
        records = [self._decode_episode(row['payload_json']) for row in rows]
        return legacy.rank_experiences(task, records, limit)

    def set_experience_feedback(self, identity: str, feedback: str) -> bool:
        if type(feedback) is not str or feedback not in legacy.FEEDBACK_REWARDS:
            return False
        self._require_open(write=True)
        with self._store.transaction() as connection:
            try:
                row = self._experience_row(identity)
            except (MemoryAccessDenied, MemoryValidationError):
                return False
            if row['owner_id'] != self.principal_id:
                return False
            self._decode_episode(row['payload_json'])  # Validate without rewriting task content.
            record = json.loads(row['payload_json'])
            record.update(feedback=feedback, reward=legacy.FEEDBACK_REWARDS[feedback],
                          quality=legacy.FEEDBACK_QUALITY[feedback])
            connection.execute('UPDATE episodes SET payload_json=? WHERE id=? AND profile_id=? AND owner_id=?',
                               (_json(record), identity, self.profile_id, self.principal_id))
            connection.execute(
                'INSERT INTO feedback (id, profile_id, episode_id, actor_id, rating, recorded_at, access_scope, owner_id) '
                "VALUES (?,?,?,?,?,?,'private',?) ON CONFLICT(profile_id, episode_id, actor_id) "
                'DO UPDATE SET rating=excluded.rating, recorded_at=excluded.recorded_at',
                (str(uuid4()), self.profile_id, identity, self.principal_id,
                 legacy.FEEDBACK_REWARDS[feedback], _now(), self.principal_id))
        return True


def _experience_record(record):
    """Validate and sanitize the existing record vocabulary, without raw tool outputs."""
    if type(record) is not dict:
        raise MemoryValidationError('An experience dictionary is required.')
    allowed = set(legacy.Experience('validation').record)
    if set(record) - allowed:
        raise MemoryValidationError('Unsupported experience fields.')
    _json(record, 30000)
    _text(record.get('id'), 80)
    _text(record.get('task'), 1000)
    _choice(record.get('status'), legacy.OUTCOMES)
    result = legacy.Experience(record['task']).record
    result.update(id=record['id'], task=legacy.safe_text(record['task'], 1000), status=record['status'])
    result['timestamp'] = record.get('timestamp')  # Missing legacy dates sort oldest.
    result['error_type'] = record.get('error_type')
    result['error_message'] = record.get('error_message')
    for key, maximum in (('timestamp', 80), ('provider', 200), ('model', 200),
                         ('error_type', 80), ('lesson_status', 80), ('quality', 80)):
        if key in record:
            value = record[key]
            if value is not None:
                _text(value, maximum)
            result[key] = (legacy.safe_text(value, maximum)
                           if value is not None and key != 'timestamp' else value)
    for key, maximum in (('final_answer', 5000), ('error_message', 500), ('lesson', 500)):
        if key in record:
            value = record[key]
            if value is not None and (type(value) is not str or len(value) > maximum):
                raise MemoryValidationError('Invalid or oversized experience text.')
            result[key] = legacy.safe_text(value, maximum) if value is not None else None
    for key in ('steps', 'parse_failures'):
        if key in record:
            result[key] = _integer(record[key], 0, 5)
        else:
            result.pop(key, None)
    result['budget_exhausted'] = record.get('budget_exhausted', False)
    if type(result['budget_exhausted']) is not bool:
        raise MemoryValidationError('Invalid experience budget flag.')
    feedback = record.get('feedback')
    if feedback is not None:
        _choice(feedback, legacy.FEEDBACK_REWARDS)
    result['feedback'] = feedback
    result['reward'] = legacy.FEEDBACK_REWARDS.get(feedback, 0)
    if feedback is not None:
        result['quality'] = legacy.FEEDBACK_QUALITY[feedback]
    events = record.get('tools_used', [])
    if type(events) is not list or len(events) > 5:
        raise MemoryValidationError('Invalid or oversized tool history.')
    result['tools_used'] = []
    for event in events:
        if type(event) is not dict or type(event.get('args', {})) is not dict:
            raise MemoryValidationError('Invalid tool metadata.')
        name = _text(event.get('name'), 64)
        if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', name):
            raise MemoryValidationError('Invalid tool name.')
        success = event.get('success')
        if type(success) is not bool:
            raise MemoryValidationError('Invalid tool outcome.')
        error = event.get('error_type')
        if error is not None:
            _text(error, 80)
        permission = _text(event.get('permission', 'UNKNOWN'), 80)
        telemetry = legacy.Experience('metadata')
        telemetry.tool(name, event.get('args', {}), permission,
                       ToolResult('', success, error), _integer(event.get('step', 1), 1, 5))
        summary = event.get('result_summary', 'Tool completed; output omitted.')
        _text(summary, 500)
        telemetry.record['tools_used'][0]['result_summary'] = legacy.safe_text(summary, 500)
        result['tools_used'].extend(telemetry.record['tools_used'])
    return result


class SQLiteExperienceAdapter:
    """Optional AgentRuntime experience_service; no default/global installation."""
    observe = staticmethod(legacy.observe)
    safe_text = staticmethod(legacy.safe_text)
    build_experience_context = staticmethod(legacy.build_experience_context)

    def __init__(self, service: MemoryService):
        if not isinstance(service, MemoryService):
            raise MemoryValidationError('A MemoryService dependency is required.')
        self._service = service

    def Experience(self, task):
        service = self._service

        class Record(legacy.Experience):
            def persist(self):
                return service.record_experience(self.record)

        return Record(task)

    def find_relevant_experiences(self, task, limit=3):
        try:
            self._service._require_open()
            if type(limit) is not int or limit <= 0 or not legacy.task_tokens(task):
                return []
            return self._service.find_relevant_experiences(
                legacy.safe_text(task, 1000), min(limit, legacy.MAX_RETRIEVED_EXPERIENCES))
        except StoreStateError:
            raise  # A closed/read-only lifecycle mistake is the host's programming error.
        except (StoreError, MemoryValidationError):
            return []  # Optional historical context must not prevent a task from running.

    def set_experience_feedback(self, identity, feedback):
        try:
            return self._service.set_experience_feedback(identity, feedback)
        except (StoreError, MemoryAccessDenied, MemoryValidationError):
            return False
