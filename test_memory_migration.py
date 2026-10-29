"""M2.3 synthetic migration tests; all persistence uses temporary paths."""
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

import pytest
import experience
from memory_migration import migrate_json, main
from memory_service import MemoryService
from sqlite_store import SQLiteStore, StoreError


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / 'legacy # % μ.json'
    database = tmp_path / 'destination # % μ.sqlite3'
    def run(records, *, kind='memory', dry_run=False, **kwargs):
        source.write_text(json.dumps(records), encoding='utf-8')
        return migrate_json(database, **{kind + '_files': [source]},
                            profile_id='athena', principal_id='owner', dry_run=dry_run, **kwargs)
    return source, database, run


def rows(database, table):
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute('SELECT * FROM ' + table)]


def memory(**kwargs):
    return dict(text='Remember the project', saved_at='2026-10-29T10:00:00', **kwargs)


def episode():
    record = experience.Experience('Inspect project configuration').record
    record.update(status='completed', error_type=None, error_message=None,
                  feedback='positive', reward=1, quality='user_validated',
                  lesson='Use the documented configuration', lesson_status='validated')
    recorder = experience.Experience('tools')
    recorder.tool('read_text_file', {'path': 'notes.txt'}, 'READ', 'hello', 1)
    record['tools_used'] = recorder.record['tools_used']
    return record


@pytest.mark.parametrize('kind', ['memory', 'experience'])
def test_empty(setup, kind):
    source, database, run = setup
    assert run([], kind=kind).committed
    assert not rows(database, 'memories') and not rows(database, 'episodes')


def test_memory_preservation_repeat_and_matching(setup):
    source, database, run = setup
    record = memory(id='old-id', revision=7)
    assert run([record]).imported == 1
    row = rows(database, 'memories')[0]
    assert row['id'] == 'old-id' and row['revision'] == 7
    assert row['content'] == record['text']
    assert row['created_at'] == row['updated_at'] == record['saved_at']
    assert row['status'] == 'candidate' and row['approved_by'] is None
    result = run([record])
    assert result.skipped == 1 and result.imported == 0 and result.committed
    assert len(rows(database, 'memories')) == 1


def test_generated_ids_and_duplicate_multiplicity(setup):
    source, database, run = setup
    assert run([memory(), memory()]).imported == 2
    assert run([memory(), memory()]).skipped == 2
    assert len(rows(database, 'memories')) == 2


def test_experience_preserves_all_supplied_fields(setup):
    source, database, run = setup
    record = episode()
    before = json.dumps([record])
    assert run([record], kind='experience').imported == 1
    service = MemoryService(SQLiteStore(database), 'athena', 'owner').open()
    try:
        assert service.read_experience(record['id']) == record
    finally:
        service.close()
    assert run([record], kind='experience').skipped == 1
    assert source.read_text(encoding='utf-8') == before


@pytest.mark.parametrize('dry_run', [True, False])
def test_conflicts_block_whole_batch(setup, dry_run):
    source, database, run = setup
    run([memory(id='same')])
    changed = memory(id='same')
    changed['text'] = 'Changed'
    result = run([changed, memory(id='new')], dry_run=dry_run)
    assert result.conflicting == 1 and not result.committed
    assert len(rows(database, 'memories')) == 1


def test_source_collisions(setup):
    source, database, run = setup
    one, two = memory(id='same'), memory(id='same')
    two['text'] = 'Other'
    result = run([one, two])
    assert result.conflicting == 1 and not database.exists()
    assert run([one, one]).skipped == 1


@pytest.mark.parametrize('raw', ['{', '{}', '[NaN]', '[{"text":1,"text":2}]', '\ufeff[]'])
def test_malformed_json(setup, raw):
    source, database, run = setup
    source.write_text(raw, encoding='utf-8')
    result = migrate_json(database, memory_files=[source], profile_id='a', principal_id='b', dry_run=False)
    assert result.invalid and not database.exists()
    assert source.read_text(encoding='utf-8') == raw


@pytest.mark.parametrize('record', [None, {}, {'text': '', 'saved_at': 'bad'},
                                    memory(revision=True)])
def test_invalid_records(setup, record):
    source, database, run = setup
    assert run([record]).invalid == 1
    assert not database.exists()


def test_unsupported_and_lossy_records(setup):
    source, database, run = setup
    assert run([memory(custom='metadata')]).unsupported == 1
    record = episode()
    record['reward'] = -1
    assert run([record], kind='experience').unsupported == 1
    record = episode()
    record['tools_used'][0]['raw_output'] = 'must not disappear'
    assert run([record], kind='experience').unsupported == 1
    assert not database.exists()


def test_dry_run_no_writes(setup):
    source, database, run = setup
    result = run([memory()], dry_run=True)
    assert result.valid == 1 and not result.committed and not database.exists()
    run([memory()])
    before = database.read_bytes()
    result = run([memory(), memory(id='next')], dry_run=True)
    assert result.valid == result.skipped == 1
    assert database.read_bytes() == before


def test_rollback_on_insert_failure(setup):
    source, database, run = setup
    run([])
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TRIGGER fail_import BEFORE INSERT ON memories WHEN NEW.id='fail' "
                           "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(StoreError):
        run([memory(id='first'), memory(id='fail')])
    assert rows(database, 'memories') == []


def test_mixed_batch_atomic_and_source_untouched(setup, tmp_path):
    source, database, run = setup
    source.write_text(json.dumps([memory()]), encoding='utf-8')
    other = tmp_path / 'experiences.json'
    other.write_text(json.dumps([episode()]), encoding='utf-8')
    before = {p: p.read_bytes() for p in (source, other)}
    result = migrate_json(database, memory_files=[source], experience_files=[other],
                          profile_id='athena', principal_id='owner', dry_run=False)
    assert result.imported == 2
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert len(rows(database, 'episodes')) == len(rows(database, 'memories')) == 1


def test_incompatible_schema(setup):
    source, database, run = setup
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE unrelated (value)')
    before = database.read_bytes()
    with pytest.raises(StoreError):
        run([memory()])
    assert database.read_bytes() == before


def test_wrong_profile_or_owner_conflicts(setup):
    source, database, run = setup
    run([memory(id='shared-id')])
    result = migrate_json(database, memory_files=[source], profile_id='other', principal_id='owner')
    assert result.conflicting == 1


def test_json_runtime_compatibility(setup, monkeypatch):
    import tools
    from tool_modules.memory import load_memory, save_memory
    source, database, run = setup
    run([memory()])
    monkeypatch.setattr(tools, 'MEMORY_FILE', source)
    assert load_memory() == [memory()]
    save_memory('JSON still works')
    assert len(load_memory()) == 2 and len(rows(database, 'memories')) == 1


def test_cli_explicit_modes_and_portable_paths(setup):
    source, database, run = setup
    source.write_text(json.dumps([memory()]), encoding='utf-8')
    args = ['--memory', str(source), '--database', str(database), '--profile', 'athena', '--principal', 'owner']
    with pytest.raises(SystemExit):
        main(args)
    assert main(args + ['--dry-run']) == 0 and not database.exists()
    assert main(args + ['--execute']) == 0 and database.exists()
    assert main(args + ['--dry-run']) == 0


def test_destination_cannot_be_source(setup):
    source, database, run = setup
    source.write_text('[]', encoding='utf-8')
    with pytest.raises(ValueError):
        migrate_json(source, memory_files=[source], profile_id='a', principal_id='b', dry_run=False)
    assert source.read_text() == '[]'


def test_reordered_source_does_not_duplicate(setup):
    source, database, run = setup
    a, b = memory(), memory()
    b['text'] = 'Another memory'
    assert run([a, b]).imported == 2
    assert run([b, a]).skipped == 2


def test_feedback_change_conflicts(setup):
    source, database, run = setup
    record = episode()
    run([record], kind='experience')
    service = MemoryService(SQLiteStore(database), 'athena', 'owner').open(read_only=False)
    try:
        assert service.set_experience_feedback(record['id'], 'negative')
    finally:
        service.close()
    assert run([record], kind='experience').conflicting == 1


def test_invalid_batch_does_not_initialize(setup):
    source, database, run = setup
    result = run([memory(), {}])
    assert result.valid == result.invalid == 1 and not database.exists()


def test_rollback_includes_new_identity(setup):
    source, database, run = setup
    run([])
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TRIGGER fail_all BEFORE INSERT ON memories "
                           "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    source.write_text(json.dumps([memory()]), encoding='utf-8')
    with pytest.raises(StoreError):
        migrate_json(database, memory_files=[source], profile_id='new-profile',
                     principal_id='new-owner', dry_run=False)
    assert [r['id'] for r in rows(database, 'identities')] == ['athena']


def test_cli_blocked_and_bad_source(setup):
    source, database, run = setup
    source.write_text('[{}]', encoding='utf-8')
    args = ['--memory', str(source), '--database', str(database), '--profile', 'a', '--principal', 'b']
    assert main(args + ['--execute']) == 1
    source.unlink()
    assert main(args + ['--dry-run']) == 2


def test_source_cannot_be_sqlite_sidecar(setup):
    source, database, run = setup
    source = database.with_name(database.name + '-journal')
    source.write_text('[]', encoding='utf-8')
    with pytest.raises(ValueError):
        migrate_json(database, memory_files=[source], profile_id='a', principal_id='b', dry_run=False)
    assert source.read_text() == '[]' and not database.exists()
