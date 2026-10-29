# Optional SQLite memory service (M2.2)

The CLI and default runtime still use JSON. This service is an explicit,
programmatic option built on SQLite schema v1; it performs no migration and
does not activate a new backend. Python 3.12+ and existing dependencies suffice.

## Explicit lifecycle

```python
from pathlib import Path
from memory_service import MemoryDraft, MemoryService, SQLiteExperienceAdapter
from sqlite_store import SQLiteStore

def open_profile(path, profile_id, principal_id, host_review):
    # Host supplies authenticated IDs and a trusted per-action approval callback.
    service = MemoryService(SQLiteStore(Path(path)), profile_id, principal_id,
                            authorize=host_review)
    service.initialize()  # Explicit schema/profile creation; parent must exist.
    service.open(read_only=False)
    return service

# host_review(action) must return exactly True to authorize the specific draft.
# It must not approve based on model-supplied fields; escape/redact UI previews.
def remember_example(service):
    identity = service.save_memory(MemoryDraft(
        "Prefer short reports", kind="preference", provenance_type="user_statement"))
    return service.read_memory(identity)
```

The caller must use `try/finally: service.close()` around its opened service.
Construction/import does no database I/O, setup or networking. `initialize()`
explicitly creates the bound profile if needed and leaves the connection closed.
`open()` defaults to read-only and cannot create a database or profile. Each
service owns its supplied store lifecycle; do not share an open store object
between services. Separate stores may target the same file with one application
writer and short transactions. Multi-process agent safety is not claimed.

## Public operations

| API | Behavior |
| --- | --- |
| `save_memory(MemoryDraft)` | Returns a new ID after host authorization and commit. |
| `read_memory(id)` | Returns an immutable `MemoryRecord`, or denies unavailable/ineligible access. |
| `find_memories(query, limit=None, kind=None)` | Returns bounded, deterministic active records. |
| `inspect_memory(id)` | Owner-only inspection, including inactive/expired records. |
| `promote_memory(id, expected_revision=...)` | Approved candidate-to-active transition; preserves ID/scope/owner. |
| `retire_memory(id, expected_revision=...)` | Approved active-to-superseded retirement; keeps the record. |
| `update_memory(id, draft, expected_revision=...)` | Owner-only, authorized replacement; increments revision and preserves creation time. |
| `record_experience(record)` | Stores validated/sanitized legacy-format telemetry, private to the bound principal. |
| `read_experience(id)` | Returns a detached permitted experience dictionary. |
| `find_relevant_experiences(task, limit=3)` | Returns bounded matches using the existing scoring algorithm. |
| `set_experience_feedback(id, feedback)` | Owner-only task rating; replaces prior rating without accumulating reward. |

Validation/authorization/revision failures raise `MemoryValidationError`,
`MemoryAccessDenied` or `RevisionConflict`. Storage/lifecycle failures use the
existing store errors. Feedback returns False for invalid ratings or inaccessible
IDs. No method exposes arbitrary SQL to a model.

`MemoryDraft` separates personal, knowledge and preference kinds from observation
and inference bases. It includes provenance, status, scope, optional expiry and
an uncalibrated confidence estimate. Inferences remain epistemically unknown;
an estimate of 1 does not create verification. There is no automatic promotion,
evidence endorsement or personality adaptation. Every memory write, including a
candidate or a scope change, requires exact-True host approval. Drafts contain no
approval, profile or owner override fields. Updates cannot change record kind.

## Access boundaries and limits

The host authenticates and binds profile/principal IDs. The service does not
authenticate operating-system users or provide a Python sandbox. Every data
operation checks the bound profile; private records require matching ownership.
Profile-scoped records are visible within that profile. Shared scope additionally
requires an explicit host grant via `scopes`; it never bypasses the profile.
Defaults are private/profile. Only the owner may update a memory or rate an episode.
Nonactive memories are visible only to their owner and are excluded from search.
Expired records are excluded from ordinary reads, retrieval and updates; owner-only inspect_memory includes them.

Defaults: 5 memory results (configurable 1-20), a 200-record recent candidate window
(configurable 1-1000), 2,000 characters per memory body, and 8,000 characters for
the JSON representation of returned memory context (configurable 4,000-20,000).
Results contain whole records; oversized records are not partially quoted.
Memory relevance uses casefolded lexical overlap, then update-time/ID tie breaks.
Candidate limits deliberately trade historical recall for bounded work.

Episodes are bounded to 20,000 serialized characters with up to five tool events.
Retrieval selects the most recent candidate window, restores insertion order,
then uses the unchanged legacy weighted-Jaccard threshold, feedback/quality
bonuses, timestamp ordering and deduplication. At most three experience matches
enter the unchanged bounded context builder. A completion remains unverified
without feedback; failures remain cautionary evidence even after positive feedback.
Unaware/invalid legacy timestamps remain in the payload and do not invent a new
occurrence time. No raw tool outputs or full conversation archives are added.

## Optional experience adapter

`SQLiteExperienceAdapter(service)` implements the existing `experience_service`
contract and can be supplied to `AgentRuntime(experience_service=adapter)`.
Its record class inherits the existing recorder and redirects only persistence.
The default observer keeps telemetry failures nonfatal. Optional unavailable or
invalid history becomes empty context; an unopened service remains a host
lifecycle error. Feedback and episode metadata are updated in one transaction.

This does not rebind existing memory tools or CLI feedback commands. They retain
their JSON backend. A custom host must call the adapter/service feedback method
for the matching SQLite ID and deliberately configure any registry it exposes.
No default prompt, schema, provider or permission policy changes.

Raw SQLiteStore access is trusted infrastructure and can bypass service policy.
Authentication, encryption, role management, deletion/retention workflows, profile
discovery and production backend activation remain later work.
Keep database files, journals, backups and exports private and outside commits.

Explicit optional JSON import is documented in [MEMORY_MIGRATION.md](MEMORY_MIGRATION.md).

Runtime retrieval integration is documented in [MEMORY_RETRIEVAL.md](MEMORY_RETRIEVAL.md).

Controlled transitions, imported-record review and explicit feedback are documented in [MEMORY_LIFECYCLE.md](MEMORY_LIFECYCLE.md).
