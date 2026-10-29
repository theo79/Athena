# Memory retrieval integration (M2.4)

Athena retrieves persisted memories and relevant past experiences before the first
model call of each task. `MemoryRetrieval` is a small selection/context layer on
top of the existing JSON contracts and optional `MemoryService`. It adds no new
persistence backend, schema, migration, embedding or dependency.

## Runtime integration and backend selection

`AgentRuntime(..., retrieval_service=None)` and
`agent_runtime.run_agent(..., retrieval_service=None)` use JSON by default.
The legacy `agent.run_agent` and interactive CLI follow this same path. JSON
memory comes from `tools.MEMORY_FILE`; experience retrieval uses the existing
runtime `experience_service` hooks. No database discovery or environment flag
selects SQLite. Importing/constructing retrieval performs no storage I/O.

Normal usage remains unchanged:

```python
from agent_runtime import AgentRuntime
runtime = AgentRuntime(provider=my_provider)
answer = runtime.run_agent('Compare the weather sensors configuration')
```

Explicitly configure JSON retrieval limits or a host-selected local file:

```python
from memory_retrieval import MemoryRetrieval
retrieval = MemoryRetrieval(memory_file='memory.json', memory_limit=3,
                           experience_limit=2, context_chars=6000)
runtime = AgentRuntime(provider=my_provider, retrieval_service=retrieval)
```

Select SQLite by supplying the already bound and explicitly opened M2.2 service:

```python
from agent_runtime import AgentRuntime
from memory_retrieval import MemoryRetrieval
from memory_service import MemoryService
from sqlite_store import SQLiteStore

service = MemoryService(SQLiteStore('athena.sqlite3'), 'athena', 'local-user')
# Open an existing initialized database/profile; never initialize or migrate here.
service.open(read_only=True)
try:
    runtime = AgentRuntime(provider=my_provider,
                           retrieval_service=MemoryRetrieval(service))
    answer = runtime.run_agent('Compare the weather sensors configuration')
finally:
    service.close()
```

The host authenticates/selects profile and principal IDs; model text cannot
select them. Retrieval neither owns nor opens/closes the supplied service.
If opening fails, the host may report the error or explicitly continue using a
closed bound service: retrieval then supplies empty evidence and availability
flags, with no database creation or JSON fallback. A closed service can later
be opened by the host and used again.

For explicit SQLite retrieval, the built-in `search_memory` tool is also bound
to this retrieval service. This prevents a subsequent model search from reading
global JSON across identity boundaries. The global tool registry is never
mutated. Explicit custom host search handlers are retained; their isolation is
the host's responsibility. Tool schema and permission checks still apply.

Retrieval selection does not switch persistence: save_memory, CLI feedback and
task logging retain their existing JSON behavior unless the host separately
configures them. For SQLite task logging/feedback, explicitly pass the existing
`SQLiteExperienceAdapter(service)` as `experience_service` and open the service
writable. An adapter supplied alone selects experience logging/retrieval only;
use `MemoryRetrieval(service)` to select SQLite memory retrieval too. A host
building a multi-user application must also scope its write tools and logging;
the default CLI remains a local single-user JSON application.

## API and context selection

`MemoryRetrieval.retrieve(task)` returns a detached `RetrievalResult` containing
`memories`, `experiences`, combined `context`, `memory_available` and
`experience_available`. Results are tuples of dictionaries. Expected storage or
record-validation failures mark the affected component unavailable and leave it
empty; programming errors propagate. A legacy injected experience adapter keeps
its existing closed-service lifecycle error; the new optional behavior applies
when SQLite retrieval is explicitly selected through `MemoryRetrieval(service)`.
Empty/missing JSON memory is normal empty
storage. Empty task strings return empty context.

The runtime places combined context in a separate **user-role** message after
the system prompt and before session history/current task. It is retrieved once
per task, reused through that task's tool loop, and never appended to conversation
history or copied into the experience task record. This gives the model evidence
when choosing a response or tool. It does not guarantee model factual accuracy.

Both system instructions and the memory context label retrieved text as untrusted
contextual evidence. JSON encoding keeps record text separate from message roles;
text that claims to be a system message does not become one. Stored content
cannot grant approval, authorize actions or override tool permissions. Existing
confirmation callbacks and MemoryService approval checks remain authoritative.
Tests exercise hostile text with a fake model selecting an unapproved write;
the runtime denies execution. Prompt wording alone is not a model sandbox.

## Ranking and limits

- JSON memories reuse `search_memory` matching: casefolded substring or word
  overlap, in stable file order. No new score is invented. The most recent 200
  file entries are considered by default (`candidate_limit`, range 1–1000).
- SQLite memories use `MemoryService.find_memories`: substring/lexical overlap,
  then update time descending and ID ascending for ties. Only active, visible,
  unexpired records are candidates. The bound service retains its own candidate
  window and context cap; retrieval cannot widen those limits.
- Experiences reuse `experience.rank_experiences`, including weighted Jaccard
  threshold 0.45, feedback/quality adjustments, completion/failure bonus, timestamp
  ordering, stable ties and ID/task deduplication. SQLite uses the existing
  service's bounded recent episode window; JSON uses legacy retrieval unchanged.
- `memory_limit` defaults to 5 (range 0–20), `experience_limit` to 3 (range 0–3).
  Zero disables that read. The service's configured memory maximum can impose a
  stricter cap. JSON experience hooks retain their legacy call signatures; the
  returned ranked list is capped afterward.
- `context_chars` defaults to 8000 (range 512–20000). It bounds the complete
  retrieval message, including headers, JSON escaping and separators, in Unicode
  characters, not model tokens. System prompt, conversation history, current task
  and tool outputs have their existing separate limits.
- Whole memories are packed first, then whole experience summaries. Oversized
  records are skipped so smaller later records may fit. Memories can exhaust the
  budget before experiences. No partial serialized records enter the context.
- SQLite memory queries are capped at 500 characters and experience queries at
  1000 using existing safe text handling. JSON memory queries use that 1000
  character bound. Stored memory content is capped at 2000; telemetry summaries
  retain their established privacy/size bounds. Environment secrets are redacted.

Identical queries and unchanged eligible storage return stable ordering. Clock-
based expiry can change eligibility; feedback and writes can change ranking.

## Identity isolation, malformed data and failures

SQLite retrieval delegates all reads to the same MemoryService. Its bound profile,
principal, allowed scopes, private ownership and expiry rules apply to memories
and experiences. Profile-visible records can intentionally be shared within that
profile. Cross-profile records and another principal's private records are excluded.
No failure causes fallback to global JSON. The built-in search bridge follows the
same policy, including when optional storage is closed/unavailable.

Legacy JSON has no authenticated profile/owner fields. It is a single local user's
store; it is not a multi-user authorization backend. Hosts must not share that file
across unrelated users. Unknown memory fields, malformed/oversized records and
invalid text types are skipped. Corrupt JSON or read errors produce no memory
context and do not rewrite/recover/create memory files. Explicit search/save tools
retain their existing JSON recovery behavior on the default backend.

Malformed SQLite records can invalidate their whole retrieval component, while
the other component still contributes evidence. Unsupported schemas are rejected
by SQLiteStore when the host opens the service. An unavailable optional service
supplies empty evidence without stopping ordinary model/tool execution.

M2.3 imported memories remain unapproved candidates and are excluded from active
retrieval. Migration behavior is unchanged. Legacy content is not promoted or
rewritten by retrieval. Historical provenance is not proof of correctness.

## Verification and limitations

```sh
python -m pytest test_memory_retrieval.py test_memory_service.py test_memory_migration.py
python -m pytest test_agent_runtime.py test_retrieval.py test_experience.py test_feedback.py
python -m pytest
python -m pip check
```

All tests use temporary paths; shared test fixtures isolate memory, experience and
settings locations. Path-contract tests explicitly retain their real path
constants but select temporary paths for every storage operation. Tests cover
relevance, deterministic ordering, feedback,
limits, read-only operation, identity isolation, optional failures, normal CLI and
headless flow, untrusted text and unchanged action approval boundaries.

This milestone adds no CLI backend flag, automatic migration, database creation,
backend switch, memory rewriting, consolidation, embeddings or M2.5 features.
Retrieval is lexical and window-bounded, so relevant older data can be missed.
No new full-snapshot guarantee is added across the two component reads; use the
existing single-writer host contract. JSON experience recovery remains legacy
behavior. Live-model resistance to injection and factual answer quality are not
established by offline tests. Run Windows and Linux CI independently; Windows
results do not establish Linux compatibility.

For explicit candidate promotion, retirement and feedback, see [MEMORY_LIFECYCLE.md](MEMORY_LIFECYCLE.md).
