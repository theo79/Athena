"""Explicit local SQLite foundation. Trusted host SQL only; no active backend.

Constructors do no I/O. initialize() alone may create a database (the parent
directory must exist). open() defaults to read-only and never creates a database.
Use one application writer initially. This is not a model-facing SQL interface.
"""
from contextlib import contextmanager
import math
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 1
APPLICATION_ID = 0x4154484E


class StoreError(RuntimeError):
    """Fixed public diagnostics exclude SQL, paths and stored contents."""


class StoreStateError(StoreError):
    pass


class SchemaVersionError(StoreError):
    pass


class StoreIntegrityError(StoreError):
    pass


class StoreBusyError(StoreError):
    pass


# Scope labels are storage constraints, not authorization. The future service
# must filter by authenticated profile/principal and permitted scope on EVERY
# query (including evidence joins). Raw connections are trusted infrastructure.
SCOPE = """
    access_scope TEXT NOT NULL CHECK(access_scope IN ('private', 'profile', 'shared')),
    owner_id TEXT CHECK(access_scope != 'private' OR
          (owner_id IS NOT NULL AND length(trim(owner_id)) > 0))
"""
CONFIDENCE = """confidence_estimate REAL CHECK(confidence_estimate IS NULL OR
    (typeof(confidence_estimate) IN ('real', 'integer') AND
     confidence_estimate BETWEEN 0 AND 1))"""

TABLES = {
    'identities': """
        id TEXT PRIMARY KEY NOT NULL CHECK(length(trim(id)) > 0),
        created_at TEXT NOT NULL
    """,
    'personality_versions': """
        profile_id TEXT NOT NULL REFERENCES identities(id),
        version INTEGER NOT NULL CHECK(typeof(version) = 'integer' AND version > 0),
        display_name TEXT NOT NULL CHECK(length(trim(display_name)) > 0),
        role_description TEXT NOT NULL,
        configuration_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL CHECK(length(trim(created_by)) > 0),
        PRIMARY KEY(profile_id, version)
    """,
    'episodes': f"""
        id TEXT PRIMARY KEY NOT NULL,
        profile_id TEXT NOT NULL REFERENCES identities(id),
        {SCOPE},
        occurred_at TEXT,
        recorded_at TEXT NOT NULL,
        summary TEXT NOT NULL,
        basis TEXT NOT NULL CHECK(basis IN ('observation', 'inference', 'legacy_unknown')),
        provenance_type TEXT NOT NULL CHECK(length(trim(provenance_type)) > 0),
        provenance_ref TEXT,
        execution_status TEXT NOT NULL CHECK(execution_status IN
            ('success', 'completed', 'model_failure', 'max_steps', 'parse_failure',
             'tool_failure', 'internal_error', 'legacy_unknown')),
        payload_json TEXT NOT NULL DEFAULT '{{}}',
        {CONFIDENCE},
        expires_at TEXT,
        UNIQUE(profile_id, id)
    """,
    'memories': f"""
        id TEXT PRIMARY KEY NOT NULL,
        profile_id TEXT NOT NULL REFERENCES identities(id),
        {SCOPE},
        kind TEXT NOT NULL CHECK(kind IN ('personal', 'knowledge', 'preference')),
        subject_key TEXT,
        content TEXT NOT NULL CHECK(length(trim(content)) > 0),
        basis TEXT NOT NULL CHECK(basis IN ('observation', 'inference', 'legacy_unknown')),
        epistemic_status TEXT NOT NULL CHECK(epistemic_status IN
            ('unknown', 'user_asserted', 'corroborated', 'disputed')),
        status TEXT NOT NULL CHECK(status IN
            ('candidate', 'active', 'disputed', 'superseded', 'rejected')),
        provenance_type TEXT NOT NULL CHECK(length(trim(provenance_type)) > 0),
        provenance_ref TEXT,
        {CONFIDENCE},
        approved_by TEXT,
        approved_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(typeof(revision) = 'integer' AND revision > 0),
        supersedes_id TEXT,
        UNIQUE(profile_id, id),
        FOREIGN KEY(profile_id, supersedes_id) REFERENCES memories(profile_id, id),
        CHECK(supersedes_id IS NULL OR supersedes_id != id),
        CHECK((approved_by IS NULL) = (approved_at IS NULL)),
        CHECK(approved_by IS NULL OR length(trim(approved_by)) > 0),
        CHECK(kind NOT IN ('personal', 'preference') OR status != 'active'
              OR approved_by IS NOT NULL)
    """,
    'memory_evidence': f"""
        id TEXT PRIMARY KEY NOT NULL,
        profile_id TEXT NOT NULL REFERENCES identities(id),
        {SCOPE},
        memory_id TEXT NOT NULL,
        episode_id TEXT,
        relationship TEXT NOT NULL CHECK(relationship IN ('supports', 'contradicts', 'corrects')),
        basis TEXT NOT NULL CHECK(basis IN ('observation', 'inference', 'legacy_unknown')),
        source_ref TEXT,
        recorded_at TEXT NOT NULL,
        FOREIGN KEY(profile_id, memory_id) REFERENCES memories(profile_id, id),
        FOREIGN KEY(profile_id, episode_id) REFERENCES episodes(profile_id, id),
        CHECK(episode_id IS NOT NULL OR source_ref IS NOT NULL)
    """,
    'feedback': f"""
        id TEXT PRIMARY KEY NOT NULL,
        profile_id TEXT NOT NULL REFERENCES identities(id),
        {SCOPE},
        episode_id TEXT NOT NULL,
        actor_id TEXT NOT NULL CHECK(length(trim(actor_id)) > 0),
        rating INTEGER NOT NULL CHECK(rating IN (-1, 0, 1)),
        preference_key TEXT,
        preference_value TEXT,
        recorded_at TEXT NOT NULL,
        UNIQUE(profile_id, episode_id, actor_id),
        FOREIGN KEY(profile_id, episode_id) REFERENCES episodes(profile_id, id)
    """,
    'goals': f"""
        id TEXT PRIMARY KEY NOT NULL,
        profile_id TEXT NOT NULL REFERENCES identities(id),
        {SCOPE},
        description TEXT NOT NULL CHECK(length(trim(description)) > 0),
        state TEXT NOT NULL CHECK(state IN
            ('proposed', 'active', 'paused', 'completed', 'failed', 'cancelled')),
        created_by TEXT NOT NULL,
        provenance_ref TEXT,
        priority INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        due_at TEXT,
        completion_criteria TEXT,
        completion_episode_id TEXT,
        parent_id TEXT,
        UNIQUE(profile_id, id),
        FOREIGN KEY(profile_id, parent_id) REFERENCES goals(profile_id, id),
        FOREIGN KEY(profile_id, completion_episode_id) REFERENCES episodes(profile_id, id),
        CHECK(parent_id IS NULL OR parent_id != id)
    """,
    'legacy_imports': """
        id TEXT PRIMARY KEY NOT NULL,
        profile_id TEXT NOT NULL REFERENCES identities(id),
        source_fingerprint TEXT NOT NULL CHECK(length(trim(source_fingerprint)) > 0),
        source_kind TEXT NOT NULL CHECK(source_kind IN ('memory', 'experience')),
        source_schema_version TEXT NOT NULL,
        imported_at TEXT NOT NULL,
        record_count INTEGER NOT NULL CHECK(typeof(record_count) = 'integer' AND record_count >= 0),
        UNIQUE(profile_id, source_kind, source_fingerprint)
    """,
}

SCHEMA = [f'CREATE TABLE {name} ({columns})' for name, columns in TABLES.items()] + [
    """CREATE TRIGGER identity_id_immutable BEFORE UPDATE OF id ON identities
       WHEN NEW.id != OLD.id BEGIN SELECT RAISE(ABORT, 'Identity ID is immutable'); END""",
    'CREATE INDEX memory_lookup ON memories(profile_id, access_scope, status, kind)',
    'CREATE INDEX episode_lookup ON episodes(profile_id, access_scope, recorded_at)',
    'CREATE INDEX evidence_lookup ON memory_evidence(profile_id, memory_id)',
    'CREATE INDEX goal_lookup ON goals(profile_id, state)',
]

REQUIRED_COLUMNS = {
    'identities': 'id created_at',
    'personality_versions': 'profile_id version display_name role_description configuration_json created_at created_by',
    'episodes': ('id profile_id access_scope owner_id occurred_at recorded_at summary basis '
                 'provenance_type provenance_ref execution_status payload_json confidence_estimate expires_at'),
    'memories': ('id profile_id access_scope owner_id kind subject_key content basis epistemic_status '
                 'status provenance_type provenance_ref confidence_estimate approved_by approved_at '
                 'created_at updated_at expires_at revision supersedes_id'),
    'memory_evidence': 'id profile_id access_scope owner_id memory_id episode_id relationship basis source_ref recorded_at',
    'feedback': 'id profile_id access_scope owner_id episode_id actor_id rating preference_key preference_value recorded_at',
    'goals': ('id profile_id access_scope owner_id description state created_by provenance_ref priority '
              'created_at updated_at due_at completion_criteria completion_episode_id parent_id'),
    'legacy_imports': 'id profile_id source_fingerprint source_kind source_schema_version imported_at record_count',
}


def _error(exc):
    code = getattr(exc, 'sqlite_errorcode', 0) & 255
    if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return StoreBusyError('SQLite storage is busy; retry the operation later.')
    if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        return StoreIntegrityError('SQLite storage is corrupt or is not a database.')
    if isinstance(exc, sqlite3.IntegrityError):
        return StoreIntegrityError('SQLite data violates a storage constraint.')
    return StoreError('SQLite storage operation failed.')


class SQLiteStore:
    """A host-supplied path and one controlled connection; no hidden singleton.

    connection is deliberately a trusted-host escape hatch for SQL, not a scoped
    memory API. Scope authorization and JSON payload validation belong to M2.2.
    Confidence fields are uncalibrated estimates, never verified probabilities.
    """

    def __init__(self, path, *, timeout=1.0):
        if not isinstance(path, (str, Path)) or not str(path).strip() or str(path) == ':memory:':
            raise ValueError('Supply a nonempty filesystem database path.')
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 <= timeout <= 60):
            raise ValueError('SQLite timeout must be between 0 and 60 seconds.')
        self.path = Path(path).absolute()
        self.timeout = float(timeout)
        self._connection = None
        self._read_only = True
        self._transaction = False

    @property
    def connection(self):
        if self._connection is None:
            raise StoreStateError('SQLite store is not open.')
        return self._connection

    def _connect(self, mode):
        connection = None
        try:
            connection = sqlite3.connect(
                self.path.as_uri() + '?mode=' + mode, uri=True,
                timeout=self.timeout, autocommit=True)
            connection.execute('PRAGMA foreign_keys=ON')
            if connection.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
                raise StoreError('SQLite foreign-key enforcement is unavailable.')
            return connection
        except (sqlite3.Error, StoreError) as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, sqlite3.Error):
                raise _error(exc) from None
            raise

    @staticmethod
    def _check_schema(connection):
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        app_id = connection.execute('PRAGMA application_id').fetchone()[0]
        if version != SCHEMA_VERSION or app_id != APPLICATION_ID:
            raise SchemaVersionError('Unsupported or uninitialized Athena SQLite schema.')
        actual = {row[0] for row in connection.execute('SELECT name FROM sqlite_schema')}
        if not {statement.split()[2] for statement in SCHEMA} <= actual:
            raise StoreIntegrityError('Athena SQLite schema is incomplete.')
        for table, columns in REQUIRED_COLUMNS.items():
            actual_columns = [row[1] for row in connection.execute(f'PRAGMA table_info({table})')]
            if actual_columns != columns.split():
                raise StoreIntegrityError('Athena SQLite schema is incomplete.')

    @staticmethod
    def _check_integrity(connection):
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise StoreIntegrityError('SQLite integrity validation failed.')
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise StoreIntegrityError('SQLite referential integrity validation failed.')

    def initialize(self):
        """Create schema v1 explicitly, or validate an existing v1 database.

        No automatic upgrade, corruption recovery, parent creation, or JSON import.
        An unrecognized nonempty database is never adopted or overwritten.
        """
        if self._connection is not None:
            raise StoreStateError('Close the store before initializing it.')
        connection = self._connect('rwc')
        try:
            connection.execute('BEGIN IMMEDIATE')
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            app_id = connection.execute('PRAGMA application_id').fetchone()[0]
            objects = connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchall()
            if version == 0 and app_id == 0 and not objects:
                for statement in SCHEMA:
                    connection.execute(statement)
                connection.execute(f'PRAGMA application_id={APPLICATION_ID}')
                connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            self._check_schema(connection)
            self._check_integrity(connection)
            connection.execute('COMMIT')
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute('ROLLBACK')
            if isinstance(exc, sqlite3.Error):
                raise _error(exc) from None
            raise
        finally:
            connection.close()

    def open(self, *, read_only=True):
        """Open an existing initialized database. Never create or migrate one."""
        if type(read_only) is not bool:
            raise ValueError('read_only must be a boolean.')
        if self._connection is not None:
            raise StoreStateError('SQLite store is already open.')
        connection = self._connect('ro' if read_only else 'rw')
        try:
            self._check_schema(connection)
            self._check_integrity(connection)
        except BaseException as exc:
            connection.close()
            if isinstance(exc, sqlite3.Error):
                raise _error(exc) from None
            raise
        self._connection = connection
        self._read_only = read_only
        return self

    def close(self):
        if self._transaction:
            raise StoreStateError('Cannot close a managed transaction in progress.')
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def validate_integrity(self):
        try:
            self._check_schema(self.connection)
            self._check_integrity(self.connection)
        except sqlite3.Error as exc:
            raise _error(exc) from None
        return True

    @contextmanager
    def transaction(self):
        """One short write transaction; commit on success, rollback on failure."""
        connection = self.connection
        if self._read_only or self._transaction or connection.in_transaction:
            raise StoreStateError('A writable store without an active transaction is required.')
        self._transaction = True
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.execute('COMMIT')
        except BaseException as exc:
            if connection.in_transaction:
                connection.execute('ROLLBACK')
            if isinstance(exc, sqlite3.Error):
                raise _error(exc) from None
            raise
        finally:
            self._transaction = False
