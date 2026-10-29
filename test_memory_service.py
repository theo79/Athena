"""Optional SQLite service tests: synthetic data, no production backend changes."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest

import experience as legacy
from agent_runtime import AgentRuntime
from memory_service import (MemoryService, MemoryDraft, SQLiteExperienceAdapter,
                            MemoryAccessDenied, MemoryValidationError, RevisionConflict)
from sqlite_store import SQLiteStore, StoreError, StoreStateError, StoreIntegrityError
from test_conversation import FakeProvider, final
from test_retrieval import record, seed


@pytest.fixture
def services(tmp_path):
    opened = []

    def create(*, filename='synthetic # % database.sqlite3', profile='athena', principal='alice',
               authorize=lambda action: True, scopes=('private', 'profile'), **limits):
        service = MemoryService(SQLiteStore(tmp_path / filename), profile, principal,
                                authorize=authorize, scopes=scopes, **limits)
        service.initialize()
        service.open(read_only=False)
        opened.append(service)
        return service

    yield create
    for service in opened:
        service.close()


def test_import_and_construction_have_no_side_effects(tmp_path):
    script = r'''
import os, sys
sys.dont_write_bytecode = True
def forbidden(*args, **kwargs):
    raise AssertionError('Unexpected import/construction side effect')
def audit(event, args):
    if event in {'sqlite3.connect', 'builtins.input', 'socket.connect',
                 'socket.bind', 'socket.getaddrinfo', 'os.mkdir', 'os.remove', 'os.rename'}:
        forbidden()
    if event == 'open':
        path, mode, flags = args
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            forbidden()
        if isinstance(path, (str, bytes)):
            name = os.fsdecode(path).replace('\\', '/').rsplit('/', 1)[-1].lower()
            if name.startswith(('.env', 'memory.json', 'experiences.json')) or name == 'config.json':
                forbidden()
sys.addaudithook(audit)
import runtime_paths
runtime_paths.load_configuration = forbidden
from sqlite_store import SQLiteStore
from memory_service import MemoryService, SQLiteExperienceAdapter
service = MemoryService(SQLiteStore(sys.argv[1]), 'athena', 'alice')
adapter = SQLiteExperienceAdapter(service)
assert 'agent' not in sys.modules
'''
    subprocess.run([sys.executable, '-c', script, str(tmp_path / 'absent.db')],
                   cwd=Path(__file__).parent,
                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), check=True)
    assert not list(tmp_path.iterdir())


def test_explicit_initialization_and_lifecycle(tmp_path):
    store = SQLiteStore(tmp_path / 'explicit.db')
    service = MemoryService(store, 'athena', 'alice')
    with pytest.raises(StoreError):
        service.open()
    with pytest.raises(StoreStateError):
        service.save_memory(MemoryDraft('synthetic'))
    assert not store.path.exists()
    service.initialize()
    with pytest.raises(StoreStateError):
        _ = store.connection
    service.open()
    with pytest.raises(StoreStateError):
        service.save_memory(MemoryDraft('synthetic'))
    with pytest.raises(StoreStateError):
        service.initialize()
    service.close()
    service.close()
    with pytest.raises(StoreStateError):
        service.find_memories('synthetic')


def test_open_cannot_create_a_new_profile(services):
    existing = services()
    outsider = MemoryService(SQLiteStore(existing._store.path), 'absent', 'alice')
    before = existing._store.path.read_bytes()
    with pytest.raises(MemoryAccessDenied):
        outsider.open()
    assert existing._store.path.read_bytes() == before


@pytest.mark.parametrize('result', [None, False, 1, 'yes', {'approved': True}])
def test_exact_true_host_authorization_required(services, result):
    callback = Mock(return_value=result)
    service = services(authorize=callback)
    before = service._store.path.read_bytes()
    with pytest.raises(MemoryAccessDenied):
        service.save_memory(MemoryDraft('Synthetic personal fact'))
    assert service._store.path.read_bytes() == before
    assert service.find_memories('Synthetic') == []
    callback.assert_called_once()


def test_missing_callback_and_callback_failure_fail_closed(services):
    for callback in (None, Mock(side_effect=RuntimeError('synthetic callback failure'))):
        service = services(authorize=callback)
        before = service._store.path.read_bytes()
        with pytest.raises(MemoryAccessDenied):
            service.save_memory(MemoryDraft('Synthetic personal fact'))
        assert service._store.path.read_bytes() == before


def test_model_fields_cannot_supply_authorization(services):
    service = services(authorize=None)
    before = service._store.path.read_bytes()
    with pytest.raises(MemoryValidationError):
        service.save_memory({'content': 'synthetic', 'approved': True})
    with pytest.raises(TypeError):
        service.save_memory(MemoryDraft('synthetic'), approved=True)
    with pytest.raises(TypeError):
        MemoryDraft('synthetic', approved_by='user')
    assert service._store.path.read_bytes() == before


def test_profile_principal_and_database_isolation(services):
    alice = services()
    bob = services(principal='bob')
    other_profile = services(profile='other')
    other_path = services(filename='other.db')
    identity = alice.save_memory(MemoryDraft('Synthetic private project'))
    for other in (bob, other_profile, other_path):
        assert other.find_memories('project') == []
        with pytest.raises(MemoryAccessDenied):
            other.read_memory(identity)
        with pytest.raises(MemoryAccessDenied):
            other.update_memory(identity, MemoryDraft('replacement'), expected_revision=1)
    saved = alice.read_memory(identity)
    assert saved.profile_id == 'athena' and saved.owner_id == 'alice'
    assert saved.approved_by == 'alice' and saved.approved_at


def test_scope_grants_do_not_grant_write_ownership(services):
    owner = services(scopes=('private', 'profile', 'shared'))
    reader = services(principal='bob')
    restricted = services(principal='bob', scopes=('private',))
    shared_reader = services(principal='bob', scopes=('shared',))
    profile_id = owner.save_memory(MemoryDraft('Synthetic profile fact', scope='profile'))
    shared_id = owner.save_memory(MemoryDraft('Synthetic shared fact', scope='shared'))
    assert reader.read_memory(profile_id).draft.scope == 'profile'
    assert shared_reader.read_memory(shared_id).draft.scope == 'shared'
    for service, identity in ((reader, shared_id), (restricted, profile_id)):
        with pytest.raises(MemoryAccessDenied):
            service.read_memory(identity)
    with pytest.raises(MemoryAccessDenied):
        reader.update_memory(profile_id, MemoryDraft('replacement', scope='profile'), expected_revision=1)


def test_candidates_and_expired_records_are_not_retrieved(services):
    owner = services()
    candidate = owner.save_memory(MemoryDraft('Synthetic candidate fact', status='candidate', scope='profile'))
    expired = owner.save_memory(MemoryDraft('Synthetic expired fact', expires_at='2000-01-01T00:00:00+00:00'))
    reader = services(principal='bob')
    assert owner.find_memories('Synthetic') == []
    assert owner.read_memory(candidate).draft.status == 'candidate'
    for service, identity in ((reader, candidate), (owner, expired)):
        with pytest.raises(MemoryAccessDenied):
            service.read_memory(identity)


def test_all_classifications_remain_distinct(services):
    callback = Mock(return_value=True)
    service = services(authorize=callback)
    drafts = [MemoryDraft('Synthetic personal', kind='personal'),
              MemoryDraft('Synthetic preference', kind='preference', status='candidate'),
              MemoryDraft('Synthetic observation', kind='knowledge', basis='observation'),
              MemoryDraft('Synthetic inference', kind='knowledge', basis='inference', confidence_estimate=1.0)]
    saved = [service.read_memory(service.save_memory(draft)) for draft in drafts]
    assert [entry.draft.kind for entry in saved] == ['personal', 'preference', 'knowledge', 'knowledge']
    assert saved[-1].draft.basis == 'inference'
    assert saved[-1].draft.epistemic_status == 'unknown'
    assert saved[1].draft.status == 'candidate'
    assert callback.call_count == 4


@pytest.mark.parametrize('change', [
    {'kind': 'episode'}, {'basis': 'verified'}, {'confidence_estimate': True},
    {'confidence_estimate': float('nan')}, {'confidence_estimate': 1.1},
    {'basis': 'inference', 'epistemic_status': 'user_asserted'},
    {'epistemic_status': 'corroborated'}, {'scope': 'public'}, {'scope': 'shared'},
    {'content': ''}, {'content': 'x' * 2001}, {'provenance_type': ''},
    {'provenance_ref': 'x' * 501}, {'expires_at': '2026-10-28'},
    {'expires_at': 'invalid'}, {'status': 'invented'},
])
def test_invalid_drafts_do_not_invoke_approval_or_modify_storage(services, change):
    callback = Mock(return_value=True)
    service = services(authorize=callback)
    before = service._store.path.read_bytes()
    with pytest.raises(MemoryValidationError):
        service.save_memory(replace(MemoryDraft('Synthetic fact'), **change))
    callback.assert_not_called()
    assert service._store.path.read_bytes() == before


def test_revision_checked_updates_and_approval_binding(services):
    actions = []
    service = services(authorize=lambda action: actions.append(action) or True)
    identity = service.save_memory(MemoryDraft('Synthetic old fact'))
    original = service.read_memory(identity)
    assert service.update_memory(identity, MemoryDraft('Synthetic correction'), expected_revision=1) == 2
    updated = service.read_memory(identity)
    assert updated.draft.content == 'Synthetic correction'
    assert updated.created_at == original.created_at
    assert updated.revision == 2
    assert actions[-1].record_id == identity and actions[-1].expected_revision == 1
    with pytest.raises(RevisionConflict):
        service.update_memory(identity, MemoryDraft('stale'), expected_revision=1)
    assert service.read_memory(identity) == updated
    with pytest.raises(MemoryValidationError):
        service.update_memory(identity, MemoryDraft('changed classification', kind='knowledge'), expected_revision=2)


def test_update_rechecks_revision_after_host_approval(services):
    owner = services()
    identity = owner.save_memory(MemoryDraft('Synthetic original'))
    peer = services()

    def approve(action):
        peer.update_memory(identity, MemoryDraft('Synthetic concurrent correction'), expected_revision=1)
        return True

    caller = services(authorize=approve)
    with pytest.raises(RevisionConflict):
        caller.update_memory(identity, MemoryDraft('stale correction'), expected_revision=1)
    assert owner.read_memory(identity).draft.content == 'Synthetic concurrent correction'


def test_denied_update_leaves_revision_and_content_unchanged(services):
    owner = services()
    identity = owner.save_memory(MemoryDraft('Synthetic original'))
    caller = services(authorize=None)
    before = owner.read_memory(identity)
    with pytest.raises(MemoryAccessDenied):
        caller.update_memory(identity, MemoryDraft('replacement'), expected_revision=1)
    assert owner.read_memory(identity) == before


def test_retrieval_count_context_bound_and_deterministic_ties(services):
    service = services(max_results=3, context_chars=4000)
    identities = [service.save_memory(MemoryDraft('Synthetic ' + str(i) + ' ' + 'x' * 1500)) for i in range(8)]
    # A common timestamp makes the documented ID tie break observable.
    service._store.connection.execute("UPDATE memories SET updated_at='2026-01-01T00:00:00+00:00'")
    first = service.find_memories('Synthetic')
    assert first == service.find_memories('Synthetic')
    assert 0 < len(first) <= 3
    assert [entry.id for entry in first] == sorted(identities)[:len(first)]
    assert len(json.dumps([asdict(entry) for entry in first], ensure_ascii=False)) <= 4000


def test_memory_candidate_window_and_kind_filter(services):
    service = services(candidate_limit=1)
    service.save_memory(MemoryDraft('Synthetic old rare topic'))
    service.save_memory(MemoryDraft('Synthetic newest topic', kind='knowledge'))
    assert service.find_memories('rare') == []  # Explicit recent candidate window.
    assert service.find_memories('topic', kind='knowledge')[0].draft.kind == 'knowledge'
    assert service.find_memories('topic', kind='preference') == []


@pytest.mark.parametrize('limit', [0, -1, 6, True, '1'])
def test_invalid_retrieval_limits(services, limit):
    service = services()
    with pytest.raises(MemoryValidationError):
        service.find_memories('Synthetic', limit=limit)


def test_sql_like_input_is_data_not_an_executable_query(services):
    service = services()
    identity = service.save_memory(MemoryDraft("Synthetic '; DROP TABLE memories; --"))
    assert service.find_memories("'; DROP TABLE memories; --")[0].id == identity
    assert service._store.validate_integrity()


def test_memory_transaction_rolls_back_after_database_constraint_failure(services):
    service = services()
    service._store.connection.execute("""CREATE TRIGGER reject_test AFTER INSERT ON memories
        BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
    with pytest.raises(StoreIntegrityError):
        service.save_memory(MemoryDraft('Synthetic fact'))
    assert service._store.connection.execute('SELECT count(*) FROM memories').fetchone() == (0,)
    assert not service._store.connection.in_transaction


def test_optional_runtime_adapter_persists_across_restart_without_json(services, monkeypatch):
    service = services()
    adapter = SQLiteExperienceAdapter(service)
    monkeypatch.setattr(legacy, 'load_experiences', Mock(side_effect=AssertionError('JSON read')))
    monkeypatch.setattr(legacy, 'save_experience', Mock(side_effect=AssertionError('JSON write')))
    runtime = AgentRuntime(FakeProvider([final('Synthetic answer')]), experience_service=adapter)
    assert runtime.run_agent('Synthetic task') == 'Synthetic answer'
    identity = runtime.conversation.last_experience_id
    assert identity
    path = service._store.path
    service.close()
    reopened = MemoryService(SQLiteStore(path), 'athena', 'alice')
    reopened.open()
    try:
        saved = reopened.read_experience(identity)
        assert saved['status'] == 'completed' and saved['steps'] == 1
        assert saved['final_answer'] == 'Synthetic answer'
        assert reopened.find_relevant_experiences('Synthetic task')[0]['evidence'] == 'unverified_example'
    finally:
        reopened.close()
    legacy.load_experiences.assert_not_called()
    legacy.save_experience.assert_not_called()
    assert not legacy.EXPERIENCES_FILE.exists()


@pytest.mark.parametrize('status', sorted(legacy.OUTCOMES))
def test_historical_outcomes_keep_execution_separate_from_feedback(services, status):
    service = services()
    item = record('legacy', 'weather sensors', status)
    service.record_experience(item)
    assert service.set_experience_feedback('legacy', 'positive')
    found, = service.find_relevant_experiences('weather sensors')
    assert found['evidence'] == ('positive_example' if status in {'success', 'completed'} else 'caution')
    assert found['status'] == ('completed' if status == 'success' else status)
    assert found['quality'] == 'user_validated'


def test_sqlite_retrieval_matches_legacy_algorithm_and_order(services):
    service = services()
    originals = [
        dict(record('old', 'Read CONFIG file'), feedback='positive', timestamp='2025-01-01T00:00:00Z'),
        dict(record('new', 'please read local config file'), feedback='positive', timestamp='2026-01-01T00:00:00Z'),
        dict(record('failed', 'read config file settings', 'tool_failure'), feedback='positive'),
        dict(record('unrelated', 'astronomy telescope'), feedback='positive'),
        dict(record('negative', 'read config file environment'), feedback='negative', reward=999),
    ]
    seed(originals)
    for item in originals:
        service.record_experience(item)
    assert service.find_relevant_experiences('read config file') == legacy.find_relevant_experiences('read config file')


def test_missing_episode_dates_preserve_file_order(services):
    service = services()
    originals = [record('one', 'weather sensors north'), record('two', 'weather sensors south')]
    for item in originals:
        service.record_experience(item)
    assert service.find_relevant_experiences('weather sensors') == legacy.rank_experiences('weather sensors', originals)


def test_episode_window_keeps_legacy_tie_order_within_candidates(services):
    service = services(candidate_limit=2)
    for i in range(4):
        service.record_experience(record(str(i), f'weather sensors region{i}'))
    assert [entry['id'] for entry in service.find_relevant_experiences('weather sensors')] == ['2', '3']


def test_episode_privacy_and_feedback_owner_checks(services):
    owner = services()
    bob, other_profile = services(principal='bob'), services(profile='other')
    owner.record_experience(record('task', 'weather sensors'))
    for other in (bob, other_profile):
        assert other.find_relevant_experiences('weather sensors') == []
        with pytest.raises(MemoryAccessDenied):
            other.read_experience('task')
        assert other.set_experience_feedback('task', 'positive') is False
    assert owner.read_experience('task')['feedback'] is None


def test_episode_expiry_is_enforced_on_all_access_paths(services):
    service = services()
    service.record_experience(record('task', 'weather sensors'))
    service._store.connection.execute("UPDATE episodes SET expires_at='2000-01-01T00:00:00+00:00'")
    assert service.find_relevant_experiences('weather sensors') == []
    assert service.set_experience_feedback('task', 'positive') is False
    with pytest.raises(MemoryAccessDenied):
        service.read_experience('task')


def test_feedback_replaces_and_persists_without_accumulation(services):
    service = services()
    adapter = SQLiteExperienceAdapter(service)
    service.record_experience(record('task', 'weather sensors'))
    for feedback in ('positive', 'negative', 'neutral'):
        assert adapter.set_experience_feedback('task', feedback)
        saved = service.read_experience('task')
        assert saved['feedback'] == feedback and saved['reward'] == legacy.FEEDBACK_REWARDS[feedback]
        assert saved['quality'] == legacy.FEEDBACK_QUALITY[feedback]
    assert service._store.connection.execute('SELECT rating FROM feedback').fetchall() == [(0,)]
    assert adapter.set_experience_feedback('absent', 'positive') is False


def test_feedback_and_episode_update_are_one_transaction(services):
    service = services()
    service.record_experience(record('task', 'weather sensors'))
    service._store.connection.execute("""CREATE TRIGGER reject_feedback AFTER INSERT ON feedback
        BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
    assert SQLiteExperienceAdapter(service).set_experience_feedback('task', 'positive') is False
    assert service.read_experience('task')['feedback'] is None
    assert service._store.connection.execute('SELECT count(*) FROM feedback').fetchone() == (0,)


@pytest.mark.parametrize('change', [
    {'id': ''}, {'task': 'x' * 1001}, {'status': 'verified'}, {'steps': True},
    {'parse_failures': 6}, {'tools_used': [None]}, {'feedback': 'self-approved'},
    {'approved': True}, {'final_answer': 'x' * 5001}, {'budget_exhausted': 1},
])
def test_invalid_episode_inputs_do_not_modify_storage(services, change):
    service = services()
    before = service._store.path.read_bytes()
    with pytest.raises(MemoryValidationError):
        service.record_experience(dict(record('task', 'weather sensors'), **change))
    assert service._store.path.read_bytes() == before


def test_episode_id_collision_cannot_overwrite_another_profile(services):
    owner, other = services(), services(profile='other')
    owner.record_experience(record('task', 'weather sensors'))
    with pytest.raises(StoreIntegrityError):
        other.record_experience(record('task', 'different task'))
    assert owner.read_experience('task')['task'] == 'weather sensors'


def test_adapter_best_effort_logging_failure_and_programming_error_behavior(services):
    service = services()
    adapter = SQLiteExperienceAdapter(service)
    service.close()
    runtime = AgentRuntime(FakeProvider([final('answer')]), experience_service=adapter)
    # Retrieval is not silently hidden: a closed service is a host lifecycle error.
    with pytest.raises(StoreStateError):
        runtime.run_agent('task')
    service.open(read_only=False)
    service._store.connection.execute("""CREATE TRIGGER reject_episode AFTER INSERT ON episodes
        BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
    assert runtime.run_agent('task') == 'answer'
    assert runtime.conversation.last_experience_id is None


def test_json_fixtures_are_untouched(services, tmp_path):
    contents = {name: b'[{"synthetic": "unchanged"}]' for name in
                ('memory.json', 'experiences.json', 'memory.json.bak')}
    for name, raw in contents.items():
        (tmp_path / name).write_bytes(raw)
    service = services()
    service.save_memory(MemoryDraft('Synthetic fact'))
    service.record_experience(record('task', 'weather sensors'))
    assert all((tmp_path / name).read_bytes() == raw for name, raw in contents.items())


@pytest.mark.parametrize('kwargs', [
    {'profile_id': ''}, {'principal_id': ''}, {'scopes': ()}, {'scopes': ('public',)},
    {'max_results': 0}, {'candidate_limit': 1001}, {'context_chars': 100000}, {'authorize': True},
])
def test_invalid_service_configuration_has_no_io(tmp_path, kwargs):
    values = dict(profile_id='athena', principal_id='alice')
    values.update(kwargs)
    with pytest.raises(MemoryValidationError):
        MemoryService(SQLiteStore(tmp_path / 'absent.db'), **values)
    assert not list(tmp_path.iterdir())


def test_expiry_is_normalized_and_provenance_preserved(services):
    service = services()
    draft = MemoryDraft('Synthetic fact', provenance_ref='synthetic-source:42',
                        expires_at='2099-01-01T03:00:00+03:00', epistemic_status='user_asserted')
    stored = service.read_memory(service.save_memory(draft))
    assert stored.draft.expires_at == '2099-01-01T00:00:00+00:00'
    assert stored.draft.provenance_ref == 'synthetic-source:42'
    assert stored.draft.epistemic_status == 'user_asserted'


def test_scope_changes_require_explicit_approval_for_exact_draft(services):
    actions = []
    owner = services(authorize=lambda action: actions.append(action) or True)
    identity = owner.save_memory(MemoryDraft('Synthetic fact'))
    bob = services(principal='bob')
    with pytest.raises(MemoryAccessDenied):
        bob.read_memory(identity)
    owner.update_memory(identity, MemoryDraft('Synthetic fact', scope='profile'), expected_revision=1)
    assert actions[-1].draft.scope == 'profile'
    assert bob.read_memory(identity).draft.scope == 'profile'


def test_service_scope_policy_is_copied_from_host_configuration(services):
    scopes = ['private']
    owner = services()
    profile_id = owner.save_memory(MemoryDraft('Synthetic profile', scope='profile'))
    reader = services(principal='bob', scopes=scopes)
    scopes.append('profile')
    with pytest.raises(MemoryAccessDenied):
        reader.read_memory(profile_id)
    with pytest.raises(AttributeError):
        reader.profile_id = 'other'
    with pytest.raises(AttributeError):
        reader.principal_id = 'alice'


def test_profile_existence_is_rechecked_on_each_operation(services):
    service = services()
    service._store.connection.execute('DELETE FROM identities')
    with pytest.raises(MemoryAccessDenied):
        service.find_memories('Synthetic')
    with pytest.raises(MemoryAccessDenied):
        service.save_memory(MemoryDraft('Synthetic fact'))


def test_candidate_preference_cannot_be_activated_without_new_approval(services):
    owner = services()
    identity = owner.save_memory(MemoryDraft('Synthetic preference', kind='preference', status='candidate'))
    denied = services(authorize=None)
    with pytest.raises(MemoryAccessDenied):
        denied.update_memory(identity, MemoryDraft('Synthetic preference', kind='preference'), expected_revision=1)
    assert owner.read_memory(identity).draft.status == 'candidate'
    assert owner.find_memories('Synthetic') == []


def test_runtime_parser_failure_and_programming_error_are_recorded(services):
    service = services()
    runtime = AgentRuntime(FakeProvider(['invalid'] * 5), experience_service=SQLiteExperienceAdapter(service))
    assert runtime.run_agent('weather sensors') == 'Maximum number of steps reached.'
    saved = service.read_experience(runtime.conversation.last_experience_id)
    assert saved['status'] == 'parse_failure' and saved['parse_failures'] == 5
    provider = Mock()
    provider.generate.side_effect = TypeError('synthetic programming error')
    broken = AgentRuntime(provider, experience_service=SQLiteExperienceAdapter(service))
    with pytest.raises(TypeError):
        broken.run_agent('weather sensors north')
    assert broken.conversation.last_experience_id is None
    assert service.find_relevant_experiences('weather sensors north')[0]['status'] == 'internal_error'


def test_telemetry_omits_memory_contents_and_raw_tool_output(services):
    service = services()
    adapter = SQLiteExperienceAdapter(service)
    run = adapter.Experience('weather sensors')
    from tool_modules.results import ToolResult
    run.tool('save_memory', {'text': 'synthetic private personal memory'}, 'WRITE_LOCAL',
             ToolResult.ok('synthetic raw tool output'), 1)
    run.finish('handled', 'completed')
    saved = service.read_experience(run.persist())
    assert saved['tools_used'][0]['args'] == {'text': '[omitted]'}
    assert 'synthetic private personal memory' not in json.dumps(saved)
    assert 'synthetic raw tool output' not in json.dumps(saved)


def test_experience_context_preserves_existing_privacy_and_bounds(services):
    service = services()
    for i in range(10):
        service.record_experience(record(str(i), f'weather sensors region{i}'))
    adapter = SQLiteExperienceAdapter(service)
    matches = adapter.find_relevant_experiences('weather sensors', limit=100)
    context = adapter.build_experience_context(matches)
    assert len(matches) == 3 and len(context) < 5000
    assert 'RAW OLD ANSWER' not in context and 'RAW INTERNAL ERROR' not in context
    assert adapter.find_relevant_experiences('weather sensors', limit=0) == []
    assert adapter.find_relevant_experiences('') == []


def test_adapter_accepts_long_runtime_tasks_without_expanding_history(services):
    service = services()
    runtime = AgentRuntime(FakeProvider([final('handled')]), experience_service=SQLiteExperienceAdapter(service))
    assert runtime.run_agent('weather sensors ' * 200) == 'handled'
    saved = service.read_experience(runtime.conversation.last_experience_id)
    assert len(saved['task']) <= 1000


def test_corrupt_optional_history_does_not_abort_a_task(services):
    service = services()
    service.record_experience(record('past', 'weather sensors'))
    service._store.connection.execute("UPDATE episodes SET payload_json='invalid json'")
    with pytest.raises(MemoryValidationError):
        service.read_experience('past')
    runtime = AgentRuntime(FakeProvider([final('handled')]), experience_service=SQLiteExperienceAdapter(service))
    assert runtime.run_agent('weather sensors') == 'handled'
    assert runtime.conversation.last_experience_id


def test_scopes_apply_to_episode_reads_and_feedback_even_for_shared_rows(services):
    owner = services()
    owner.record_experience(record('past', 'weather sensors'))
    owner._store.connection.execute("UPDATE episodes SET access_scope='shared'")
    restricted = services(principal='bob')
    shared = services(principal='bob', scopes=('shared',))
    with pytest.raises(MemoryAccessDenied):
        restricted.read_experience('past')
    assert restricted.find_relevant_experiences('weather sensors') == []
    assert shared.read_experience('past')['id'] == 'past'
    assert shared.find_relevant_experiences('weather sensors')
    assert shared.set_experience_feedback('past', 'positive') is False


def test_oversized_storage_bypasses_are_not_returned(services):
    service = services()
    identity = service.save_memory(MemoryDraft('Synthetic fact'))
    service.record_experience(record('past', 'weather sensors'))
    service._store.connection.execute('UPDATE memories SET content=?', ('x' * 100000,))
    service._store.connection.execute('UPDATE episodes SET payload_json=?', ('x' * 100000,))
    assert service.find_memories('Synthetic') == []
    assert service.find_relevant_experiences('weather sensors') == []
    with pytest.raises(MemoryAccessDenied):
        service.read_memory(identity)
    with pytest.raises(MemoryAccessDenied):
        service.read_experience('past')


def test_save_timestamps_are_captured_after_host_approval(services, monkeypatch):
    import memory_service
    clock = ['2026-10-28T12:00:00+00:00']

    def approve(action):
        clock[0] = '2026-10-28T12:05:00+00:00'
        return True

    service = services(authorize=approve)
    monkeypatch.setattr(memory_service, '_now', lambda: clock[0])
    saved = service.read_memory(service.save_memory(MemoryDraft('Synthetic fact')))
    assert saved.approved_at == '2026-10-28T12:05:00+00:00'
    assert saved.created_at == saved.updated_at == saved.approved_at
