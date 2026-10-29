"""M2.5 lifecycle and feedback: temporary stores, offline runtime and explicit writes."""
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import experience
import memory_service as module
import tools
from agent_runtime import AgentRuntime
from memory_migration import migrate_json
from memory_retrieval import MemoryRetrieval
from memory_service import (MemoryService, MemoryDraft, SQLiteExperienceAdapter,
                            MemoryAccessDenied, MemoryValidationError, RevisionConflict)
from sqlite_store import SQLiteStore, StoreError, StoreStateError
from test_conversation import FakeProvider, final
from test_memory_service import services
from test_memory_retrieval import seed_experience


def test_imported_candidate_can_be_read_for_review(services, tmp_path):
    source = tmp_path / 'legacy.json'
    saved_at = '2026-01-01T12:30:00'
    source.write_text(json.dumps([{'text': 'weather sensors legacy', 'saved_at': saved_at}]))
    service = services()
    result = migrate_json(service._store.path, memory_files=[source], profile_id='athena',
                          principal_id='alice', dry_run=False)
    assert result.imported == 1
    identity = service._store.connection.execute('SELECT id FROM memories').fetchone()[0]
    record = service.read_memory(identity)
    assert record.created_at == saved_at
    assert record.draft.basis == 'legacy_unknown' and record.draft.status == 'candidate'


def test_feedback_changes_only_feedback_metadata(services, monkeypatch):
    service = services()
    seed_experience(service, task='weather sensors synthetic-saved-value')
    def payload():
        raw = service._store.connection.execute("SELECT payload_json FROM episodes WHERE id='past'").fetchone()[0]
        return json.loads(raw)
    before = payload()
    monkeypatch.setenv('TEST_LIFECYCLE_API_KEY', 'synthetic-saved-value')
    assert service.set_experience_feedback('past', 'positive')
    after = payload()
    for key in ('feedback', 'reward', 'quality'):
        after.pop(key)
        before.pop(key)
    assert after == before


def test_candidate_promotion_preserves_fields_and_identity(services):
    actions = []
    service = services(authorize=lambda action: actions.append(action) or True)
    identity = service.save_memory(MemoryDraft('weather sensors fact', status='candidate', scope='profile',
        kind='preference', subject_key='sensors', confidence_estimate=0.6,
        provenance_ref='review-note', expires_at='2099-01-01T03:00:00+03:00'))
    original = service.read_memory(identity)
    assert MemoryRetrieval(service).retrieve('weather sensors').memories == ()
    assert service.promote_memory(identity, expected_revision=1) == 2
    current = service.read_memory(identity)
    assert current.id == identity and current.owner_id == original.owner_id
    assert current.profile_id == original.profile_id and current.created_at == original.created_at
    assert current.draft == replace(original.draft, status='active')
    assert current.approved_by == service.principal_id
    assert current.revision == 2
    assert actions[-1].operation == 'update' and actions[-1].draft.status == 'active'
    assert actions[-1].expected_revision == 1
    assert MemoryRetrieval(service).retrieve('weather sensors').memories[0]['id'] == identity


@pytest.mark.parametrize('authorization', [None, False, 1, 'yes'])
def test_promotion_requires_exact_true_authorization(services, authorization):
    owner = services()
    identity = owner.save_memory(MemoryDraft('weather sensors', status='candidate'))
    caller = services(authorize=None if authorization is None else lambda action: authorization)
    before = owner.read_memory(identity)
    with pytest.raises(MemoryAccessDenied):
        caller.promote_memory(identity, expected_revision=1)
    assert owner.read_memory(identity) == before


@pytest.mark.parametrize('previous,target', [('active', 'candidate'), ('candidate', 'superseded'),
    ('superseded', 'active'), ('rejected', 'active'), ('rejected', 'candidate')])
def test_invalid_transitions_rejected_before_approval(services, previous, target):
    callback = Mock(return_value=True)
    service = services(authorize=callback)
    identity = service.save_memory(MemoryDraft('weather sensors', status=previous))
    callback.reset_mock()
    original = service.read_memory(identity)
    with pytest.raises(MemoryValidationError):
        service.update_memory(identity, replace(original.draft, status=target), expected_revision=1)
    callback.assert_not_called()
    assert service.read_memory(identity) == original


def test_explicit_operation_invalid_source_state(services):
    service = services()
    active = service.save_memory(MemoryDraft('weather sensors'))
    candidate = service.save_memory(MemoryDraft('weather sensors', status='candidate'))
    with pytest.raises(MemoryValidationError):
        service.promote_memory(active, expected_revision=1)
    with pytest.raises(MemoryValidationError):
        service.retire_memory(candidate, expected_revision=1)


@pytest.mark.parametrize('operation', ['promote_memory', 'retire_memory'])
def test_lifecycle_stale_revision(services, operation):
    service = services()
    status = 'candidate' if operation == 'promote_memory' else 'active'
    identity = service.save_memory(MemoryDraft('weather sensors', status=status))
    service.update_memory(identity, MemoryDraft('weather sensors revised', status=status), expected_revision=1)
    with pytest.raises(RevisionConflict):
        getattr(service, operation)(identity, expected_revision=1)
    assert service.read_memory(identity).revision == 2


def test_promotion_rechecks_revision_after_approval(services):
    owner = services()
    identity = owner.save_memory(MemoryDraft('weather sensors', status='candidate'))
    def approve(action):
        owner.update_memory(identity, MemoryDraft('weather sensors concurrent', status='candidate'), expected_revision=1)
        return True
    caller = services(authorize=approve)
    with pytest.raises(RevisionConflict):
        caller.promote_memory(identity, expected_revision=1)
    assert owner.read_memory(identity).draft.status == 'candidate'
    assert owner.read_memory(identity).revision == 2


def test_correction_reuses_update_and_retrieval(services):
    service = services()
    identity = service.save_memory(MemoryDraft('weather sensors old', scope='profile'))
    original = service.read_memory(identity)
    corrected = replace(original.draft, content='weather sensors corrected')
    assert service.update_memory(identity, corrected, expected_revision=1) == 2
    row = service.read_memory(identity)
    assert row.id == identity and row.owner_id == original.owner_id
    assert row.draft.scope == 'profile' and row.created_at == original.created_at
    assert MemoryRetrieval(service).retrieve('weather sensors').memories[0]['content'] == corrected.content
    with pytest.raises(RevisionConflict):
        service.update_memory(identity, original.draft, expected_revision=1)


def test_retirement_preserves_record_and_cannot_reactivate(services):
    service = services()
    identity = service.save_memory(MemoryDraft('weather sensors', scope='profile'))
    original = service.read_memory(identity)
    assert service.retire_memory(identity, expected_revision=1) == 2
    retired = service.inspect_memory(identity)
    assert retired.draft == replace(original.draft, status='superseded')
    assert retired.id == original.id and retired.owner_id == original.owner_id
    assert MemoryRetrieval(service).retrieve('weather sensors').memories == ()
    with pytest.raises(MemoryValidationError):
        service.update_memory(identity, original.draft, expected_revision=2)


@pytest.mark.parametrize('now,visible', [('2027-01-01T09:59:59.999999+00:00', True),
    ('2027-01-01T10:00:00+00:00', False), ('2027-01-01T10:00:00.000001+00:00', False)])
def test_utc_expiry_boundary_and_inspection(services, monkeypatch, now, visible):
    monkeypatch.setattr(module, '_now', lambda: now)
    service = services()
    identity = service.save_memory(MemoryDraft('weather sensors', expires_at='2027-01-01T13:00:00+03:00'))
    assert bool(MemoryRetrieval(service).retrieve('weather sensors').memories) is visible
    assert service.inspect_memory(identity).draft.expires_at == '2027-01-01T10:00:00+00:00'
    if not visible:
        with pytest.raises(MemoryAccessDenied):
            service.read_memory(identity)
        with pytest.raises(MemoryAccessDenied):
            service.retire_memory(identity, expected_revision=1)


def test_expired_inspection_is_owner_only(services):
    owner = services()
    identity = owner.save_memory(MemoryDraft('weather sensors', scope='profile', expires_at='2000-01-01T00:00:00+00:00'))
    assert owner.inspect_memory(identity).revision == 1
    for profile, principal in [('athena', 'bob'), ('other', 'alice')]:
        caller = services(profile=profile, principal=principal)
        with pytest.raises(MemoryAccessDenied):
            caller.inspect_memory(identity)


@pytest.mark.parametrize('operation,status', [('promote_memory', 'candidate'), ('retire_memory', 'active')])
def test_identity_cannot_change_lifecycle(services, operation, status):
    owner = services()
    identity = owner.save_memory(MemoryDraft('weather sensors', status=status, scope='profile'))
    for profile, principal in [('athena', 'bob'), ('other', 'alice')]:
        caller = services(profile=profile, principal=principal)
        with pytest.raises(MemoryAccessDenied):
            getattr(caller, operation)(identity, expected_revision=1)
    assert owner.read_memory(identity).revision == 1


def test_migration_promotion_end_to_end(services, tmp_path):
    source = tmp_path / 'legacy # % μ.json'
    source.write_text(json.dumps([{'id': 'legacy-id', 'revision': 4, 'text': 'weather sensors legacy',
                                 'saved_at': '2026-01-01T12:00:00'}]), encoding='utf-8')
    before = source.read_bytes()
    service = services()
    assert migrate_json(service._store.path, memory_files=[source], profile_id='athena',
                        principal_id='alice', dry_run=False).imported == 1
    assert not MemoryRetrieval(service).retrieve('weather sensors').memories
    assert service.promote_memory('legacy-id', expected_revision=4) == 5
    record = service.read_memory('legacy-id')
    assert record.created_at == '2026-01-01T12:00:00'
    assert record.draft.basis == 'legacy_unknown' and record.draft.epistemic_status == 'unknown'
    assert MemoryRetrieval(service).retrieve('weather sensors').memories[0]['id'] == 'legacy-id'
    assert source.read_bytes() == before
    assert migrate_json(service._store.path, memory_files=[source], profile_id='athena',
                        principal_id='alice', dry_run=False).conflicting == 1


@pytest.mark.parametrize('operation,status', [('promote_memory', 'candidate'), ('retire_memory', 'active'),
                                            ('update_memory', 'active')])
def test_failed_write_rolls_back_lifecycle(services, operation, status):
    service = services()
    identity = service.save_memory(MemoryDraft('weather sensors', status=status))
    original = service.read_memory(identity)
    service._store.connection.execute("CREATE TRIGGER fail_change AFTER UPDATE ON memories "
        "BEGIN SELECT RAISE(ABORT, 'synthetic rollback'); END")
    with pytest.raises(StoreError):
        if operation == 'update_memory':
            service.update_memory(identity, replace(original.draft, content='weather sensors corrected'), expected_revision=1)
        else:
            getattr(service, operation)(identity, expected_revision=1)
    assert service.read_memory(identity) == original


def test_runtime_observes_lifecycle_without_reconstruction(services):
    service = services()
    identity = service.save_memory(MemoryDraft('weather sensors original', status='candidate'))
    provider = FakeProvider([final('done')] * 4)
    runtime = AgentRuntime(provider, retrieval_service=MemoryRetrieval(service),
                           experience_service=SQLiteExperienceAdapter(service))
    assert runtime.run_agent('weather sensors') == 'done'
    assert 'weather sensors original' not in json.dumps(provider.requests[0])
    service.promote_memory(identity, expected_revision=1)
    runtime.run_agent('weather sensors')
    assert 'weather sensors original' in provider.requests[1][1]['content']
    record = service.read_memory(identity)
    service.update_memory(identity, replace(record.draft, content='weather sensors corrected'), expected_revision=2)
    runtime.run_agent('weather sensors')
    assert 'weather sensors corrected' in provider.requests[2][1]['content']
    service.retire_memory(identity, expected_revision=3)
    runtime.run_agent('weather sensors')
    assert 'weather sensors corrected' not in json.dumps(provider.requests[3])
    assert service.inspect_memory(identity).revision == 4


def test_read_only_retrieval_never_promotes_or_rates(services):
    callback = Mock(return_value=True)
    service = services(authorize=callback)
    identity = service.save_memory(MemoryDraft('weather sensors candidate', status='candidate'))
    seed_experience(service)
    callback.reset_mock()
    path = service._store.path
    service.close()
    before = path.read_bytes()
    service.open(read_only=True)
    for _ in range(3):
        assert MemoryRetrieval(service).retrieve('weather sensors').memories == ()
    assert service.inspect_memory(identity).draft.status == 'candidate'
    assert service.read_experience('past')['feedback'] is None
    with pytest.raises(StoreStateError):
        service.promote_memory(identity, expected_revision=1)
    callback.assert_not_called()
    service.close()
    assert path.read_bytes() == before


def test_explicit_feedback_changes_runtime_ranking(services):
    service = services()
    seed_experience(service, 'alpha', 'weather sensors alpha')
    seed_experience(service, 'beta', 'weather sensors beta')
    retrieval = MemoryRetrieval(service)
    assert [r['id'] for r in retrieval.retrieve('weather sensors').experiences] == ['alpha', 'beta']
    adapter = SQLiteExperienceAdapter(service)
    assert adapter.set_experience_feedback('beta', 'positive')
    assert [r['id'] for r in retrieval.retrieve('weather sensors').experiences] == ['beta', 'alpha']
    row = service.read_experience('beta')
    assert (row['feedback'], row['reward'], row['quality']) == ('positive', 1, 'user_validated')
    assert adapter.set_experience_feedback('beta', 'negative')
    assert [r['id'] for r in retrieval.retrieve('weather sensors').experiences] == ['alpha', 'beta']
    assert service._store.connection.execute('SELECT count(*) FROM feedback').fetchone()[0] == 1


@pytest.mark.parametrize('feedback', ['good', '', None, True, 1])
def test_invalid_feedback_leaves_experience_unchanged(services, feedback):
    service = services()
    seed_experience(service)
    before = service.read_experience('past')
    assert service.set_experience_feedback('past', feedback) is False
    assert service.read_experience('past') == before


def test_feedback_identity_and_visibility(services):
    owner = services()
    seed_experience(owner)
    for profile, principal in [('athena', 'bob'), ('other', 'alice')]:
        assert services(profile=profile, principal=principal).set_experience_feedback('past', 'positive') is False
    assert owner.read_experience('past')['feedback'] is None


def test_json_defaults_and_sqlite_opt_in_remain_separate(services):
    service = services()
    service.save_memory(MemoryDraft('weather sensors SQLITE_ONLY'))
    tools.MEMORY_FILE.write_text('[{"text":"weather sensors JSON_ONLY"}]')
    assert 'JSON_ONLY' in MemoryRetrieval().retrieve('weather sensors').context
    assert 'SQLITE_ONLY' not in MemoryRetrieval().retrieve('weather sensors').context
    assert 'SQLITE_ONLY' in MemoryRetrieval(service).retrieve('weather sensors').context
    assert 'JSON_ONLY' not in MemoryRetrieval(service).retrieve('weather sensors').context
    seed_experience(None, 'json-task')
    assert experience.set_experience_feedback('json-task', 'positive')
    assert experience.load_experiences()[0]['feedback'] == 'positive'


def test_runtime_completion_does_not_infer_feedback(services):
    service = services()
    provider = FakeProvider([final('completed')])
    runtime = AgentRuntime(provider, retrieval_service=MemoryRetrieval(service),
                           experience_service=SQLiteExperienceAdapter(service))
    runtime.run_agent('weather sensors')
    record = service.read_experience(runtime.conversation.last_experience_id)
    assert record['feedback'] is None and record['reward'] == 0
    assert record['quality'] == 'unverified'


def test_imported_memory_correction_preserves_naive_creation_time(services, tmp_path):
    service = services()
    source = tmp_path / 'legacy-correction.json'
    source.write_text('[{"id":"imported","text":"weather sensors old","saved_at":"2026-01-01T12:00:00"}]')
    migrate_json(service._store.path, memory_files=[source], profile_id='athena', principal_id='alice', dry_run=False)
    service.promote_memory('imported', expected_revision=1)
    service.update_memory('imported', MemoryDraft('weather sensors reviewed'), expected_revision=2)
    record = service.read_memory('imported')
    assert record.created_at == '2026-01-01T12:00:00' and record.revision == 3
    assert record.draft.basis == 'observation'
    assert MemoryRetrieval(service).retrieve('weather sensors').memories[0]['content'] == 'weather sensors reviewed'


def test_retirement_requires_approval(services):
    owner = services()
    identity = owner.save_memory(MemoryDraft('weather sensors'))
    denied = services(authorize=lambda action: False)
    with pytest.raises(MemoryAccessDenied):
        denied.retire_memory(identity, expected_revision=1)
    assert owner.read_memory(identity).draft.status == 'active'


def test_runtime_feedback_ranking_updates_without_new_runtime(services):
    service = services()
    seed_experience(service, 'alpha', 'weather sensors alpha')
    seed_experience(service, 'beta', 'weather sensors beta')
    provider = FakeProvider([final('done')] * 2)
    runtime = AgentRuntime(provider, retrieval_service=MemoryRetrieval(service),
                           experience_service=SQLiteExperienceAdapter(service))
    runtime.run_agent('weather sensors')
    assert provider.requests[0][1]['content'].index('alpha') < provider.requests[0][1]['content'].index('beta')
    assert service.set_experience_feedback('beta', 'positive')
    runtime.run_agent('weather sensors')
    context = provider.requests[1][1]['content']
    assert context.index('beta') < context.index('alpha')
