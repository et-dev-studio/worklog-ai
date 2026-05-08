# Changelog

## Phase 4 — HTTP/OpenAI-compatible inference (PR #6)

### Architecture change (breaking)
Worklog no longer spawns a subprocess per inference call. Agents speak the OpenAI Chat Completions API; a separate inference server (e.g. `llama-server`, ollama, vLLM, OpenAI) holds the model and applies its own chat template. Persistent model load → sub-second responses; backend swaps require zero code change.

### Removed
- `BITNET_CMD` env var and the subprocess code path. **No backwards compatibility — hard cut.**
- `scripts/bitnet-shim.sh` (never shipped; obsoleted by the HTTP layer).

### Added
- `services/inference_service.py` rewritten as a stdlib HTTP client (`urllib.request` + `asyncio.to_thread`). New entry point: `chat(messages, temperature, max_tokens, **extra) -> str`.
- Auto-detect model id via `GET /v1/models` on first call (cached).
- New env vars: `WORKLOG_INFERENCE_URL`, `WORKLOG_INFERENCE_MODEL`, `WORKLOG_INFERENCE_API_KEY`, `WORKLOG_INFERENCE_TIMEOUT`.
- `scripts/start-inference.sh` — wraps `llama-server` with env-driven defaults; intended to run in its own terminal, tmux pane, or as a systemd-user unit.
- `docs/inference-systemd.md` — copy-paste systemd-user unit + ollama / vLLM swap example.
- `tests/_fake_inference_server.py` — tiny stdlib `ThreadingHTTPServer` returning canned `/v1/models` and `/v1/chat/completions`. Used by `tests/test_e2e_workflow.py` instead of spawning a real binary; **fast** and deterministic.

### Updated
- `agents/reflection_agent.py`, `agents/summary_agent.py`: build `[{role:"system", …}, {role:"user", …}]` and call `inference.chat(...)`. Each prompt template is now interpreted as a system prompt.
- `prompts/reflection.txt`, `prompts/grouping.txt`, `prompts/categorize.txt`, `prompts/summarize.txt`: rewritten as explicit system prompts with output format specs and example outputs (the previous one-liners were too underspecified for instruction-tuned models).
- `wl doctor` shows: `Inference URL`, `Inference reachable`, `Inference model` (auto-detected), `Inference latency` (ms). `bitnet` block removed from JSON output (replaced with `inference`).
- `cli/main.py reflect` / `summary` health-check the HTTP server before running.
- `README.md`, `CLAUDE.md`: rewritten Inference section with backend-swap examples (ollama / OpenAI / Anthropic).

### Deferred to v1.1
- Streaming responses (SSE)

## Phase 3 — Terminal-First UX (PR #4)

### Added
- **`wl` / `worklog` entry points** via `pyproject.toml` (`pip install -e .` exposes both). `python -m cli.main` still works.
- **Hidden alias `wl a`** for `wl add`.
- **`add` ergonomics**: `--quiet`/`-q` short flag, `add -` reads from stdin, repeated `--tag`/`-t TAG` attaches tags inline.
- **`wl undo [EVENT_ID] [--reason TEXT]`** writes a `VOIDED` audit event referencing the original (defaults to most recent capture). All read paths skip voided events by default.
- **Event surface**:
  - `wl event list` — `--day` (`today | yesterday | -Nd | this-week | last-week | YYYY-MM-DD`), `--ws`, `--tag`, `--type`, `--limit`.
  - `wl event show <ID>` — event with tags + metadata.
  - `wl event search <QUERY>` — FTS5 search over `events.content` via the new `events_fts` virtual table mirrored by triggers.
  - `wl event tag <ID> <TAG>...` — attach tags.
- **Tag surface**: `wl tag list`, `wl tag show <NAME>`.
- **`wl doctor`** — DB path, Alembic head, BITNET healthcheck, daemon status + last-job age, notify-send availability, prompts dir + missing files, active workstream, completion hint.
- **`wl daemon-dashboard [--watch N]`** — schedules with next-run timestamps, pending reflections, last 10 events.
- **Global `--json` flag** on every read command (`status`, `workstream list`, `event list/show/search`, `tag list/show`, `daemon-status`, `daemon-dashboard`, `doctor`). Output is pure JSON on stdout for `jq` pipelines.
- **Natural-date parser** `services/dateparse_service.py` consumed by `event list --day` and `summary --day`.

### Schema
- `0003_voided_event_type` widens the `events.type` CHECK to include `VOIDED` (`batch_alter_table` recreates the events table — must run before triggers are installed).
- `0004_tags` — `tags`, `event_tags` join table, indexes on both sides.
- `0005_events_fts` — FTS5 virtual table mirroring `events.content`, plus AFTER INSERT/DELETE/UPDATE triggers on `events`. Backfilled from existing rows. **Must run after `0003_voided_event_type`** so the trigger set survives the events-table recreation.

### Tests
- `tests/test_phase3_ux.py` (9 cases) covers `--json` output for status/workstream/list/show/search/tag/doctor/dashboard, FTS search semantics, undo + voided filtering, stdin capture, dateparse helpers.

### Docs
- README rewritten around the `wl` surface, JSON mode, undo, doctor, dashboard, and natural-date filters.
- `CLAUDE.md` architecture map refreshed to cover the new layers (paths_service, tag_service, dateparse_service, output helper, prompts helper) and the migration-order constraint between enum widening and FTS triggers.

## Phase 2 — v1 Checklist Closure & Test Coverage (PR #3)

### Tests
- New `tests/test_e2e_workflow.py`: end-to-end workflow under `WORKLOG_HOME` with a fake `BITNET_CMD` shell stub; exercises capture → summary → markdown export, hard-fail on missing `BITNET_CMD`, non-TTY add, and reflect-requires-TTY paths.
- New `tests/test_daemon_lifecycle.py`: daemon start/stop/status/restart, heartbeat + last-job tracking, stale-PID cleanup, SIGKILL fallback after SIGTERM is ignored, double-start returns `already_running`.
- Expanded `tests/test_cli_daemon.py`: smoke coverage for every CLI command (init-db idempotency, workstream create/list/set-status, status, resume id validation, add explicit and non-TTY paths, connect with no data and non-TTY-without-ids, summary invalid date, notify-test fallback to log).
- Expanded `tests/test_services.py`: archived/completed workstreams excluded from suggestions, reflection priority `fixed` keyword subtracts, unresolved capture excludes already-reflected events, summary/connection audit events emit valid JSON metadata, day-boundary correctness, JSON1 metadata constraint rejects malformed JSON, export `ALL` writes both markdown and json.

### Tooling
- `scripts/pre-commit.sh` runs `pytest -q` inside the BitNet venv (`WORKLOG_VENV` overrides path); install with `ln -sf "$(pwd)/scripts/pre-commit.sh" .git/hooks/pre-commit`.

### Docs
- `docs/v1_release_checklist.md`: every item ticked with file/test references.
- `README.md`: full CLI surface documented; `--yes` semantics, `WORKLOG_HOME`, notification fallback path, hard-fail behavior, pre-commit hook install.
- `CHANGELOG.md` (this file).

## Phase 1 — Correctness Fixes (PR #2)

### Fixed
- **Enum case** (`services/event_service.py`): workstream-suggestion filter switched from `["active", "paused"]` string literals to `[WorkstreamStatus.ACTIVE, WorkstreamStatus.PAUSED]` enum refs so it matches the uppercase Alembic-DDL storage. Suggestions had been silently empty in any non-test database.
- **Title-immutability listener** (`db/models.py`): handles SQLAlchemy `NEVER_SET` / `NO_VALUE` sentinels so `Workstream(...)` construction no longer trips `PermissionError` during `__init__`.
- **Naive vs aware datetimes** (`services/event_service.py`): `_as_utc` helper normalizes DB-loaded timestamps before subtracting `datetime.now(UTC)`, eliminating `TypeError` once the enum-case fix unmasked the scoring path.
- **Day-boundary edge** (`services/summary_service.py`): exclusive `< start_of_next_day` boundary; events at `23:59:59.999999` are no longer dropped.
- **Hand-rolled JSON metadata** (`services/reflection_service.py`): replaced with `json.dumps`.

### Hardened
- **`WORKLOG_HOME` paths_service** (`services/paths_service.py` + dependents): central resolver for data dir, db url, exports, prompts, config, daemon files. Prompts and Alembic anchored via `__file__` so the daemon subprocess survives any `cwd`.
- **TTY guards + `--yes`/`-y` flags** (`cli/prompts.py`, `cli/main.py`): `add`, `connect`, `reflect`, `summary` no longer hang on `questionary.ask()` in pipes/cron/CI.
- **Robust notifications** (`services/notification_service.py`): detects `notify-send` via `shutil.which`, falls back to `data/notifications.log` + stderr; `daemon-start` logs WARN if missing; new `notify-test` command.
- **Inference hard-fail** (`services/inference_service.py`): `InferenceUnavailable` exception replaces placeholder strings. `reflect` and `summary` run `healthcheck()` first, exit code 3 with actionable error when `BITNET_CMD` is unset/unhealthy. No more fake summaries persisted as real data. Inference subprocess now also kills on timeout.
- **`init-db` runs Alembic** (`cli/main.py`): replaced `Base.metadata.create_all` with `alembic upgrade head` to eliminate ORM/migration drift.
- **Daemon supervision** (`services/daemon_service.py`): `stop()` polls SIGTERM exit then escalates to SIGKILL after 5 s; `scheduler-run` installs SIGTERM/SIGINT handlers and shuts APScheduler cleanly; subprocess `cwd=project_root` + env passthrough.
- **Job-health-aware status**: scheduler writes `last_job` per successful run; `status()` flags `degraded` when `last_job` exceeds 2× the largest configured interval, even if process heartbeat is fresh.
- **Differentiated reminders** (`services/scheduler_service.py`): lunch lists top 3 unresolved item titles; evening shows count + summary hint.
- **`resume` validates workstream id** before writing `data/.active_workstream`.
- **PromptService**: friendly error when a template file is missing.

### Schema
- New migration `alembic/versions/0002_add_indexes.py`: `ix_events_timestamp`, `ix_events_workstream_type`, `ix_events_type`, `ix_reflections_event_id`.

### Tests
- `conftest.py` at repo root puts the project on `sys.path` for pytest.
- New regression `test_suggestions_match_real_db_enum_storage` exercises the active/paused filter against real enum DDL storage.
