# Controlled memory lifecycle and feedback (M2.5)

M2.5 extends the existing optional SQLite MemoryService. JSON remains the default
runtime backend. Lifecycle operations are trusted-host Python API calls; no model
lifecycle tools, automatic promotion, inferred feedback, cleanup worker or schema
upgrade is added. Existing lexical retrieval and experience ranking are reused.

## States and transitions

The schema's existing `superseded` state represents retirement. No record is deleted.

| Current state | Allowed target states through update_memory |
| --- | --- |
| candidate | candidate, active, rejected |
| active | active, disputed, superseded, rejected |
| disputed | disputed, active, superseded, rejected |
| superseded | superseded |
| rejected | rejected |

Same-state updates allow explicit content/metadata corrections. Every update,
including a same-state correction, requires host approval and the expected
revision. Terminal states cannot be reactivated. These restrictions apply to
update_memory as well as lifecycle helpers; they cannot be bypassed by using the
lower-level service update API. Raw SQL remains trusted host infrastructure.

`promote_memory(id, expected_revision=...)` accepts candidates only and moves
them to active. `retire_memory(id, expected_revision=...)` accepts active records
only and moves them to superseded. Both return the new integer revision. Repeating
a completed transition is an invalid transition, not an implicit no-op.

Promotion and retirement preserve the ID, owner, profile, scope, creation time,
content, provenance, epistemic status, confidence, subject and expiry. They update
status, revision, update time and approval metadata through the existing
update_memory transaction. An approved legacy record can still have unknown
provenance; approval does not mean verification or corroboration.

## Explicit review, correction and approval

The host must explicitly initialize/open the service as documented in
[MEMORY_SERVICE.md](MEMORY_SERVICE.md). Use a writable connection for writes,
and close it in finally. The host authenticates/selects profile/principal IDs
and supplies an authorization callback; these values never come from model text.

```python
from dataclasses import replace
from memory_service import MemoryDraft

# service is already explicitly opened writable for the authenticated owner.
identity = service.save_memory(MemoryDraft('Prefer short sensor reports', status='candidate'))
record = service.inspect_memory(identity)
revision = service.promote_memory(identity, expected_revision=record.revision)

record = service.read_memory(identity)
revision = service.update_memory(
    identity, replace(record.draft, content='Prefer concise sensor reports with sources'),
    expected_revision=record.revision,
)
revision = service.retire_memory(identity, expected_revision=revision)
retired = service.inspect_memory(identity)
assert retired.draft.status == 'superseded'
```

The existing update_memory is the correction API; there is no parallel editor.
Prefer `replace(record.draft, content=...)` to retain all other fields. Deliberate
scope changes through update_memory retain the existing explicit host approval
contract; promotion/retirement never change scope. Kind, ID and ownership cannot
be changed through update_memory.

Authorization uses the existing MemoryAction with operation `update`, record ID,
expected revision and complete proposed draft (including the target status).
The callback must return exactly True. Rejected/missing approval, stale revisions,
invalid transitions and storage errors cause no write. Revision is checked before
approval and again under the SQLite write transaction. Failed updates roll back
content, state, revision and approval metadata together. Approval UI is host-owned.
No new approval infrastructure is introduced.

`inspect_memory(id)` is owner-only, respects the bound profile and allowed scopes,
and includes expired/inactive records. It is a read operation, including on a
read-only service. Ordinary read_memory retains its prior visibility rules:
unexpired active records may be visible by scope; inactive records are owner-only.
Nonowners cannot inspect lifecycle details through the new inspection API.

## Migrated candidates

M2.3 migration behavior and source files are unchanged. Imported memories retain
`legacy_unknown` basis, `legacy_json` provenance, unknown epistemic status and
verbatim saved_at timestamps (possibly timezone-naive). They can now be inspected,
then explicitly approved/promoted using the same IDs and imported revisions.

The service accepts the existing schema's legacy_unknown basis without inventing
observation/inference provenance. Unknown legacy basis, like inference, cannot
assert a verified/user-asserted epistemic status. Promotion retains that basis.
A separate explicitly approved correction may change provenance/epistemic metadata
within the existing validation rules. Preserved naive creation timestamps remain
readable afterward; no timezone is invented. New writes retain aware UTC update
and approval timestamps. Creation-time awareness is not used for expiry decisions.

Migration reruns after promotion/correction safely report a conflicting record
rather than undoing lifecycle changes. Original JSON remains untouched and usable.

## Expiry

Expiry is an eligibility condition, not a stored state transition. Aware ISO expiry
values use the existing `_timestamp` UTC normalization. Records are eligible only
while expires_at is strictly greater than the current UTC time; equality means
expired. Tests cover offset-equivalent timestamps and before/at/after boundaries.

Retrieval never extends expiry or changes state/revision. There is no background
cleanup. Expired records remain owner-inspectable but ordinary reads, promotion,
correction and retirement continue to reject them. No renewal-after-expiry API is
added. Retired records remain stored indefinitely. Records are not physically deleted.

## Experience feedback

Reuse the existing service API or SQLiteExperienceAdapter:

```python
# Explicit caller-supplied experience ID and rating, never inferred from an answer.
accepted = service.set_experience_feedback(experience_id, 'positive')
# Equivalent host adapter entry point:
accepted = adapter.set_experience_feedback(experience_id, 'negative')
```

Vocabulary and semantics are unchanged: positive/+1/user_validated,
neutral/0/unverified, negative/-1/user_rejected. Feedback updates only feedback,
reward and quality in the stored experience payload, plus the existing normalized
feedback row. Task text, tool history, answer, errors, lesson and execution status
are preserved. Validation/redaction on read still applies, but a new environment
secret configuration cannot cause feedback to rewrite historical task content.

The owner/profile/principal and visibility checks on MemoryService remain the
feedback authorization boundary. Calling this trusted API is the explicit host
feedback action; there is no new model tool or automatic rating. Invalid ratings,
missing IDs and inaccessible records return False. Closed/read-only lifecycle and
storage failures preserve existing service/adapter error contracts. Episode
metadata and feedback row are changed atomically. Repeated ratings replace the
existing per-episode/actor rating without accumulating reward.

The existing ranking code consumes this metadata on the next retrieval. Successful
execution does not infer positive feedback; a failure remains cautionary even
after positive feedback. No lesson rewriting, autonomous learning or content
correction follows from a rating.

## Runtime and backend compatibility

MemoryRetrieval and AgentRuntime need no lifecycle write hooks: each task queries
the same explicitly supplied MemoryService again. Candidate -> active becomes
retrievable; a correction changes subsequent context; superseded/expired records
are excluded. Read-only retrieval never promotes, retires, corrects or rates records.
In-flight task context is a snapshot and is not retroactively replaced mid-loop.

For SQLite telemetry and explicit feedback on runtime IDs, configure both the
existing `MemoryRetrieval(service)` and `SQLiteExperienceAdapter(service)`. The
runtime exposes the last saved experience ID through its conversation metadata;
a trusted host can pass that exact ID to the existing feedback API. Selecting
SQLite retrieval alone does not switch JSON logging or feedback storage.

The default CLI and JSON runtime remain unchanged. JSON memory has no lifecycle,
authenticated owner or revision model; none is invented here. Existing JSON
feedback semantics remain in experience.set_experience_feedback. Hosts must select
the API corresponding to the actual backend. No implicit migration or activation.

## Verification and CI

```sh
python -m pytest test_memory_lifecycle.py
python -m pytest test_memory_service.py test_memory_migration.py test_memory_retrieval.py test_sqlite_store.py
python -m pytest test_agent_runtime.py test_experience.py test_feedback.py
python -m pytest
python -m pip check
```

Tests use temporary stores, isolated JSON fixtures and offline model providers.
They cover lifecycle states, authorization, revisions, rollback, runtime visibility,
UTC expiry, imported candidates, feedback ranking and preservation, identity
boundaries, read-only behavior and default JSON compatibility.

The existing `.github/workflows/tests.yml` already runs Windows and Ubuntu with
Python 3.12, 3.13 and 3.14. It installs `.[dev]`, runs the complete pytest suite
(including migration, retrieval and lifecycle tests) and pip check. That Ubuntu
matrix job uses no Docker or external services. No duplicate workflow is added;
pre-existing distribution container jobs are unchanged.

Configured CI is not evidence of completed Linux execution. Report local Windows,
local Linux and observed remote CI separately. Python 3.14 runner availability
must be established by the actual CI run, not inferred from workflow YAML.

## Limitations

This remains a local single-writer application boundary, not authentication or a
sandbox against trusted Python/raw SQL. No immutable revision history, lifecycle
audit log, deletion/retention, automatic expiry renewal or new CLI review UI is
added. update_memory is a full draft replacement; copy the prior draft when
correcting only content. Invalid/stale operations raise existing validation,
access, revision or storage exceptions. See the service guide for size/scope
limits and the retrieval guide for bounded lexical selection. M2.6 is outside scope.
