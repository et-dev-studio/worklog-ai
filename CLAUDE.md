# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Always activate the project venv before running any Python command:

```bash
source ~/BitNet/BitEnv/bin/activate
```

## Commands

```bash
# Install (editable; exposes `wl` and `worklog` entry points)
pip install -e .[dev]

# Initialize / upgrade the SQLite schema (runs Alembic upgrade head)
wl init-db

# Run tests
pytest
pytest tests/test_services.py::test_summary_emits_summary_generated_audit_event

# Run database migrations directly
alembic upgrade head
alembic revision --autogenerate -m "description"

# Daemon lifecycle
wl daemon-start
wl daemon-status
wl daemon-stop
wl daemon-dashboard --watch 5
```

`wl` and `python -m cli.main` are equivalent. All examples below use `wl`.

## Architecture

**worklog-ai** is a local-first, event-sourced work memory tool for engineers. It captures work events into an immutable log, then uses AI-driven reflection and summarization to produce daily summaries.

### Layers

**`db/`** — SQLAlchemy models and session factory.
- Tables: `workstreams`, `events`, `reflections`, `summaries`, `tags`, `event_tags`, plus the `events_fts` FTS5 virtual table mirrored via triggers.
- All user actions produce append-only `events` rows with a typed `EventType` (CAPTURE, REFLECTION, EVENT_CONNECTED, STATUS_UPDATE, SUMMARY_GENERATED, VOIDED). Mistakes are corrected by writing a new VOIDED event referencing the original — events are never deleted.
- Workstream titles are immutable after creation via a SQLAlchemy `set` listener. The listener understands `NEVER_SET` / `NO_VALUE` sentinels so construction works; bulk-repair scripts must disable it explicitly.

**`services/`** — All business logic. Each file is a single-concern module.
- `paths_service.py`: Resolves `WORKLOG_HOME` (data dir, db, exports, daemon files, notifications log, active-workstream pointer). `project_root()` anchors code-bundled assets (`prompts/`, `alembic/`, `config/`).
- `event_service.py`: Captures events; ranks workstream suggestions via a weighted continuity algorithm. Provides `list_events`, `get_event`, `search` (FTS5) and `void_event`. Voided events are filtered out of list/search by default.
- `reflection_service.py`: Scores unresolved events by priority (short text, no workstream, no recent status update, investigating/fixed keywords).
- `tag_service.py`: `get_or_create`, `attach`, `tags_for_event`, `events_for_tag`, `list_tags` with usage counts.
- `summary_service.py`: Day-bounded events grouped by workstream; emits `summary_generated` audit events with decision/edited metadata. Skips voided events.
- `inference_service.py`: Async subprocess wrapper for `BITNET_CMD`. Raises `InferenceUnavailable` when unset or unhealthy; never returns placeholder text.
- `daemon_service.py`: PID + heartbeat + last-job lifecycle. Status returns `running` | `degraded` | `stopped`. `stop()` polls SIGTERM exit and escalates to SIGKILL after 5 s. `start()` sets `cwd=project_root` so `python -m cli.main` resolves under any `WORKLOG_HOME`.
- `scheduler_service.py`: APScheduler cron jobs at config-driven times. Each job calls `daemon.mark_job_run()` so `daemon.status()` flags `degraded` when jobs silently stop firing.
- `notification_service.py`: Tries `notify-send`; falls back to `data/notifications.log` and stderr when unavailable (e.g., WSL2, headless).
- `dateparse_service.py`: Natural-date parser supporting `today`, `yesterday`, `-Nd`, `this-week`, `last-week`, `YYYY-MM-DD`.

**`agents/`** — Thin wrappers that combine `inference_service` + `prompt_service`. Surface `InferenceUnavailable` to the caller; do not swallow it.
- `reflection_agent.py`: Produces reflection questions for an event.
- `summary_agent.py`: Multi-step pipeline — group → categorize → summarize.

**`cli/`** — Typer app.
- `cli/main.py` registers root + workstream + event + tag sub-apps.
- `cli/prompts.py` provides TTY-aware select/text/confirm helpers; `require_tty()` raises `typer.Exit(2)` in non-interactive shells.
- `cli/output.py` — `emit_json` shared helper for `--json` output.

**`prompts/`** — Plain-text templates loaded by `prompt_service.py`. Resolved via `project_root` (not `WORKLOG_HOME`) so they ship with the code.

**`alembic/`** — Migrations.
- `0001_initial` core schema
- `0002_add_indexes` hot-path indexes (timestamp, workstream_id+type, type, reflections.event_id)
- `0003_voided_event_type` widens `events.type` CHECK to include `VOIDED`
- `0004_tags` `tags` + `event_tags`
- `0005_events_fts` FTS5 virtual table mirroring `events.content` plus triggers

Always generate a migration alongside model changes (especially enum widenings). `init-db` calls `alembic upgrade head`, never `Base.metadata.create_all`.

### Key data flows

1. `wl add "<text>" [--workstream-id ID] [--tag t1 -t t2] [--quiet]`. With no `--workstream-id`: TTY users get a ranked suggestion prompt; non-TTY/`--quiet` falls back to the active workstream pointer.
2. `wl reflect` → top unresolved capture events → `reflection_agent.generate_questions()` → interactive Q&A → inserts reflection rows. Healthcheck runs first; missing `BITNET_CMD` → exit 3.
3. `wl summary [--day today|yesterday|-3d|YYYY-MM-DD] [--export-format markdown|json|terminal|all] [--yes]` → `summary_agent.run()` → human approval/edit → `export_service` writes markdown/JSON to `<WORKLOG_HOME>/exports/YYYY/MM/`.
4. Daemon: `scheduler_service` fires at config times → `notification_service.send` → notify-send or fallback log. Each successful job updates `last_job` so health probes catch silent scheduler failure.
5. `wl undo [EVENT_ID] [--reason ...]` writes a VOIDED event referencing the original. List/search/summary skip voided events by default.

### Environment variables

| Variable | Purpose |
|---|---|
| `WORKLOG_HOME` | Root for runtime data (db, exports, daemon files, logs). Defaults to project root. |
| `BITNET_CMD` | Path to local inference binary. Unset → `reflect`/`summary` exit 3 with hint. |
| `WORKLOG_VENV` | Used by `scripts/pre-commit.sh` to locate the venv (default `~/BitNet/BitEnv`). |
| `WORKLOG_PROMPTS` | Override prompt directory (defaults to `<repo>/prompts`). |
| `WORKLOG_CONFIG` | Override config TOML path. |

### Operational invariants

- TTY-only commands (`reflect`) call `require_tty()` and exit 2 in non-interactive shells. `add`, `connect`, `summary` accept `--yes`/`--quiet` plus stdin (`add -`).
- `init-db` always runs Alembic — no `create_all` shortcuts.
- Daemon `cwd=project_root` and `env=os.environ.copy()` so `WORKLOG_HOME` propagates and `python -m cli.main` resolves regardless of caller cwd.
- Voided events are append-only corrections; never delete an event row.
- Run `wl doctor` before debugging — it surfaces DB path, Alembic head, BITNET healthcheck, daemon status + last-job age, notify-send availability, prompt files, and active workstream.

### Testing

- Tests live in `tests/`. `conftest.py` at the repo root puts the project on `sys.path`.
- `tests/test_e2e_workflow.py` and `tests/test_daemon_lifecycle.py` spawn real subprocesses; do not mock-patch them.
- `scripts/pre-commit.sh` runs `pytest -q` in the venv before each commit. Install via `ln -sf "$(pwd)/scripts/pre-commit.sh" .git/hooks/pre-commit`.
