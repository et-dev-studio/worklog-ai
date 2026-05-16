# Worklog AI V2 — Architecture Blueprint

Status: approved by maintainer (2026-05-16). Supersedes the v1 "Events → Reflections → Summaries" model with a memory-centric, retrieval-first design running on Supabase Cloud Postgres for all deployments.

---

## 1. Vision

Worklog V2 evolves the v1 single-user worklog into a **collaborative engineering memory operating system**. It exists to:

- preserve engineering memory across individuals and small teams
- extract structured cognition from raw operational signals (terminal, git, slack, meetings)
- support natural-language retrieval over the organisation's working history
- enable proactive but human-governed agentic assistance
- maintain long-term continuity across debugging journeys, decisions, and meetings

Prioritisation: **memory quality > raw accumulation; retrieval quality > chatbot UX; explainable intelligence > opaque autonomy; hybrid cognition > pure vector memory.**

---

## 2. Goals and Non-goals

### Goals
- Memory objects as the first-class intelligence primitive.
- Single storage backend: **Supabase Cloud Postgres with `pgvector` extension**. Solo and team deployments use the same backend.
- Hybrid retrieval combining lexical, semantic, temporal, and relational signals.
- Multi-agent system with explicit per-agent contracts and observable execution.
- v1 CLI commands carry forward verbatim where semantics survive; new commands added under `wl memory`, `wl recall`, `wl meeting`, `wl team`.
- Append-only audit guarantees preserved (raw events immutable; memory edits versioned).

### Non-goals
- **No SQLite path in v2.** SQLite is a v1-only concern.
- No bundled audio transcription (Whisper, etc.). Transcripts come from upstream tools.
- No autonomous engineering actions (no auto-commit, auto-PR, auto-resolve).
- No swarm-style emergent agent behaviour. Agents are typed, contracted, dispatched.
- No automatic rewriting of human-defined identifiers (workstream titles, memory titles set by users).
- No migration tooling from v1. New users only. v1 remains available on its own branch/tag.

---

## 3. Continuity from v1

V2 is a deployment-target rewrite, not an in-place upgrade. v1 stays on its tag/branch; v2 begins on a clean Postgres schema. The two ideas that **do** carry forward are the audit-log discipline and the CLI ergonomics.

| v1 anchor | V2 disposition |
|---|---|
| `events` table (`db/models.py`) | Reborn as `raw_events` on Postgres. Same column intent (id, ts, type/kind, content, workstream_id, metadata) plus `owner_id`, `source_uri`. Built fresh on Postgres — no SQLite migration. |
| `events.type` enum | **Widened** in the initial v2 schema to include `GIT_COMMIT`, `SLACK_MESSAGE`, `MEETING_SEGMENT`, `TERMINAL_HISTORY`, `GITHUB_EVENT` alongside the v1 values. Single migration; no incremental widening. |
| `VOIDED` audit pattern | Generalised: memory edits versioned via `memory_object_versions` (no row ever deleted). Raw events keep the v1 soft-delete approach. |
| Workstream title immutability listener (`db/models.py:100`, `NEVER_SET`/`NO_VALUE` sentinels) | Ported to v2 SQLAlchemy models verbatim. Generalised rule: **human-defined identifiers are AI-immutable.** Applies to memory object `title` when `source_kind = authored`. |
| `services/inference_service.py` `chat(messages, temperature, max_tokens, **extra) -> str` and `InferenceUnavailable` exception | Preserved verbatim. The v2 inference router wraps it; agents see the same call signature and the same exit-3 contract on unreachable backend. |
| `wl doctor` health-probe pattern (`cli/main.py:173`) | Extended. Probes: Postgres reachability, pgvector extension present, embedding model loaded, retrieval round-trip latency, RLS policy presence. |
| `services/event_service.py:40` continuity scoring | Carried forward as **one ranking signal** inside the v2 retrieval fusion stage, not the whole ranker. |
| FTS5 via `events_fts` (`0005_events_fts.py`) | Dropped. Replaced by Postgres `tsvector` columns + GIN indexes on `raw_events.content` and `memory_objects.body`. |
| Daemon + scheduler (`services/daemon_service.py`, `services/scheduler_service.py`) | Carried forward. New jobs (memory consolidation, reembedding catch-up, inbox reminder) register alongside lunch/evening reminders. |
| `wl` CLI top-level commands (`add`, `undo`, `reflect`, `summary`, `event …`, `tag …`, `workstream …`, `daemon-*`, `doctor`, `notify-test`) | All preserved; semantics intact. New top-level groups: `wl memory`, `wl recall`, `wl meeting`, `wl team`. |

---

## 4. Domain Model

### 4.1 Raw events (immutable archive)

Append-only operational truth, sourced from any signal channel.

```sql
CREATE TABLE raw_events (
  id              BIGSERIAL PRIMARY KEY,
  ts              TIMESTAMPTZ NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN (
                    'capture','reflection','event_connected','status_update',
                    'summary_generated','voided',
                    'git_commit','slack_message','meeting_segment',
                    'terminal_history','github_event')),
  content         TEXT NOT NULL,
  workstream_id   BIGINT REFERENCES workstreams(id),
  owner_id        UUID NOT NULL REFERENCES users(id),
  source_uri      TEXT,                              -- slack://C123/p456, github://owner/repo/pr/42, ...
  metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
  content_tsv     TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  CONSTRAINT metadata_is_object CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX ix_raw_events_ts            ON raw_events (ts);
CREATE INDEX ix_raw_events_ws_kind       ON raw_events (workstream_id, kind);
CREATE INDEX ix_raw_events_kind          ON raw_events (kind);
CREATE INDEX ix_raw_events_owner_ts      ON raw_events (owner_id, ts);
CREATE INDEX ix_raw_events_content_tsv   ON raw_events USING GIN (content_tsv);
```

Raw events are **not directly retrievable** from the user-facing `wl recall`. They are inputs to extraction agents and the archival audit trail. `wl event search` still queries them lexically (lexical-only surface kept for raw-event grep).

### 4.2 Workstreams

```sql
CREATE TABLE workstreams (
  id              BIGSERIAL PRIMARY KEY,
  title           TEXT NOT NULL UNIQUE,
  summary         TEXT,
  status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','paused','completed','archived')),
  owner_id        UUID NOT NULL REFERENCES users(id),
  team_id         UUID REFERENCES teams(id),
  visibility      TEXT NOT NULL DEFAULT 'private'
                    CHECK (visibility IN ('private','team')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_activity_at TIMESTAMPTZ
);
```

Title remains AI-immutable via the v1 SQLAlchemy listener (ported). `team_id` + `visibility` enable multi-user mode (NULL `team_id` = solo workstream).

### 4.3 Memory objects (independently editable, typed, versioned)

The central intelligence primitive in v2. **Memory objects are first-class and independently editable.** Raw events feed extraction; once a memory object exists it has its own lifecycle and is the unit of retrieval.

```sql
CREATE TABLE memory_objects (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  type            TEXT NOT NULL CHECK (type IN (
                    'decision','investigation','blocker','architecture','meeting',
                    'action_item','workflow','insight','retrospective',
                    'deployment','incident')),
  title           TEXT NOT NULL,
  body            TEXT NOT NULL,                       -- markdown
  status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','confirmed','archived')),

  -- Provenance (IMMUTABLE after insert; enforced by SQLAlchemy listener + trigger)
  source_kind     TEXT NOT NULL CHECK (source_kind IN ('extracted','authored','imported')),
  source_refs     JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{raw_event_id} | {url} | {memory_id}]

  workstream_id   BIGINT REFERENCES workstreams(id),
  owner_id        UUID NOT NULL REFERENCES users(id),
  team_id         UUID REFERENCES teams(id),
  visibility      TEXT NOT NULL DEFAULT 'private'
                    CHECK (visibility IN ('private','team','org')),

  confidence      REAL,                                -- meaningful when source_kind='extracted'

  embedding       VECTOR(768),                         -- pgvector; bge-base-en-v1.5
  embedding_model TEXT NOT NULL,

  -- Audit
  last_edited_by  UUID REFERENCES users(id),           -- NULL = never human-edited (still agent-state)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  body_tsv        TSVECTOR GENERATED ALWAYS AS
                    (setweight(to_tsvector('english', coalesce(title,'')), 'A') ||
                     setweight(to_tsvector('english', coalesce(body,'')),  'B')) STORED
);

CREATE INDEX ix_memory_type        ON memory_objects (type);
CREATE INDEX ix_memory_ws          ON memory_objects (workstream_id);
CREATE INDEX ix_memory_owner       ON memory_objects (owner_id);
CREATE INDEX ix_memory_updated     ON memory_objects (updated_at DESC);
CREATE INDEX ix_memory_body_tsv    ON memory_objects USING GIN (body_tsv);
CREATE INDEX ix_memory_embedding   ON memory_objects USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

Provenance rules (hard-enforced):

- `source_kind` is **immutable** after insert. Attempting to update raises `IntegrityError`. Reason: provenance must remain authoritative even after human edits.
- `last_edited_by` is `NULL` when no human has ever edited the row (memory is still in its agent- or import-authored state). Set to the editing user's `id` on the first human edit and on every subsequent human edit. Agents never set this field.
- A boolean derived flag `is_human_edited := (last_edited_by IS NOT NULL)` is exposed in the API/CLI.

```sql
CREATE TABLE memory_object_versions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  memory_id       UUID NOT NULL REFERENCES memory_objects(id) ON DELETE CASCADE,
  version         INTEGER NOT NULL,
  title           TEXT NOT NULL,
  body            TEXT NOT NULL,
  edited_by       UUID REFERENCES users(id),           -- NULL = system/agent edit (e.g. reembed)
  edit_reason     TEXT,
  edited_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (memory_id, version)
);
CREATE INDEX ix_mov_memory ON memory_object_versions (memory_id, version DESC);
```

**Version retention: keep all versions forever.** No compaction in v2. Storage cost accepted in exchange for full audit history.

### 4.4 Relationships

```sql
CREATE TABLE memory_relationships (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  src_id          UUID NOT NULL REFERENCES memory_objects(id) ON DELETE CASCADE,
  dst_id          UUID NOT NULL REFERENCES memory_objects(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL CHECK (kind IN (
                    'related_to','caused_by','discussed_in','blocked_by',
                    'continuation_of','references','supersedes')),
  status          TEXT NOT NULL DEFAULT 'confirmed'
                    CHECK (status IN ('proposed','confirmed','rejected')),
  confidence      REAL,
  created_by      TEXT NOT NULL CHECK (created_by IN ('human','agent')),
  agent_name      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (src_id, dst_id, kind)
);
CREATE INDEX ix_mr_src_kind ON memory_relationships (src_id, kind);
CREATE INDEX ix_mr_dst_kind ON memory_relationships (dst_id, kind);
```

Agent-proposed edges land as `status='proposed'` and surface in `wl memory inbox --links`. Human accepts/rejects (`wl memory link --accept`/`--reject`).

### 4.5 Identity tables

```sql
CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  pg_role         TEXT NOT NULL UNIQUE,        -- Postgres role name; 1:1 with DB role
  display_name    TEXT NOT NULL,
  email           TEXT UNIQUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE teams (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL UNIQUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE team_members (
  team_id         UUID REFERENCES teams(id) ON DELETE CASCADE,
  user_id         UUID REFERENCES users(id) ON DELETE CASCADE,
  role            TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','admin','member')),
  PRIMARY KEY (team_id, user_id)
);
```

Helper SQL function used by RLS:

```sql
CREATE FUNCTION current_user_id() RETURNS UUID
LANGUAGE sql STABLE AS $$
  SELECT id FROM users WHERE pg_role = current_user
$$;
```

---

## 5. Storage Architecture

**Single backend.** All deployments run against Supabase Cloud Postgres. No storage adapter abstraction — that was a v1-bridging concept; v2 ships Postgres-only.

### 5.1 Supabase Cloud

- **Hosted Postgres** with `pgvector` extension (Supabase enables it via dashboard or `CREATE EXTENSION vector`).
- **Connection.** Each user receives a Postgres role and password (or cert) provisioned via `wl team add` (server-side script that invokes `CREATE ROLE … LOGIN`). CLI reads `WORKLOG_DB_URL` (e.g. `postgresql://user:pwd@db.<project>.supabase.co:5432/postgres`).
- **Migrations.** Single Alembic chain targeting Postgres dialect, starting at `0001_v2_initial`. v1 migrations live on the v1 tag and are not part of the v2 chain.
- **Pooling.** Supabase ships PgBouncer (transaction mode) on port 6543. CLI uses session mode (5432) for migrations + RLS sessions; pooled mode for stateless queries.
- **Vector index.** `ivfflat` with cosine ops, `lists=100` baseline. Tune per corpus size; `wl doctor` reports approximate row count and recommends re-tuning when crossing thresholds (10k, 100k, 1M).
- **Backups.** Supabase Cloud manages PITR (Point-in-Time Recovery) automatically. No additional v2 backup tooling.

### 5.2 Connection lifecycle

- CLI sessions use SQLAlchemy async engine (`postgresql+asyncpg`).
- Each command opens a transaction with `SET LOCAL ROLE <user_role>` to ensure RLS policies apply against the acting user. The bootstrap connection uses a service role; the `SET LOCAL ROLE` switches to the human user's role for the duration of the transaction.
- Long-running daemon connections use a connection pool of size 5; the scheduler acquires per-job connections.

### 5.3 Local development

For local dev, contributors run a Supabase local stack via `supabase start` (docker-backed). Same schema, same RLS policies. No code path differs between local-Supabase and Supabase Cloud.

---

## 6. Memory Lifecycle

### 6.1 Formation

```
raw_event(s) ──► ExtractionAgent.run() ──► MemoryCandidate
                                              │
                                              ▼
                                   human review (TTY/UI/auto-confirm)
                                              │
                                              ▼
                                memory_objects (status='confirmed')
```

`MemoryCandidate` carries `confidence`, proposed `type`, proposed `workstream_id`, and `source_refs`. Above `confidence ≥ 0.8` and user opted into auto-confirm → persists directly with `status='confirmed'`. Below threshold → surfaces in `wl memory inbox` for explicit confirmation.

### 6.2 Editing

Edits write a new `memory_object_versions` row (next `version` number), update the live row in `memory_objects`, set `updated_at`, set `last_edited_by = current_user_id()` (if human edit), and enqueue reembedding when `title` or `body` changed. Reverting = copy-forward from an older version (creates yet another version row; history strictly append-only).

Memory-object edits **do not** mutate `raw_events`.

### 6.3 Linking

- **Manual:** `wl memory link <src> <dst> --kind <kind>` → inserted with `status='confirmed'`, `created_by='human'`.
- **Agent-proposed:** Memory-linker agent inserts with `status='proposed'`, `created_by='agent'`, `agent_name=<name>`. Surfaces in `wl memory inbox --links`. Human `--accept` flips status to `confirmed`; `--reject` flips to `rejected` (kept for audit, excluded from traversal).

### 6.4 Reembedding

Triggered when:
- `title` or `body` changes (synchronous; upsert path includes embedding).
- `embedding_model` config changes (background catch-up job, scheduled by the v1 daemon).

Embedding service (§9.2) is the single chokepoint. Reembed writes both the new vector and the new `embedding_model`. During model migrations, mixed-model rows are tolerated; vector search filters by `embedding_model = current_model` when present.

---

## 7. Retrieval Architecture

### 7.1 Index layout

| Signal | Implementation |
|---|---|
| Lexical (memories) | `body_tsv` GIN on `memory_objects` |
| Lexical (raw events) | `content_tsv` GIN on `raw_events` |
| Semantic | `pgvector ivfflat` on `memory_objects.embedding` |
| Temporal | B-tree on `ts` (raw_events) and `updated_at` (memories) |
| Relational | B-tree on `memory_relationships(src_id, kind)` + `(dst_id, kind)` |

### 7.2 Fusion strategy

**Reciprocal Rank Fusion (RRF)** over four ranked lists: lexical, semantic, temporal, relational. Default `k=60`. Each ranker returns up to `N=50` hits; RRF fuses; final cut at `--limit` (default 10).

Pre-fusion filter (inside each ranker): `visibility` AND `workstream_id` (if set) AND `actor` (if set). Post-fusion filter: `--type`, `--since`.

RRF is the baseline. The fusion interface is the seam where a learned reranker can slot in later.

### 7.3 Query planner contract

```python
class QueryPlanner:
    async def plan(self, q: str, ctx: QueryContext) -> RetrievalPlan: ...
    async def execute(self, plan: RetrievalPlan) -> RetrievalResult: ...
```

`plan()` decomposes a natural-language query into ranker calls plus filters. Example: `"what did we decide about Redis retries last week"` →
- lexical: `"Redis retries"` over memories of type `decision`
- semantic: embed query, vector_search
- temporal: range `last-week` (uses v1 `services/dateparse_service.py`)
- relational: skipped (no anchor id)

`RetrievalPlan` is data — logged, replayable, displayable via `wl recall --explain`.

### 7.4 Ranking signals

v1 workstream-continuity score (`services/event_service.py:40`: keyword overlap × 3, technical-term overlap × 4, recency 14-day decay, prior-attach count, semantic similarity × 0.7, temporal proximity 72-hour window) is reused **as one feature of the relational ranker**. v2 retrieval does not reinvent it.

### 7.5 `wl recall` vs `wl event search` — separation

Two distinct CLI surfaces, **kept separate by design**:

| Surface | Scope | Index | Use case |
|---|---|---|---|
| `wl event search <q>` | Raw events only | tsvector GIN on `raw_events` | "grep my captures" — fast lexical grep over what I wrote down |
| `wl recall <q>` | Memory objects | Hybrid (lexical+semantic+temporal+relational) | "what do we know about X" — synthesised retrieval over extracted memories |

Reason: raw-event lookup and memory recall are different cognitive operations. Mixing them in one command muddles results and obscures provenance.

---

## 8. Multi-Agent System Architecture

Specialised, contracted, dispatched. Not a swarm.

### 8.1 Agent Protocol + lifecycle

```python
class Agent(Protocol):
    name: str
    inputs: type[BaseModel]
    outputs: type[BaseModel]

    async def run(self, ctx: AgentContext, payload: inputs) -> outputs: ...
```

`AgentContext` carries: storage (services layer), inference router, logger, `trace_id`, `actor_id`. Every agent run produces a structured trace event (start, llm_call, storage_write, end) keyed by `trace_id`; observable via `wl agent trace <id>`.

Hard rules:
- Agents read/write through services only. No raw SQL.
- Agents never bypass the inference router. No direct provider SDK calls.
- Outputs must validate against `outputs`. Orchestrator raises on validation failure.
- Agents never directly mutate `memory_objects` rows whose `status='confirmed'`. They write proposals to the inbox.
- Agents never set `last_edited_by` (that field is reserved for human edits).

### 8.2 Orchestrator

Thin asyncio dispatcher:

```python
class Orchestrator:
    async def run(self, agent: Agent, payload, ctx) -> AgentResult: ...
    async def run_parallel(self, jobs: list[Job]) -> list[AgentResult]: ...
    async def run_pipeline(self, stages: list[Agent], seed) -> AgentResult: ...
```

No DAG engine, no message bus. Pipelines are Python composition. Concurrency: `asyncio.gather`. Cancellation: `asyncio.CancelledError`. Retries: explicit per call site.

### 8.3 Agent taxonomy

| Class | Purpose | Examples |
|---|---|---|
| Extraction | raw signal → memory candidate | meeting, slack, git, terminal, standup |
| Memory | memory coherence | linker, deduplicator, continuity-stitcher |
| Retrieval | plan + execute hybrid retrieval | query planner, context ranker |
| Synthesis | produce derived artefacts | standup synthesiser, retrospective generator, onboarding summariser |
| Reflection | identify gaps | ambiguity detector, missing-context detector |
| Continuity | long-horizon tracking | recurring-issue detector, investigation mapper |

v1's `agents/reflection_agent.py` and `agents/summary_agent.py` port directly: each becomes an `Agent` implementation. Existing prompts (`prompts/reflection.txt`, `grouping.txt`, `categorize.txt`, `summarize.txt`) carry forward.

### 8.4 Observability hooks

- Per-run trace records persisted to `agent_traces` table.
- Inference router emits per-call latency + token counts to the same trace.
- `wl agent trace <id>` reconstructs the timeline.
- `wl doctor` includes 24h agent failure rate.

```sql
CREATE TABLE agent_traces (
  trace_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_name      TEXT NOT NULL,
  actor_id        UUID REFERENCES users(id),
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at        TIMESTAMPTZ,
  status          TEXT NOT NULL CHECK (status IN ('running','ok','error')),
  error           TEXT,
  events          JSONB NOT NULL DEFAULT '[]'::jsonb   -- ordered structured events
);
CREATE INDEX ix_traces_agent_started ON agent_traces (agent_name, started_at DESC);
```

---

## 9. Inference Architecture

### 9.1 Router policy

Rules-based, declarative, no auto-routing model:

```toml
# config/inference_routes.toml
[routes.extraction]
provider    = "local"
model       = "llama-3.1-8b-instruct"
max_tokens  = 512
temperature = 0.2

[routes.synthesis]
provider    = "anthropic"
model       = "claude-sonnet-4-6"
max_tokens  = 2048
temperature = 0.3

[routes.embedding]
provider    = "local-st"
model       = "BAAI/bge-base-en-v1.5"
```

Agents call `inference.chat(messages, task="extraction", **overrides)`. Router resolves task → route → provider client. Per-call `route=` override honoured. Providers (`local`, `openai`, `anthropic-via-litellm`, `ollama`, `vllm`) implement a common `ChatBackend` interface.

The router preserves v1's chat surface: `inference.chat(messages, temperature, max_tokens, **extra) -> str`. Existing callers in `agents/` do not change.

### 9.2 Embedding service

Single chokepoint:

```python
class EmbeddingService:
    async def embed(self, texts: list[str], task: str = "memory") -> list[list[float]]: ...
    @property
    def model_id(self) -> str: ...
    @property
    def dim(self) -> int: ...
```

Default backend: `sentence-transformers` with `BAAI/bge-base-en-v1.5` (**dim 768**). Model cached under `WORKLOG_HOME/embeddings/`. Cloud backend (OpenAI/Voyage/Cohere) routes through the same service; switching is a config change. If swapping to a different dim, run `wl memory reembed --all` (background-safe, scheduled).

### 9.3 Backwards-compat

- `services/inference_service.py` becomes the `local` provider implementation behind the router.
- `InferenceUnavailable` is the only error class agents handle. Router translates provider failures into it.
- v1 env vars (`WORKLOG_INFERENCE_URL`/`_MODEL`/`_API_KEY`/`_TIMEOUT`) remain valid; seed the `local` provider entry in the routes table.

---

## 10. Meeting Pipeline

V2 is a meeting **intelligence** system, not a transcript archive. Transcription is upstream.

### 10.1 Input adapters

```
TranscriptAdapter (Protocol)
  parse(source) -> list[Utterance(speaker, ts_start, ts_end, text)]
```

Implementations: `VTTAdapter`, `SRTAdapter`, `TeamsJSONAdapter`, `ZoomJSONAdapter`, `PlaintextAdapter`. CLI: `wl meeting ingest <path> [--workstream-id ID]`.

### 10.2 Segmentation contract

```python
class Segmenter:
    async def segment(self, utterances: list[Utterance]) -> list[Segment]: ...

Segment = { id, speakers: list[str], window: TimeRange, text: str }
```

Default implementation: rolling-window embedding cosine drop detects topic shifts. Embedding via §9.2. Segments typically 1–5 minutes; bounded so extraction prompts stay within model context.

### 10.3 Extraction agents

Per segment:
- `DecisionExtractor` → memory_objects of type `decision`
- `ActionItemExtractor` → type `action_item`
- `BlockerExtractor` → type `blocker`
- `ArchitectureExtractor` → type `architecture`

All produce candidates (status=draft); surface in `wl memory inbox` unless auto-confirm enabled.

### 10.4 Output

Per meeting:
- One `meeting`-type memory_object (index, with `source_refs` to all raw segments).
- Zero or more decision/action_item/blocker/architecture memories, each linked to the meeting via `discussed_in` relationship.
- Optional workstream attachment when `--workstream-id` provided or inferred.

---

## 11. Multi-User Model

### 11.1 Identity

- **Auth method:** Postgres role per user. Each user maps 1:1 to a `pg_role` plus a row in `users` table. No GoTrue/Supabase Auth UI flow in v2 — the CLI is the only interaction surface, so role-based auth suffices.
- **Provisioning:** `wl team add <email> --display-name <name>` (admin only). Server-side action: `CREATE ROLE pg_role_<id> LOGIN PASSWORD '<generated>';` + insert into `users`. The admin receives a connection-string snippet to hand to the new user.
- **CLI auth:** user sets `WORKLOG_DB_URL=postgresql://<role>:<pwd>@db.<project>.supabase.co:5432/postgres`. No additional login step.
- **Session role switching:** the bootstrap connection (used for the connection pool) uses a service role. Each transaction starts with `SET LOCAL ROLE <user_role>` so RLS policies evaluate against the acting human user. `current_user_id()` (§4.5) returns the `users.id` for that role.

### 11.2 Permissions (Postgres RLS)

```sql
-- memory_objects
ALTER TABLE memory_objects ENABLE ROW LEVEL SECURITY;

CREATE POLICY mo_select ON memory_objects FOR SELECT
USING (
     owner_id = current_user_id()
  OR (visibility = 'team' AND team_id IN
        (SELECT team_id FROM team_members WHERE user_id = current_user_id()))
  OR  visibility = 'org'
);

CREATE POLICY mo_insert ON memory_objects FOR INSERT
WITH CHECK (owner_id = current_user_id());

CREATE POLICY mo_modify ON memory_objects FOR UPDATE
USING (owner_id = current_user_id())
WITH CHECK (owner_id = current_user_id());

CREATE POLICY mo_no_delete ON memory_objects FOR DELETE
USING (false);   -- memory rows are never deleted; archive via status='archived'
```

Analogous policies for `raw_events`, `memory_object_versions`, `memory_relationships`, `agent_traces`, `workstreams`. `raw_events`: owner-only by default; promoted to team-visible only when attached to a team-visible workstream (enforced in service layer on insert).

### 11.3 Shared workstreams

A workstream with `team_id` non-null and `visibility='team'` is shared. Memories and raw events attached to it inherit team visibility (explicit on insert, not magical — keeps RLS reads cheap).

---

## 12. UX Surface

### 12.1 CLI

All v1 commands preserved exactly: `wl add`, `wl undo`, `wl reflect`, `wl summary`, `wl resume`, `wl status`, `wl event list|show|search|tag`, `wl tag list|show`, `wl workstream create|list|set-status`, `wl daemon-*`, `wl doctor`, `wl notify-test`. New groups:

- `wl memory create|show|list|edit|link|inbox|reembed` — memory CRUD + agent-proposal review.
- `wl recall <query> [--type T] [--ws ID] [--since SPEC] [--limit N] [--explain] [--json]` — hybrid retrieval.
- `wl meeting ingest <path> [--workstream-id ID]` — transcript ingestion.
- `wl agent trace <trace_id>` — observability.
- `wl team add|list|remove` — multi-user provisioning (admin role required).

Every read command supports `--json` (v1 convention).

### 12.2 TUI scope

Focused workflows benefiting from in-place navigation, built on `textual`:
- Memory inbox triage.
- Recall results with side-pane preview.
- Reflection sessions.
- Meeting segment review.

### 12.3 Web UI scope

The Web UI is **not** a CLI in a browser. It specialises in views the terminal cannot do well:
- Memory relationship graph (interactive).
- Timeline of memories per workstream.
- Meeting transcript ↔ extracted memory side-by-side review.
- Cross-team memory exploration.

Stack: FastAPI backend (reuses services) + Next.js frontend. Realtime updates via Supabase Realtime (`memory_objects` row-change subscriptions). Auth: same Postgres-role connection string; FastAPI requires a `WORKLOG_DB_URL` per user session (CLI-flow oriented, not browser SSO).

---

## 13. Integration Architecture

All integrations are extraction-oriented. They produce `raw_events`; extraction agents turn those into memory objects.

| Integration | Capability | Mechanism |
|---|---|---|
| Git | commit ingestion, branch tracking | local `git log` parser; `wl integration git pull` |
| GitHub | PR + issue extraction | REST API; `WORKLOG_GITHUB_TOKEN` |
| Jira | workstream sync, ticket continuity | REST API; bidirectional workstream-id mapping |
| Slack | discussion/decision extraction | Slack export ingestion in v2.0; webhook in v2.1 |
| Teams | transcript ingestion | manual transcript export |
| Zoom | transcript ingestion | manual transcript export |
| Terminal history | debugging reconstruction | reads `~/.bash_history`/`~/.zsh_history` (opt-in) |

Each integration is a separate module under `integrations/` implementing a common `Ingestor` Protocol. No always-on listeners in v2 except via the v1 daemon's scheduler.

---

## 14. Observability Stack

- Structured logs to `WORKLOG_HOME/logs/` (rotating).
- Per-agent traces in `agent_traces` table; queried via `wl agent trace`.
- Inference router metrics: per-route latency p50/p95, token counts, error rate.
- Optional OpenTelemetry export (env-gated). No mandatory third-party service.
- `wl doctor` aggregates high-signal probes (Postgres reachable, pgvector loaded, embedding model loaded, last agent failure, daemon status, RLS policies present).

---

## 15. Phasing

Four sub-phases, each independently shippable.

### Phase 5 — Postgres + Supabase foundation

Deliverables:
- Provision Supabase Cloud project; enable `pgvector`.
- New `alembic/` chain rooted at `0001_v2_initial`: tables `users`, `teams`, `team_members`, `workstreams`, `raw_events`, `agent_traces` (memory tables land in Phase 6).
- `services/storage/postgres.py` — SQLAlchemy 2.x async engine, connection pool, `SET LOCAL ROLE` session helper.
- Rewrite v1 services (`event_service`, `summary_service`, `reflection_service`, `workstream_service`, `tag_service`) against Postgres. FTS5 replaced with `content_tsv` GIN.
- v1 CLI commands re-wired: `wl add`, `wl undo`, `wl reflect`, `wl summary`, `wl event list|show|search|tag`, `wl workstream …`, `wl status`, `wl resume`, `wl doctor`.
- Multi-user provisioning: `wl team add|list|remove`.
- Env vars: `WORKLOG_DB_URL` (replaces v1's SQLite path), `WORKLOG_DB_SERVICE_ROLE_KEY`.
- Deps added to `pyproject.toml`: `psycopg[binary]`, `asyncpg`, `pgvector`, `pydantic>=2`, `sqlalchemy>=2`.

Exit criteria: v1 CLI behaviours (capture, summary, reflect, event search, workstream CRUD) pass an equivalent test suite against Postgres. RLS policies enforce visibility in a 2-user fixture.

### Phase 6 — Memory model

Deliverables:
- Alembic `0002_v2_memory_tables`: `memory_objects`, `memory_object_versions`, `memory_relationships`, including the `source_kind` immutability trigger.
- `services/memory_service.py` — CRUD, versioning, `last_edited_by` set on human edit.
- `services/extraction_service.py` — generic candidate-formation pipeline.
- `services/embedding_service.py` — sentence-transformers + `bge-base-en-v1.5` (dim 768).
- CLI: `wl memory create|list|show|edit|link|inbox|reembed`, `wl recall <query>` (lexical+semantic only at this phase).
- Embedding cache under `WORKLOG_HOME/embeddings/`.
- Dep added: `sentence-transformers`.

Exit criteria: capture → extract → confirm → recall round-trip works end-to-end. Source-kind immutability trigger rejects update attempts. Eval: 20 curated raw events produce ≥ 15 expected memory candidates.

### Phase 7 — Retrieval

Deliverables:
- `services/retrieval/` with lexical, semantic, temporal, relational rankers + RRF fusion.
- `QueryPlanner` implementation.
- `wl recall` gains `--explain`, `--type`, `--since`, `--ws`.
- pgvector ivfflat tuning (lists per corpus-size tier); `wl doctor` recommends retuning.
- Eval harness format: YAML gold-set under `tests/eval/`, each entry `{query, expected_memory_ids, k=10}`. Runner computes `recall@10` and surfaces in CI.

Exit criteria: `recall@10 ≥ 0.7` on a 50-query synthetic eval set vs BM25-only baseline of `recall@10 ≈ 0.5`. `wl recall --explain` shows per-ranker contribution.

### Phase 8 — MAS + integrations + meetings

Deliverables:
- `services/agents/` with `Agent` Protocol + orchestrator.
- v1 agents (`reflection_agent`, `summary_agent`) ported to the new Protocol with prompts unchanged.
- New agents: per-source extraction (meeting, slack, git, terminal), memory linker, retrieval planner.
- Integrations: git log ingestor, GitHub PR ingestor, Slack export ingestor, transcript ingestors (VTT/SRT/Teams JSON/Zoom JSON/plaintext).
- `wl meeting ingest` end-to-end.
- `wl agent trace` observability surface.

Exit criteria: ingest a real meeting transcript end-to-end; produce a `meeting` memory plus ≥ 1 `decision` or `action_item`, all retrievable via `wl recall`.

---

## 16. Branching and Release Strategy

- **Tag the current v1 HEAD** as `v1-final` and create a long-lived branch `v1` from the same commit (lets v1 receive bugfix backports without polluting main).
- **`main` always carries the latest version.** v2 development continues on `main`. When v3 begins, the v2 HEAD will be tagged `v2-final` + branched to `v2`, and `main` advances to v3.
- **Feature branches per phase:** `phase-5/postgres-foundation`, `phase-6/memory-model`, `phase-7/retrieval`, `phase-8/mas-integrations`. Each merges into `main` via PR after pre-commit hook (pytest) and review.
- **No force-push to `main` or any `vN` branch.** Both are protected.
- **CHANGELOG.md** continues the existing phase-numbered convention from v1 (next entry: "Phase 5 — Postgres + Supabase foundation").

---

## 17. Risks and Resolved Decisions

### Risks
- **Embedding model churn.** Switching default embedding model invalidates stored vectors. Mitigation: `embedding_model` per row + background `wl memory reembed`. Cost: storage doubling during transition.
- **Supabase vendor coupling.** v2 is bound to Supabase Cloud. Mitigation: schema is portable plain Postgres + pgvector; in principle migratable to any Postgres host if needed.
- **Agent prompt drift across model versions.** Same prompt against Sonnet 4.6 vs 4.7 may extract different memories. Mitigation: store `model_id` in each `agent_traces` event.
- **Embedding dim 768 storage cost.** ~3 KB per memory_object embedding (vs 1.5 KB at 384). Acceptable at expected corpus sizes.
- **No backfill from v1.** v1 captures stay on v1. v2 users start fresh. Mitigation: documented as a hard non-goal; v1 remains usable on its tag for read-only access to history.

### Resolved decisions (no longer open)
- Embedding dim: **768** (`bge-base-en-v1.5`).
- `wl recall` vs `wl event search`: **separate commands**, distinct semantics (§7.5).
- Version retention: **keep all versions forever**, no compaction.
- `events.type` widening: **landed in Phase 5 initial schema**, no incremental widening.
- `source_kind` mutability: **immutable after insert**. `last_edited_by` column added; NULL = never human-edited, set to user-id on human edit, never set by agents.
- v1 backfill: **none**. New users only.
- Storage backend: **Supabase Cloud Postgres + pgvector, single backend**. No SQLite in v2.
- Auth: **Postgres role per user**, no GoTrue/SSO.
- Branching: **main = latest version; `v1` branch + `v1-final` tag preserve v1.**

---

## 18. V3 Readiness

V2 lays groundwork for, but does not deliver:
- Autonomous engineering copilots that take operational actions.
- Predictive engineering assistance (proactive issue surfacing without explicit recall).
- Cross-org intelligence graphs.
- Adaptive memory systems that self-prune.
- Distributed agent orchestration.

V3's enabling primitives — typed memory, observable agents, hybrid retrieval, multi-user RLS — are all in v2.

---

## Final Architectural Identity

Worklog V2: **Collaborative Engineering Memory Operating System.** Memory-centric. Retrieval-first. Semantically linked. Supabase-hosted. Explainable. Human-governed.
