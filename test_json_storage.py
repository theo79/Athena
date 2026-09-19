"""Storage recovery and failed-commit tests; all files live in tmp_path."""
import json
from pathlib import Path

import pytest

import experience as exp
import json_storage as storage
import tools


def test_backup_keeps_previous_valid_generation(tmp_path):
    path = tmp_path / 'records.json'
    storage.write_list(path, [{'id': 'one'}])
    storage.write_list(path, [{'id': 'one'}, {'id': 'two'}])
    assert json.loads(path.with_name(path.name + '.bak').read_text()) == [{'id': 'one'}]
    assert len(storage.load_list(path)) == 2
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('raw', [b'{broken', b'', b'{}', b'null', b'\xff\xfe'])
def test_corruption_preserved_before_empty_replacement(tmp_path, raw):
    path = tmp_path / 'records.json'
    path.write_bytes(raw)
    assert storage.load_list(path) == []
    quarantine, = tmp_path.glob('records.json.corrupt-*')
    assert quarantine.read_bytes() == raw
    storage.write_list(path, [{'id': 'new'}])
    assert quarantine.read_bytes() == raw
    assert storage.load_list(path) == [{'id': 'new'}]


def test_corrupt_primary_restores_backup_and_preserves_bytes(tmp_path):
    path = tmp_path / 'records.json'
    storage.write_list(path, [{'id': 'one'}])
    storage.write_list(path, [{'id': 'one'}, {'id': 'two'}])
    path.write_bytes(b'broken newest generation')
    assert storage.load_list(path) == [{'id': 'one'}]
    quarantine, = tmp_path.glob('records.json.corrupt-*')
    assert quarantine.read_bytes() == b'broken newest generation'
    assert json.loads(path.read_text()) == [{'id': 'one'}]


def test_missing_primary_recovers_backup(tmp_path):
    path = tmp_path / 'records.json'
    path.with_name(path.name + '.bak').write_text('[{"id":"retained"}]')
    assert storage.load_list(path) == [{'id': 'retained'}]


def test_invalid_backup_is_not_destroyed_during_recovery(tmp_path):
    path = tmp_path / 'records.json'
    backup = path.with_name(path.name + '.bak')
    path.write_bytes(b'broken primary')
    backup.write_bytes(b'broken backup')
    assert storage.load_list(path) == []
    assert backup.read_bytes() == b'broken backup'
    storage.write_list(path, [{'id': 'new'}])
    quarantine, = tmp_path.glob('records.json.bak.corrupt-*')
    assert quarantine.read_bytes() == b'broken backup'


def test_failed_preservation_never_replaces_corrupt_primary(tmp_path, monkeypatch):
    path = tmp_path / 'records.json'
    path.write_bytes(b'irreplaceable corrupt bytes')
    monkeypatch.setattr(storage, '_atomic_bytes', lambda *args: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError):
        storage.load_list(path)
    assert path.read_bytes() == b'irreplaceable corrupt bytes'


def test_failed_primary_commit_keeps_old_data_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / 'records.json'
    storage.write_list(path, [{'id': 'old'}])
    original_replace = Path.replace
    def fail_primary(self, target):
        if target == path:
            raise OSError('simulated failed commit')
        return original_replace(self, target)
    monkeypatch.setattr(Path, 'replace', fail_primary)
    with pytest.raises(OSError):
        storage.write_list(path, [{'id': 'new'}])
    assert storage.load_list(path) == [{'id': 'old'}]
    assert json.loads(path.with_name(path.name + '.bak').read_text()) == [{'id': 'old'}]
    assert not list(tmp_path.glob('*.tmp'))


def test_experience_append_after_corruption_recovers_history():
    exp.save_experience({'id': 'one'})
    exp.save_experience({'id': 'two'})
    exp.EXPERIENCES_FILE.write_text('broken')
    exp.save_experience({'id': 'three'})
    assert [r['id'] for r in exp.load_experiences()] == ['one', 'three']
    assert list(exp.EXPERIENCES_FILE.parent.glob('experiences.json.corrupt-*'))


def test_memory_uses_same_backup_recovery(tmp_path, monkeypatch):
    path = tmp_path / 'memory.json'
    monkeypatch.setattr(tools, 'MEMORY_FILE', path)
    tools.save_memory('first fact')
    tools.save_memory('second fact')
    path.write_bytes(b'broken memory')
    tools.save_memory('third fact')
    assert [m['text'] for m in tools.load_memory()] == ['first fact', 'third fact']
    quarantine, = tmp_path.glob('memory.json.corrupt-*')
    assert quarantine.read_bytes() == b'broken memory'


def test_legacy_memory_and_experiences_are_not_rewritten(tmp_path):
    for name, records in [('memory.json', [{'text': 'fact', 'saved_at': 'old-date'}]),
                          ('experiences.json', [{'id': 'old', 'status': 'success'}])]:
        path = tmp_path / name
        original = json.dumps(records).encode()
        path.write_bytes(original)
        assert storage.load_list(path) == records
        assert path.read_bytes() == original


def test_backup_failure_prevents_primary_update(tmp_path, monkeypatch):
    path = tmp_path / 'records.json'
    storage.write_list(path, [{'id': 'old'}])
    original_replace = Path.replace
    def fail_backup(self, target):
        if target.name.endswith('.bak'):
            raise PermissionError('backup unavailable')
        return original_replace(self, target)
    monkeypatch.setattr(Path, 'replace', fail_backup)
    with pytest.raises(PermissionError):
        storage.write_list(path, [{'id': 'new'}])
    assert storage.load_list(path) == [{'id': 'old'}]
    assert not list(tmp_path.glob('*.tmp'))
