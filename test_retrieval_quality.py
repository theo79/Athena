"""Small deterministic relevance evaluation set; no model or network needed."""
import pytest

import experience as exp
from test_retrieval import record, seed


@pytest.mark.parametrize('query,past,expected', [
    ('read local config file', 'read configuration file', True),
    ('repair automobile', 'weather automobile statistics', False),
    ('read config file', 'read recipe file', False),
    ('please help with weather', 'please help with baking', False),
    ('weather sensors', 'astronomy telescope', False),
    ('weather sensors', 'weather sensors calibration', True),
    ('Read CONFIG files!', 'read configuration file', True),
    ('file', 'file', False),
])
def test_relevance_cases(query, past, expected):
    seed([record('past', past)])
    matches = exp.find_relevant_experiences(query)
    assert bool(matches) is expected


def test_verbose_does_not_beat_focused():
    seed([record('focused', 'weather sensors'),
          record('verbose', 'weather sensors automobile statistics baking astronomy')])
    assert [m['id'] for m in exp.find_relevant_experiences('weather sensors')] == ['focused']


def test_distinctive_overlap_beats_generic_overlap():
    query = exp.task_tokens('read config file')
    assert exp.relevance_score(query, exp.task_tokens('config')) > 0.45
    assert exp.relevance_score(query, exp.task_tokens('read recipe file')) == 0


def test_duplicate_selection_preserves_source():
    seed([dict(record('rejected', 'Read CONFIG file'), feedback='negative'),
          dict(record('old', 'read configuration files'), feedback='positive', timestamp='2025-01-01T00:00:00Z'),
          dict(record('new', 'please read local config file'), feedback='positive', timestamp='2026-01-01T00:00:00Z'),
          record('different', 'read config file settings')])
    before = exp.EXPERIENCES_FILE.read_bytes()
    assert [m['id'] for m in exp.find_relevant_experiences('read config file')] == ['new', 'different']
    assert exp.EXPERIENCES_FILE.read_bytes() == before


def test_quality_ranks_distinct_equally_relevant_tasks():
    seed([dict(record('negative', 'weather sensors south'), feedback='negative'),
          dict(record('neutral', 'weather sensors east'), feedback='neutral'),
          dict(record('positive', 'weather sensors north'), feedback='positive')])
    matches = exp.find_relevant_experiences('weather sensors')
    assert [m['id'] for m in matches] == ['positive', 'neutral', 'negative']
    assert [m['evidence'] for m in matches] == ['positive_example', 'unverified_example', 'caution']


def test_threshold_precedes_feedback_and_completion_bonuses():
    seed([dict(record('weak', 'weather automobile statistics'), feedback='positive', quality='user_validated')])
    assert exp.find_relevant_experiences('repair automobile') == []


def test_newer_breaks_equal_scores():
    seed([dict(record('old', 'weather sensors north'), timestamp='2025-01-01T00:00:00Z'),
          dict(record('new', 'weather sensors south'), timestamp='2026-01-01T00:00:00+00:00')])
    matches = exp.find_relevant_experiences('weather sensors')
    assert matches[0]['score'] == matches[1]['score']
    assert [m['id'] for m in matches] == ['new', 'old']


def test_missing_invalid_dates_keep_stable_file_order():
    seed([dict(record('one', 'weather sensors north'), timestamp=[]),
          dict(record('two', 'weather sensors south'), timestamp='not a date'),
          record('three', 'weather sensors west')])
    assert [m['id'] for m in exp.find_relevant_experiences('weather sensors')] == ['one', 'two', 'three']


@pytest.mark.parametrize('status', ['tool_failure', 'parse_failure', 'model_failure', 'max_steps', 'internal_error'])
def test_failure_remains_caution_even_with_positive_feedback(status):
    seed([dict(record('failed', 'read configuration file', status),
               error_type=status, feedback='positive')])
    match, = exp.find_relevant_experiences('read config file')
    assert match['status'] == status and match['evidence'] == 'caution'
    assert match['quality'] == 'user_validated' and match['error_category'] == status
    context = exp.build_experience_context([match])
    assert 'not approaches to copy' in context
    assert 'RAW OLD ANSWER' not in context and 'RAW INTERNAL ERROR' not in context


def test_legacy_read_does_not_migrate_or_claim_validation():
    original = record('legacy', 'weather sensors')
    seed([original])
    before = exp.EXPERIENCES_FILE.read_bytes()
    assert exp.load_experiences() == [original]
    match, = exp.find_relevant_experiences('weather sensors')
    assert match['status'] == 'completed' and match['quality'] == 'unverified'
    assert match['evidence'] == 'unverified_example'
    assert exp.EXPERIENCES_FILE.read_bytes() == before


def test_legacy_feedback_and_untrusted_reward():
    seed([dict(record('legacy', 'weather sensors'), feedback='negative', reward=999,
               quality='user_validated')])
    match, = exp.find_relevant_experiences('weather sensors')
    assert match['reward'] == -1 and match['quality'] == 'user_rejected'


def test_lesson_is_reserved_not_automatically_exposed():
    seed([dict(record('past', 'weather sensors'), lesson='PRIVATE LESSON BODY',
               lesson_status='candidate', quality=[], feedback={}, error_type=[])])
    match, = exp.find_relevant_experiences('weather sensors')
    context = exp.build_experience_context([match])
    assert 'PRIVATE LESSON BODY' not in context
    assert match['lesson_status'] == 'candidate' and match['quality'] == 'unverified'
    assert '"score"' not in context


def test_context_has_fixed_size_even_for_large_history():
    seed([dict(record(str(i), f'weather sensors region{i}', tools=[
        {'name': 'read_text_file', 'success': False, 'error_type': 'Access denied',
         'args': {'secret': 'private'}, 'result_summary': 'raw output'}] * 100),
        final_answer='private answer' * 1000) for i in range(50)])
    matches = exp.find_relevant_experiences('weather sensors')
    context = exp.build_experience_context(matches)
    assert len(matches) == 3 and len(context) < 5000
    assert 'private answer' not in context and 'raw output' not in context
    assert all(len(m['tools']) == 5 for m in matches)
