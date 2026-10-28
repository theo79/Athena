"""Standalone storage contracts. All databases and JSON fixtures are synthetic."""
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import time
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest

import sqlite_store as storage
from sqlite_store import (SQLiteStore, StoreError, StoreStateError,
                          SchemaVersionError, StoreIntegrityError, StoreBusyError)


STAMP = '2026-10-28T12:00:00+00:00'


@pytest.fixture
def store(tmp_path):
    value = SQLiteStore(tmp_path / 'synthetic.sqlite3')
    value.initialize()
    value.open(read_only=False)
    yield value
    value.close()


def identity(connection, name='profile'):
    connection.execute('INSERT INTO identities(id, created_at) VALUES (?, ?)', (name, STAMP))


def episode(connection, name='episode', profile='profile', **changes):
    values = dict(id=name, profile_id=profile, recorded_at=STAMP, summary='Synthetic task',
                  basis='observation', provenance_type='tool_execution',
                  execution_status='completed', access_scope='private', owner_id='user')
    values.update(changes)
    insert(connection, 'episodes', values)


def memory(connection, name='memory', profile='profile', **changes):
    values = dict(id=name, profile_id=profile, kind='knowledge', content='Synthetic fact',
                  basis='observation', epistemic_status='unknown', status='candidate',
                  provenance_type='user_statement', created_at=STAMP, updated_at=STAMP,
                  access_scope='private', owner_id='user')
    values.update(changes)
    insert(connection, 'memories', values)


def insert(connection, table, values):
    # Table and columns are test-owned constants, never untrusted input.
    columns = ', '.join(values)
    placeholders = ', '.join('?' for _ in values)
    connection.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', tuple(values.values()))


def test_import_and_construction_have_no_io(tmp_path):
    script = r'''
import os, sys
sys.dont_write_bytecode = True
def audit(event, args):
    if event in {'sqlite3.connect', 'builtins.input', 'socket.connect',
                 'socket.bind', 'os.mkdir', 'os.remove', 'os.rename'}:
        raise AssertionError('Unexpected construction side effect')
    if event == 'open':
        path, mode, flags = args
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError('Unexpected file write')
sys.addaudithook(audit)
import sqlite_store
store = sqlite_store.SQLiteStore(sys.argv[1])
assert 'agent' not in sys.modules
assert 'experience' not in sys.modules
assert 'tools' not in sys.modules
'''
    subprocess.run([sys.executable, '-c', script, str(tmp_path / 'absent.sqlite3')],
                   cwd=Path(storage.__file__).parent,
                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), check=True)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('read_only', [True, False])
def test_open_never_creates_missing_database(tmp_path, read_only):
    path = tmp_path / 'missing.sqlite3'
    value = SQLiteStore(path)
    with pytest.raises(StoreError):
        value.open(read_only=read_only)
    assert not path.exists()
    with pytest.raises(StoreStateError):
        value.validate_integrity()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('filename', ['synthetic.sqlite3', 'synthetic # % \u03b1.sqlite3'])
def test_initialization_and_restart_persistence(tmp_path, filename):
    path = tmp_path / filename
    first = SQLiteStore(path)
    first.initialize()
    with pytest.raises(StoreStateError):
        _ = first.connection
    first.open(read_only=False)
    assert first.connection.execute('PRAGMA user_version').fetchone()[0] == storage.SCHEMA_VERSION
    assert first.connection.execute('PRAGMA application_id').fetchone()[0] == storage.APPLICATION_ID
    assert first.connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    with first.transaction() as connection:
        identity(connection)
        memory(connection)
    first.close()
    second = SQLiteStore(path)
    second.initialize()  # Idempotent, does not erase data.
    second.open()
    assert second.connection.execute('SELECT content FROM memories').fetchone() == ('Synthetic fact',)
    assert second.validate_integrity()
    second.close()


def test_read_only_connection_rejects_writes(store):
    store.close()
    store.open()
    with pytest.raises(StoreStateError):
        with store.transaction():
            pass
    with pytest.raises(sqlite3.OperationalError):
        identity(store.connection)


@pytest.mark.parametrize('failure', [ValueError, KeyboardInterrupt])
def test_transaction_rollback_and_reuse(store, failure):
    with pytest.raises(failure):
        with store.transaction() as connection:
            identity(connection)
            raise failure('synthetic failure')
    assert not store.connection.in_transaction
    assert store.connection.execute('SELECT count(*) FROM identities').fetchone()[0] == 0
    with store.transaction() as connection:
        identity(connection)
    assert store.validate_integrity()


def test_constraint_failure_rolls_back_entire_transaction(store):
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            identity(connection)
            identity(connection)
    assert store.connection.execute('SELECT count(*) FROM identities').fetchone()[0] == 0


def test_missing_foreign_key_and_parent_deletion_are_rejected(store):
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            memory(connection)
    with store.transaction() as connection:
        identity(connection)
        memory(connection)
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            connection.execute('DELETE FROM identities')
    assert store.validate_integrity()


@pytest.mark.parametrize('version', [0, 2, 99])
def test_unsupported_versions_are_not_rewritten(store, version):
    store.connection.execute(f'PRAGMA user_version={version}')
    store.close()
    before = store.path.read_bytes()
    with pytest.raises(SchemaVersionError):
        store.open()
    with pytest.raises(SchemaVersionError):
        store.initialize()
    assert store.path.read_bytes() == before


def test_unrelated_database_is_not_adopted(tmp_path):
    path = tmp_path / 'unrelated.db'
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE unrelated (value TEXT)')
    connection.close()
    before = path.read_bytes()
    with pytest.raises(SchemaVersionError):
        SQLiteStore(path).initialize()
    assert path.read_bytes() == before


def test_empty_database_requires_explicit_initialization(tmp_path):
    path = tmp_path / 'empty.db'
    path.touch()
    with pytest.raises(SchemaVersionError):
        SQLiteStore(path).open()
    assert path.read_bytes() == b''
    SQLiteStore(path).initialize()
    assert path.stat().st_size > 0


def test_initialization_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / 'atomic.db'
    monkeypatch.setattr(storage, 'SCHEMA', storage.SCHEMA + ['INVALID SQL'])
    with pytest.raises(StoreError):
        SQLiteStore(path).initialize()
    connection = sqlite3.connect(path)
    assert connection.execute('PRAGMA user_version').fetchone() == (0,)
    assert connection.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall() == []
    connection.close()


@pytest.mark.parametrize('operation', ['open', 'initialize'])
def test_corruption_is_reported_without_overwrite(tmp_path, operation):
    path = tmp_path / 'corrupt.db'
    raw = b'synthetic private contents, not a database'
    path.write_bytes(raw)
    with pytest.raises(StoreIntegrityError) as error:
        getattr(SQLiteStore(path), operation)()
    assert 'synthetic private contents' not in str(error.value)
    assert str(path) not in str(error.value)
    assert path.read_bytes() == raw


def test_integrity_validation_detects_missing_table(store):
    store.connection.execute('DROP TABLE feedback')
    with pytest.raises(StoreIntegrityError):
        store.validate_integrity()


def test_integrity_validation_detects_foreign_key_damage(store):
    store.connection.execute('PRAGMA foreign_keys=OFF')
    memory(store.connection)
    store.connection.execute('PRAGMA foreign_keys=ON')
    with pytest.raises(StoreIntegrityError):
        store.validate_integrity()
    store.close()
    with pytest.raises(StoreIntegrityError):
        store.open()


def test_lock_wait_is_bounded_and_store_recovers(store):
    contender = SQLiteStore(store.path, timeout=0.05)
    contender.open(read_only=False)
    try:
        assert contender.connection.execute('PRAGMA busy_timeout').fetchone() == (50,)
        with store.transaction() as connection:
            identity(connection)
            start = time.monotonic()
            with pytest.raises(StoreBusyError):
                with contender.transaction():
                    pytest.fail('must not acquire writer lock')
            assert time.monotonic() - start < 2.0
        with contender.transaction() as connection:
            identity(connection, 'other')
    finally:
        contender.close()


@pytest.mark.parametrize('scope,owner,allowed', [
    ('private', 'user', True), ('profile', None, True), ('shared', None, True),
    ('private', None, False), ('private', '', False), ('private', ' ', False),
    ('public', None, False), ('UNKNOWN', 'user', False), (None, 'user', False),
])
def test_memory_scope_constraints(store, scope, owner, allowed):
    with store.transaction() as connection:
        identity(connection)
    if allowed:
        with store.transaction() as connection:
            memory(connection, access_scope=scope, owner_id=owner)
    else:
        with pytest.raises(StoreIntegrityError):
            with store.transaction() as connection:
                memory(connection, access_scope=scope, owner_id=owner)


@pytest.mark.parametrize('field,value', [
    ('kind', 'invented'), ('basis', 'verified_fact'), ('epistemic_status', 'certain'),
    ('status', 'invented'), ('confidence_estimate', -0.01), ('confidence_estimate', 1.01),
    ('confidence_estimate', 'certain'), ('revision', 0),
])
def test_memory_classification_and_estimate_constraints(store, field, value):
    with store.transaction() as connection:
        identity(connection)
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            memory(connection, **{field: value})


def test_observations_and_inferences_remain_distinct(store):
    with store.transaction() as connection:
        identity(connection)
        episode(connection, basis='observation')
        memory(connection, basis='inference', confidence_estimate=1.0)
    assert store.connection.execute('SELECT basis FROM episodes').fetchone() == ('observation',)
    assert store.connection.execute('SELECT basis, epistemic_status FROM memories').fetchone() == ('inference', 'unknown')


@pytest.mark.parametrize('kind', ['personal', 'preference'])
def test_active_personal_memory_requires_approval_metadata(store, kind):
    with store.transaction() as connection:
        identity(connection)
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            memory(connection, kind=kind, status='active')
    with store.transaction() as connection:
        memory(connection, kind=kind, status='active', approved_by='user', approved_at=STAMP)


def test_identity_and_personality_are_separate(store):
    with store.transaction() as connection:
        identity(connection)
        for version in (1, 2):
            insert(connection, 'personality_versions', dict(
                profile_id='profile', version=version, display_name='Athena',
                role_description='Assistant', created_at=STAMP, created_by='user'))
    assert store.connection.execute('SELECT count(*) FROM personality_versions').fetchone() == (2,)
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            connection.execute("UPDATE identities SET id='replacement'")


def test_evidence_and_supersession_cannot_cross_profiles(store):
    with store.transaction() as connection:
        identity(connection)
        identity(connection, 'other')
        memory(connection)
        episode(connection, profile='other')
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            insert(connection, 'memory_evidence', dict(
                id='evidence', profile_id='profile', memory_id='memory', episode_id='episode',
                relationship='supports', basis='observation', recorded_at=STAMP,
                access_scope='private', owner_id='user'))
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            memory(connection, 'replacement', profile='other', supersedes_id='memory')


def test_evidence_feedback_goals_and_import_tracking(store):
    with store.transaction() as connection:
        identity(connection)
        episode(connection)
        memory(connection)
        for relationship in ('supports', 'contradicts', 'corrects'):
            insert(connection, 'memory_evidence', dict(
                id=relationship, profile_id='profile', memory_id='memory', episode_id='episode',
                relationship=relationship, basis='observation', recorded_at=STAMP,
                access_scope='private', owner_id='user'))
        insert(connection, 'feedback', dict(id='feedback', profile_id='profile', episode_id='episode',
               actor_id='user', rating=1, recorded_at=STAMP, access_scope='private', owner_id='user'))
        insert(connection, 'goals', dict(id='goal', profile_id='profile', description='Synthetic goal',
               state='proposed', created_by='user', created_at=STAMP, updated_at=STAMP,
               access_scope='private', owner_id='user'))
        insert(connection, 'legacy_imports', dict(id='batch', profile_id='profile',
               source_fingerprint='synthetic-fingerprint', source_kind='memory',
               source_schema_version='legacy', imported_at=STAMP, record_count=0))
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            connection.execute('DELETE FROM episodes')
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            connection.execute("UPDATE goals SET state='running_forever'")
    assert store.validate_integrity()


def test_close_and_nested_transaction_contract(store):
    with store.transaction():
        with pytest.raises(StoreStateError):
            with store.transaction():
                pass
        with pytest.raises(StoreStateError):
            store.close()
    with pytest.raises(StoreStateError):
        store.initialize()
    with pytest.raises(StoreStateError):
        store.open()
    store.close()
    store.close()
    with pytest.raises(StoreStateError):
        _ = store.connection


@pytest.mark.parametrize('timeout', [-1, 61, float('inf'), float('nan'), True, '1'])
def test_invalid_timeouts(tmp_path, timeout):
    with pytest.raises(ValueError):
        SQLiteStore(tmp_path / 'absent.db', timeout=timeout)
    assert not list(tmp_path.iterdir())


def test_json_files_are_never_changed(tmp_path):
    original = {}
    for name in ('memory.json', 'experiences.json', 'memory.json.bak'):
        original[name] = b'[{"synthetic": "unchanged"}]'
        (tmp_path / name).write_bytes(original[name])
    value = SQLiteStore(tmp_path / 'new.db')
    value.initialize()
    value.open(read_only=False)
    with value.transaction() as connection:
        identity(connection)
    value.close()
    assert all((tmp_path / name).read_bytes() == raw for name, raw in original.items())


def test_restart_in_a_separate_process(store):
    with store.transaction() as connection:
        identity(connection)
    store.close()
    script = '''
import sys
from sqlite_store import SQLiteStore
store = SQLiteStore(sys.argv[1])
store.open()
assert store.connection.execute('SELECT id FROM identities').fetchall() == [('profile',)]
assert store.validate_integrity()
store.close()
'''
    subprocess.run([sys.executable, '-c', script, str(store.path)],
                   cwd=Path(storage.__file__).parent,
                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), check=True)


def test_read_only_operations_do_not_change_database(store):
    store.close()
    before = store.path.read_bytes()
    files = {path.name for path in store.path.parent.iterdir()}
    store.open()
    assert store.validate_integrity()
    assert store.connection.execute('SELECT id FROM identities').fetchall() == []
    store.close()
    assert store.path.read_bytes() == before
    assert {path.name for path in store.path.parent.iterdir()} == files


@pytest.mark.parametrize('artifact', ['identity_id_immutable', 'memory_lookup'])
def test_schema_validation_rejects_missing_supporting_objects(store, artifact):
    kind = 'TRIGGER' if artifact == 'identity_id_immutable' else 'INDEX'
    store.connection.execute(f'DROP {kind} {artifact}')
    with pytest.raises(StoreIntegrityError):
        store.validate_integrity()


def test_schema_validation_rejects_missing_column(store):
    store.connection.execute('ALTER TABLE episodes DROP COLUMN summary')
    store.close()
    with pytest.raises(StoreIntegrityError):
        store.open()


def test_wrong_application_id_is_rejected(store):
    store.connection.execute('PRAGMA application_id=123')
    store.close()
    before = store.path.read_bytes()
    with pytest.raises(SchemaVersionError):
        store.initialize()
    assert store.path.read_bytes() == before


def test_commit_failure_rolls_back_deferred_foreign_key(store):
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            connection.execute('PRAGMA defer_foreign_keys=ON')
            memory(connection)  # Deferred violation surfaces at COMMIT.
    assert not store.connection.in_transaction
    assert store.connection.execute('SELECT count(*) FROM memories').fetchone() == (0,)
    assert store.validate_integrity()


def test_failed_initialization_does_not_create_parent_directories(tmp_path):
    path = tmp_path / 'missing-parent' / 'database.db'
    with pytest.raises(StoreError):
        SQLiteStore(path).initialize()
    assert not path.parent.exists()


@pytest.mark.parametrize('path', ['', ' ', ':memory:', None])
def test_invalid_paths(path):
    with pytest.raises(ValueError):
        SQLiteStore(path)


def test_independent_paths_do_not_share_connections_or_data(tmp_path):
    left, right = (SQLiteStore(tmp_path / name) for name in ('left.db', 'right.db'))
    for value in (left, right):
        value.initialize()
        value.open(read_only=False)
    try:
        with left.transaction() as connection:
            identity(connection)
        assert right.connection.execute('SELECT id FROM identities').fetchall() == []
        assert left.connection is not right.connection
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize('table', ['episodes', 'memories', 'memory_evidence', 'feedback', 'goals'])
def test_all_memory_bearing_records_declare_explicit_scopes(store, table):
    columns = {row[1]: row for row in store.connection.execute(f'PRAGMA table_info({table})')}
    assert columns['access_scope'][3] == 1  # NOT NULL, without an implicit default.
    assert columns['access_scope'][4] is None
    assert 'owner_id' in columns and columns['profile_id'][3] == 1


def test_duplicate_feedback_and_import_fingerprints_are_rejected(store):
    with store.transaction() as connection:
        identity(connection)
        episode(connection)
        insert(connection, 'feedback', dict(id='first', profile_id='profile', episode_id='episode',
               actor_id='user', rating=1, recorded_at=STAMP, access_scope='private', owner_id='user'))
        insert(connection, 'legacy_imports', dict(id='batch', profile_id='profile',
               source_fingerprint='same', source_kind='memory', source_schema_version='legacy',
               imported_at=STAMP, record_count=0))
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            insert(connection, 'feedback', dict(id='second', profile_id='profile', episode_id='episode',
                   actor_id='user', rating=-1, recorded_at=STAMP, access_scope='private', owner_id='user'))
    with pytest.raises(StoreIntegrityError):
        with store.transaction() as connection:
            insert(connection, 'legacy_imports', dict(id='duplicate', profile_id='profile',
                   source_fingerprint='same', source_kind='memory', source_schema_version='legacy',
                   imported_at=STAMP, record_count=0))



@pytest.mark.parametrize('operation,read_only,mode', [
    ('initialize', None, 'rwc'), ('open', True, 'ro'), ('open', False, 'rw'),
])
def test_question_mark_path_is_encoded_without_filesystem_assumptions(
        tmp_path, monkeypatch, operation, read_only, mode):
    # '?' is legal on some filesystems and forbidden on others. Exercise the
    # URI boundary on every OS without attempting to create this filename.
    store = SQLiteStore(tmp_path / 'synthetic ? # %.sqlite3')
    connect = Mock(side_effect=sqlite3.OperationalError('synthetic connect failure'))
    monkeypatch.setattr(storage.sqlite3, 'connect', connect)
    with pytest.raises(StoreError):
        if operation == 'initialize':
            store.initialize()
        else:
            store.open(read_only=read_only)
    uri = connect.call_args.args[0]
    parsed = urlsplit(uri)
    assert parsed.scheme == 'file'
    assert parsed.query == 'mode=' + mode
    assert parsed.fragment == ''
    assert '%3F' in parsed.path and '%23' in parsed.path and '%25' in parsed.path
    assert connect.call_args.kwargs['uri'] is True
    assert not list(tmp_path.iterdir())
