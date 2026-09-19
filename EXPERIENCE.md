<<<<<<< HEAD
# Athena v0.10 — Experience Intelligence

See [README.md](README.md) for work assistant tools and confirmation.

Completed does not mean correct. Past experience is evidence, not truth.

## Records and compatibility

New final answers have `status: "completed"` and `quality: "unverified"`.
Existing failure statuses are unchanged. Every new record reserves `lesson: null`
and `lesson_status: null`; this release does not generate lessons.

`/good` sets quality to `user_validated`, `/bad` to `user_rejected`, and `/neutral`
to `unverified`. Feedback replaces previous feedback. The compatibility `reward`
is respectively 1, -1, or 0; it is not a reinforcement-learning signal.
Execution status is independent: a positively rated failed run is still a failure.

Existing memory and experience JSON files require no manual migration. Valid
files are not rewritten on read. Retrieval interprets legacy `status: "success"`
as completed, never automatically validated. Missing quality defaults to unverified;
recognized feedback takes precedence over quality metadata. Missing lesson fields
are allowed. Feedback updates only the selected record's feedback, reward and quality.

## Deterministic retrieval

1. Sanitize at most 1,000 task characters, casefold, tokenize with Unicode `\w+`,
   remove fixed stop words, and apply small aliases (`configuration`/`configs` to
   `config`, `files` to `file`). Repeated words do not increase relevance.
2. Generic terms (`read`, `file`, `get`, `find`, `show`, `check`, `make`, `list`,
   `data`) weigh 0.25; other terms weigh 1.
3. Compute weighted Jaccard: sum of shared-token weights divided by sum of
   union-token weights. Require a shared non-generic term and relevance >= 0.45.
   This gate runs before quality bonuses; positive feedback cannot rescue a weak match.
4. Rank eligible records using:

   `score = relevance + quality_bonus + 0.02 * feedback_reward + execution_bonus`

   Quality bonus is +0.08 for user validated, -0.08 for user rejected, otherwise 0.
   Execution bonus is +0.03 for completed, -0.03 for failed. Recognized feedback
   determines quality and the reward used in scoring, so the combined rating
   adjustment is at most +/-0.10; stored numeric reward is not trusted.
5. Break equal scores by newer UTC timestamps. Invalid/missing dates sort oldest;
   remaining ties preserve file order. There is no wall-clock-dependent decay.
6. Suppress duplicate IDs and equivalent normalized token sets in ranked order.
   Return at most three. Source records are not removed or merged.

This is lexical retrieval, not semantic search. Generic weights, aliases and the
threshold are explicit heuristics. Token-set deduplication handles normalization
and word-order variants, not all paraphrases. Retrieval still uses the current
task rather than resolving conversational references. Timestamp is a tie-breaker,
not a reason to prefer an irrelevant recent record.

## Compact model context and feedback

Context contains a bounded task, status, quality, feedback, compatibility reward,
tool outcomes, allowed error categories, steps and optional lesson status. It
omits historical answers, raw results, tool arguments and lesson content.
Evidence is labeled `positive_example`, `unverified_example`, or `caution`.
Failures and rejected runs always receive `caution`, even with contradictory
positive feedback on a failure. No label claims automatic correctness verification.

Feedback confirmation shows a sanitized task description capped at 120 characters.
`/clear` clears both conversation and feedback target. New tasks also reset the
target; failed persistence cannot leave a previous task accidentally selected.
The five-step loop and quiet/debug output behavior are unchanged.

## Local storage safety

Both JSON stores use `json_storage.py`. Valid JSON lists remain compatible.
Writes flush and fsync a unique sibling temporary file before atomic replacement.
The previous valid primary is saved atomically as `<filename>.bak` before each
update. This is one previous generation, not a complete recovery history.

On corruption, original bytes are first preserved as `<filename>.corrupt-<uuid>`.
A valid backup restores the primary; otherwise an empty list initializes it only
after preservation succeeds. Missing primaries can also recover from backups.
Corrupt backups remain untouched during recovery and are quarantined before a
subsequent save replaces them. Failed preservation or backup writes abort the
operation instead of silently discarding those bytes.

Recovery may happen on a read. If the backup is older, newer records may only be
recoverable manually from the preserved corrupt file. Backup validity here means
a parseable JSON list, not validation of every record's application schema.

Only one writer/process at a time is supported. Atomic replacement does not make
read/modify/write transactional across processes; no locking is provided. There
is no guarantee against every filesystem or power-loss scenario.

Backups and quarantines contain private data, are excluded from Git, and are not
automatically deleted. Existing privacy limits remain: experience recording is
automatic, sanitization is best effort, and memory stores supplied text. Normal
CLI output remains quiet; storage failures are available in debug diagnostics.

## Evaluation

`test_retrieval_quality.py` covers relevant aliases, generic and weak overlap,
no matches, verbosity, duplicate selection, quality, recency, failure caution,
legacy records, compact context and lesson placeholders. `test_json_storage.py`
covers backup generations, corruption preservation/recovery and failed commits
for the shared store, with integration checks for memory and experiences.

Run the full suite with the project's Python environment and `python -m pytest`.
These tests verify deterministic behavior, not measured improvements to live LLM
answer quality. No embeddings, model training or lesson generation are included.
=======
# Athena — Experience system

See [README.md](README.md) for work assistant tools and confirmation.

Completed does not mean correct. Past experience is evidence, not truth.

## Records and compatibility

New final answers have `status: "completed"` and `quality: "unverified"`.
Existing failure statuses are unchanged. Every new record reserves `lesson: null`
and `lesson_status: null`; this release does not generate lessons.

`/good` sets quality to `user_validated`, `/bad` to `user_rejected`, and `/neutral`
to `unverified`. Feedback replaces previous feedback. The compatibility `reward`
is respectively 1, -1, or 0; it is not a reinforcement-learning signal.
Execution status is independent: a positively rated failed run is still a failure.

Existing memory and experience JSON files require no manual migration. Valid
files are not rewritten on read. Retrieval interprets legacy `status: "success"`
as completed, never automatically validated. Missing quality defaults to unverified;
recognized feedback takes precedence over quality metadata. Missing lesson fields
are allowed. Feedback updates only the selected record's feedback, reward and quality.

## Deterministic retrieval

1. Sanitize at most 1,000 task characters, casefold, tokenize with Unicode `\w+`,
   remove fixed stop words, and apply small aliases (`configuration`/`configs` to
   `config`, `files` to `file`). Repeated words do not increase relevance.
2. Generic terms (`read`, `file`, `get`, `find`, `show`, `check`, `make`, `list`,
   `data`) weigh 0.25; other terms weigh 1.
3. Compute weighted Jaccard: sum of shared-token weights divided by sum of
   union-token weights. Require a shared non-generic term and relevance >= 0.45.
   This gate runs before quality bonuses; positive feedback cannot rescue a weak match.
4. Rank eligible records using:

   `score = relevance + quality_bonus + 0.02 * feedback_reward + execution_bonus`

   Quality bonus is +0.08 for user validated, -0.08 for user rejected, otherwise 0.
   Execution bonus is +0.03 for completed, -0.03 for failed. Recognized feedback
   determines quality and the reward used in scoring, so the combined rating
   adjustment is at most +/-0.10; stored numeric reward is not trusted.
5. Break equal scores by newer UTC timestamps. Invalid/missing dates sort oldest;
   remaining ties preserve file order. There is no wall-clock-dependent decay.
6. Suppress duplicate IDs and equivalent normalized token sets in ranked order.
   Return at most three. Source records are not removed or merged.

This is lexical retrieval, not semantic search. Generic weights, aliases and the
threshold are explicit heuristics. Token-set deduplication handles normalization
and word-order variants, not all paraphrases. Retrieval still uses the current
task rather than resolving conversational references. Timestamp is a tie-breaker,
not a reason to prefer an irrelevant recent record.

## Compact model context and feedback

Context contains a bounded task, status, quality, feedback, compatibility reward,
tool outcomes, allowed error categories, steps and optional lesson status. It
omits historical answers, raw results, tool arguments and lesson content.
Evidence is labeled `positive_example`, `unverified_example`, or `caution`.
Failures and rejected runs always receive `caution`, even with contradictory
positive feedback on a failure. No label claims automatic correctness verification.

Feedback confirmation shows a sanitized task description capped at 120 characters.
`/clear` clears both conversation and feedback target. New tasks also reset the
target; failed persistence cannot leave a previous task accidentally selected.
The five-step loop and quiet/debug output behavior are unchanged.

## Local storage safety

Packaged stores live in `%LOCALAPPDATA%\Athena\`; source-mode stores remain
beside the source for compatibility. Both are private and must not be uploaded.

Both JSON stores use `json_storage.py`. Valid JSON lists remain compatible.
Writes flush and fsync a unique sibling temporary file before atomic replacement.
The previous valid primary is saved atomically as `<filename>.bak` before each
update. This is one previous generation, not a complete recovery history.

On corruption, original bytes are first preserved as `<filename>.corrupt-<uuid>`.
A valid backup restores the primary; otherwise an empty list initializes it only
after preservation succeeds. Missing primaries can also recover from backups.
Corrupt backups remain untouched during recovery and are quarantined before a
subsequent save replaces them. Failed preservation or backup writes abort the
operation instead of silently discarding those bytes.

Recovery may happen on a read. If the backup is older, newer records may only be
recoverable manually from the preserved corrupt file. Backup validity here means
a parseable JSON list, not validation of every record's application schema.

Only one writer/process at a time is supported. Atomic replacement does not make
read/modify/write transactional across processes; no locking is provided. There
is no guarantee against every filesystem or power-loss scenario.

Backups and quarantines contain private data, are excluded from Git, and are not
automatically deleted. Existing privacy limits remain: experience recording is
automatic, sanitization is best effort, and memory stores supplied text. Normal
CLI output remains quiet; storage failures are available in debug diagnostics.

## Evaluation

`test_retrieval_quality.py` covers relevant aliases, generic and weak overlap,
no matches, verbosity, duplicate selection, quality, recency, failure caution,
legacy records, compact context and lesson placeholders. `test_json_storage.py`
covers backup generations, corruption preservation/recovery and failed commits
for the shared store, with integration checks for memory and experiences.

Run the full suite with the project's Python environment and `python -m pytest`.
These tests verify deterministic behavior, not measured improvements to live LLM
answer quality. No embeddings, model training or lesson generation are included.
>>>>>>> 7a14887 (Release Athena v0.12 with Ollama and native web search)
