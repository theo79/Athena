# Optional legacy JSON migration (M2.3)

Migration explicitly copies Athena JSON lists into the optional SQLite schema v1
using MemoryService and SQLiteStore. JSON remains the default runtime backend.
Import, startup and normal CLI operation never trigger migration or switch storage.
Use Python 3.12+ with the project's existing dependencies. No new dependencies.

## Inputs and commands

Stop JSON writers and SQLite writers while taking source snapshots and importing.
Supply one or more memory and/or experience files, an explicit destination path,
and trusted host-selected profile and principal IDs. The destination parent must
exist. Paths may be relative or absolute; quote paths containing spaces.
No default files, backup recovery or source rewriting occurs. A .bak may be
provided explicitly as a source if desired.

From the project directory, validate first:

```sh
python -m memory_migration --memory memory.json --experience experiences.json --database athena.sqlite3 --profile athena --principal local-user --dry-run
```

Then explicitly execute the same input batch:

```sh
python -m memory_migration --memory memory.json --experience experiences.json --database athena.sqlite3 --profile athena --principal local-user --execute
```

Repeat `--memory` or `--experience` for multiple files. One of `--dry-run` or
`--execute` is required. The existing interactive agent CLI is unchanged.
Exit codes: 0 validated/committed, 1 blocked records, 2 input/storage failure.
The JSON summary never prints record contents. Source numbers and record indices
in issues identify rejected inputs without echoing private data.

```python
from memory_migration import migrate_json
result = migrate_json(
    'athena.sqlite3', memory_files=['memory.json'],
    experience_files=['experiences.json'], profile_id='athena',
    principal_id='local-user', dry_run=True,
)
print(result.valid, result.skipped, result.conflicting, result.invalid,
      result.unsupported, result.imported, result.committed, result.issues)
```

The Python API defaults to dry run. Source I/O errors and SQLite errors propagate;
invalid JSON/records return a blocked summary. `total` counts list entries;
`invalid` also counts unreadable-format JSON documents. `valid` counts new,
validated records; `skipped` counts exact matches; `conflicting` counts colliding
IDs with different imported state; `unsupported` counts unrepresentable records.
`imported` is zero unless the batch commits. No invalid/unsupported/conflicting
batch is partially imported. Empty lists are valid.

## Preservation and limitations

Legacy memories contain `text` and `saved_at`. Both are preserved exactly as
content and creation/update timestamps, including naive local timestamps: no
unknown timezone is invented. Missing IDs become stable content/timestamp hashes
with an occurrence ordinal, preserving identical entries within one file.
Optional explicit `id` and positive integer `revision` are retained. Actual
legacy JSON has neither. Missing revision becomes 1. Unknown fields are rejected.
Empty/oversized text, invalid timestamps and invalid IDs/revisions are rejected.

Imported memories are private personal candidates with `legacy_unknown` basis,
unknown epistemic status and `legacy_json` provenance. They have no invented
approval and are excluded from active search. A trusted host can review them
using the existing MemoryService API. Migration is an explicit trusted host
operation, not a model-facing memory approval interface.

Experiences preserve IDs, status (including legacy `success`), timestamps,
provider/model, steps, parse failures, tool metadata, final answers, errors,
budget flags, feedback, reward, quality and lessons in the M2.2 compatibility
payload. M2.2 supplies defaults for missing fields. Feedback remains in the
payload read by the existing service and ranking code; migration does not invent
feedback actor/timestamp history in the normalized feedback table.

M2.2 sanitizes text, tool arguments and derived feedback metadata. If that would
change ANY supplied field (including nested tool fields), migration reports an
unsupported record and blocks the batch. Oversized/invalid records are rejected,
never truncated. Review the untouched source and prepare a separate compatible
copy if needed; no lossless migration is claimed for unsupported records.

Aware occurrence timestamps are normalized to UTC in the indexed episode field;
original timestamps remain verbatim in the payload. Naive, invalid or missing
experience dates retain their legacy representation, with no indexed occurrence
time. Episode `recorded_at` uses a fixed epoch sentinel because legacy JSON has
no separate recording time. No historic approvals, ownership, evidence links,
revision history or feedback events are fabricated.

## Integrity and repeatability

Sources are opened only for reading, using strict JSON parsing (duplicate object
keys and nonfinite JSON numbers are rejected). Recovery loaders are not used.
Dry run never creates the database, schema, profile, backup or import marker.
Existing databases are validated and read through SQLiteStore in read-only mode.
The destination cannot alias a source file.

Execution rechecks collisions under `BEGIN IMMEDIATE` and commits all identities
and imported records in one SQLiteStore transaction. An insertion failure rolls
back all batch data. Initial schema creation is a separate explicit foundation
operation: a later failure may leave an empty initialized database, never a
partially imported batch. Existing databases are never replaced. Unknown or
incomplete schemas and integrity failures are rejected by SQLiteStore.

Exact matching IDs/state are skipped; different state or profile/owner conflicts
block the whole batch. Repeating unchanged inputs with the same identity binding
does not duplicate records. Changed memories without IDs get new hashes; this
is snapshot import, not ongoing synchronization. Identical entries across files
are deduplicated by hash/ordinal. Preserve file boundaries when repeating imports.
Records subsequently edited or rated in SQLite may conflict on reimport; no
updates are overwritten. Existing records must match the complete import mapping,
including provenance, ownership and recording sentinel; an equivalent-looking
record created through a different workflow may safely conflict.

Use one local writer. Do not edit inputs during the operation. SQLite validation
inherits schema-v1 checks; this is not protection against malicious database
schema tampering or arbitrary Python/SQL access. No runtime backend switch,
background migration, cloud service or schema upgrade is included.

## Recovery and verification

Keep original JSON snapshots and, for an existing destination, take a normal
SQLite backup while writers are stopped. On failure, inspect the summary or
storage error, resolve the issue using a separate source copy or destination,
and rerun dry run followed by execute. The originals remain usable by the JSON
runtime throughout. Never delete the original JSON as a migration step.
If initialization succeeded but import failed, retry against the empty schema
or choose a fresh explicit destination. Do not replace an existing user database.

Tests use temporary JSON and SQLite files. Run:

```sh
python -m pytest test_memory_migration.py test_memory_service.py test_sqlite_store.py
python -m pytest
python -m pip check
```

Local Windows test results do not imply Linux CI passed. Run the existing
Windows/Linux CI matrix independently before release. Migration is optional;
M2.4 and backend activation are outside this milestone.

For explicit candidate promotion, retirement and feedback, see [MEMORY_LIFECYCLE.md](MEMORY_LIFECYCLE.md).
